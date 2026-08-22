from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agent.orchestrator import KnowledgeAgent
from app.agent.answer_pipeline import _StreamingTextGuard
from app.agent.answer_validation import NaturalAnswerValidationError
from app.agent.streaming import AgentStreamEvent
from app.agent.types import AgentRequest, Citation
from app.channels.types import TenantContext
from app.config import Settings


def _request(question: str = "查资料") -> AgentRequest:
    return AgentRequest(
        question=question,
        tenant=TenantContext(1, 1, "telegram", "bot", "user"),
        thread_db_id=1,
        thread_public_id="thread",
        message_id="message",
        request_id="stream-test",
    )


class _Services:
    def __init__(self, citations: list[Citation]):
        self.citations = citations

    def search_segments(self, _query, *, limit=6, item_id=None):
        return self.citations


def _citation(segment_id: int = 11) -> Citation:
    return Citation(
        item_id=7,
        segment_id=segment_id,
        title="来源视频",
        excerpt="字幕依据",
        url="https://example.test/video?t=11",
        start_sec=11,
    )


def _primary_model():
    def model(messages, _info):
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            return ModelResponse(
                parts=[ToolCallPart("search_segments", json.dumps({"query": "主题"}))]
            )
        return ModelResponse(parts=[TextPart("检索完成")])

    return FunctionModel(model)


def _plan_model(segment_id: int = 11):
    def model(_messages, _info):
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "kind": "grounded",
                            "sections": [
                                {
                                    "section_id": "model-owned-id",
                                    "task": "概括有证据的部分",
                                    "status": "grounded",
                                    "citation_ids": [segment_id],
                                },
                                {
                                    "section_id": "unsupported-part",
                                    "task": "确认没有证据的部分",
                                    "status": "unsupported",
                                },
                            ],
                        }
                    )
                )
            ]
        )

    return FunctionModel(model)


def _answer_model(segment_id: int = 11):
    def model(_messages, _info):
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "kind": "grounded",
                            "sections": [
                                {
                                    "status": "grounded",
                                    "text": "兼容答案",
                                    "citation_ids": [segment_id],
                                }
                            ],
                        }
                    )
                )
            ]
        )

    return FunctionModel(model)


def _dual_answer_model(segment_id: int = 11):
    def model(_messages, info):
        if "分段规划器" in info.instructions:
            output = {
                "kind": "grounded",
                "sections": [
                    {
                        "section_id": "model-owned-id",
                        "task": "概括有证据的部分",
                        "status": "grounded",
                        "citation_ids": [segment_id],
                    }
                ],
            }
        else:
            output = {
                "kind": "grounded",
                "sections": [
                    {
                        "status": "grounded",
                        "text": "兼容答案",
                        "citation_ids": [segment_id],
                    }
                ],
            }
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    return FunctionModel(model)


def _stream_model(*, unsafe: bool = False):
    async def stream(_messages, _info):
        yield "第一"
        await asyncio.sleep(0)
        if unsafe:
            yield " https://private.example/should-not-leak"
        else:
            yield "段"

    return FunctionModel(stream_function=stream)


def _unavailable_stream_model():
    async def stream(_messages, _info):
        if False:
            yield ""
        raise NotImplementedError("streaming endpoint is unavailable")

    return FunctionModel(stream_function=stream)


def _empty_stream_model():
    async def stream(_messages, _info):
        if False:
            yield ""

    return FunctionModel(stream_function=stream)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("回答\n来", "源：\n- SECRET"),
        ("回答\nSour", "ces:\n- SECRET"),
        ("回答\nRefer", "ences:\n- SECRET"),
        ("回答\nhttp", "://private.example/SECRET"),
        ("回答\n[", "S11]"),
    ],
)
def test_streaming_text_guard_blocks_forbidden_patterns_at_chunk_boundaries(
    first: str, second: str
):
    guard = _StreamingTextGuard()
    emitted = guard.feed(first)
    assert "SECRET" not in emitted
    with pytest.raises(NaturalAnswerValidationError):
        guard.feed(second)


