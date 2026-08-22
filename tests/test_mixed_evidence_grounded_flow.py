from __future__ import annotations

import json
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.runtime import KnowledgeAgent, build_agent
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
        request_id="mixed-evidence-test",
    )


class _Services:
    def __init__(self, citations: list[Citation]):
        self.citations = citations
        self.scope_calls: list[object] = []

    def set_reference_scope(self, scope):
        self.scope_calls.append(scope)

    def search_segments(self, _query, *, limit=6, item_id=None):
        return self.citations


def _primary_with_search(text: str):
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
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(model)


def _citation(segment_id: int, item_id: int = 1) -> Citation:
    return Citation(
        item_id=item_id,
        segment_id=segment_id,
        title=f"视频 {item_id}",
        excerpt=f"证据 {segment_id}",
        url=f"https://example.test/{item_id}",
    )


def _composer(*sections: dict) -> TestModel:
    return TestModel(
        custom_output_text=json.dumps({"kind": "grounded", "sections": list(sections)})
    )


def test_primary_toolset_has_no_model_no_evidence_tool():
    assert "report_no_relevant_evidence" not in build_agent(TestModel())._function_toolset.tools


@pytest.mark.asyncio
async def test_empty_search_is_server_owned_and_skips_composer():
    services = _Services([])
    composer_calls = 0

    def forbidden_composer(_messages, _info):
        nonlocal composer_calls
        composer_calls += 1
        raise AssertionError("empty search must not invoke Composer")

    result = await KnowledgeAgent(
        _primary_with_search("没有足够信息"),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: services,
        composer_model=FunctionModel(forbidden_composer),
    ).run(_request())

    assert result.answer.status == "not_found"
    assert result.answer.error_code == "no_evidence"
    assert composer_calls == 0


@pytest.mark.asyncio
async def test_nonempty_search_composer_keeps_supported_and_unsupported_sections():
    citation = _citation(10)
    result = await KnowledgeAgent(
        _primary_with_search("无法安全验证 [S999]"),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=_composer(
            {"status": "grounded", "text": "q1 有证据", "citation_ids": [10]},
            {"status": "unsupported"},
        ),
    ).run(_request("q1，同时回答 q2"))

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert "q1 有证据 [S10]" in result.answer.text
    assert "当前检索证据不足以确认该部分。" in result.answer.text


@pytest.mark.asyncio
async def test_unsupported_section_cannot_publish_model_fact():
    citation = _citation(10)
    result = await KnowledgeAgent(
        _primary_with_search("primary [S999]"),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=TestModel(
            custom_output_text=json.dumps(
                {
                    "kind": "grounded",
                    "sections": [
                        {
                            "status": "grounded",
                            "text": "q1 有证据",
                            "citation_ids": [10],
                        },
                        {
                            "status": "unsupported",
                            "text": "q2 的事实是模型编造的",
                        },
                    ],
                }
            )
        ),
    ).run(_request("q1，同时回答 q2"))

    assert result.answer.status == "failed"
    assert result.answer.error_code == "answer_unavailable"
    assert "q2 的事实是模型编造的" not in result.answer.text


@pytest.mark.asyncio
async def test_semantic_url_question_does_not_set_exact_service_scope():
    services = _Services([_citation(10)])
    result = await KnowledgeAgent(
        _primary_with_search("回答 [S10]"),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: services,
        composer_model=_composer(
            {"status": "grounded", "text": "回答", "citation_ids": [10]}
        ),
    ).run(_request("https://youtu.be/dQw4w9WgXcQ 讲了什么"))

    assert result.answer.status == "ok"
    assert services.scope_calls == []