@pytest.mark.asyncio
async def test_citation_first_stream_emits_two_deltas_after_validated_plan():
    citation = _citation()
    agent = KnowledgeAgent(
        _primary_model(),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=_plan_model(),
        stream_model=_stream_model(),
    )

    events = [event async for event in agent.stream(_request("q1 和 q2"))]
    types = [event.type for event in events]
    section_start = next(event for event in events if event.type == "section_started")
    deltas = [event for event in events if event.type == "text_delta"]
    completed = events[-1]

    assert types.index("section_started") < types.index("text_delta")
    assert section_start.citation_ids == (11,)
    assert section_start.status == "grounded"
    assert len(deltas) >= 2
    assert any(event.status == "unsupported" for event in events if event.type == "section_started")
    assert completed.type == "completed"
    assert completed.answer is not None
    assert completed.answer.status == "ok"
    assert "当前检索证据不足以确认该部分。" in completed.answer.text
    assert "第一段 [S11]" in completed.answer.text


@pytest.mark.asyncio
async def test_unsafe_provider_text_aborts_section_without_successful_answer():
    citation = _citation()
    agent = KnowledgeAgent(
        _primary_model(),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=_plan_model(),
        stream_model=_stream_model(unsafe=True),
    )

    events = [event async for event in agent.stream(_request())]
    assert any(event.type == "section_aborted" for event in events)
    completed = events[-1]
    assert completed.type == "completed"
    assert completed.answer is not None
    assert completed.answer.status == "failed"
    assert "private.example" not in "".join(
        event.text or "" for event in events if event.type == "text_delta"
    )


@pytest.mark.asyncio
async def test_provider_without_stream_seam_uses_one_delta_compatibility_path():
    citation = _citation()
    agent = KnowledgeAgent(
        _primary_model(),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=_answer_model(),
    )

    events = [event async for event in agent.stream(_request())]
    assert [event.type for event in events] == [
        "activity",
        "text_delta",
        "completed",
    ]
    assert events[1].section_id is None
    assert events[1].text is not None
    assert events[-1].answer is not None
    assert events[-1].answer.status == "ok"


@pytest.mark.asyncio
async def test_provider_stream_failure_before_first_delta_uses_one_delta_fallback():
    citation = _citation()
    agent = KnowledgeAgent(
        _primary_model(),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=_dual_answer_model(),
        stream_model=_unavailable_stream_model(),
    )

    events = [event async for event in agent.stream(_request())]

    assert [event.type for event in events] == [
        "activity",
        "text_delta",
        "completed",
    ]
    assert all(event.section_id is None for event in events)
    assert events[1].text is not None
    assert events[1].text.startswith("兼容答案 [S11]")
    assert events[-1].answer is not None
    assert events[-1].answer.status == "ok"


@pytest.mark.asyncio
async def test_empty_provider_stream_uses_one_delta_fallback():
    citation = _citation()
    agent = KnowledgeAgent(
        _primary_model(),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=_dual_answer_model(),
        stream_model=_empty_stream_model(),
    )

    events = [event async for event in agent.stream(_request())]

    assert [event.type for event in events] == [
        "activity",
        "text_delta",
        "completed",
    ]
    assert all(event.section_id is None for event in events)
    assert events[1].text is not None
    assert events[1].text.startswith("兼容答案 [S11]")
    assert events[-1].answer is not None
    assert events[-1].answer.status == "ok"


def test_plan_rejects_duplicate_and_unsupported_citations():
    from pydantic import ValidationError
    from app.agent.types import AnswerStreamPlan

    with pytest.raises(ValidationError):
        AnswerStreamPlan(
            kind="grounded",
            sections=[
                {"section_id": "a", "task": "重复引用", "status": "grounded", "citation_ids": [11, 11]}
            ],
        )
    with pytest.raises(ValidationError):
        AnswerStreamPlan(
            kind="grounded",
            sections=[
                {"section_id": "a", "task": "无证据", "status": "unsupported", "citation_ids": [11]},
                {"section_id": "b", "task": "有证据", "status": "grounded", "citation_ids": [12]},
            ],
        )
