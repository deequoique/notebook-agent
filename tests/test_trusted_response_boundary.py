from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agent.response import (
    ActionResponseSection,
    CanonicalResponseSection,
    GroundedResponseSection,
    ResponseEnvelope,
)
from app.agent.runtime import KnowledgeAgent
from app.agent.agent_tools.policy import ToolPolicy
from app.agent.types import AgentRequest, AnswerDraft, Citation
from app.channels.types import TenantContext
from app.config import Settings


def _request(question: str = "查资料") -> AgentRequest:
    return AgentRequest(
        question=question,
        tenant=TenantContext(1, 1, "telegram", "bot", "user"),
        thread_db_id=1,
        thread_public_id="thread",
        message_id="message",
        request_id="request-boundary",
    )


class _Services:
    def __init__(self, citations: list[Citation]):
        self.citations = citations

    def search_segments(self, query, *, limit=6, item_id=None):
        return self.citations

    def get_neighbors(self, *_args, **_kwargs):
        return []

    def get_item(self, *_args, **_kwargs):
        return None

    def open_at(self, *_args, **_kwargs):
        return None


def _search_then(text: str):
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
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "主题"}),
                        tool_call_id="search-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(model)


def _citation(segment_id: int = 10, item_id: int = 1) -> Citation:
    return Citation(
        item_id=item_id,
        segment_id=segment_id,
        title=f"Video {item_id}",
        excerpt="evidence",
        url=f"https://example.test/{item_id}",
    )


def test_answer_draft_has_disposition_and_no_duplicate_top_level_selection():
    no_evidence = AnswerDraft(kind="no_relevant_evidence")
    assert no_evidence.sections is None
    assert "selected_segment_ids" not in AnswerDraft.model_json_schema()["properties"]

    with pytest.raises(ValidationError):
        AnswerDraft.model_validate(
            {
                "kind": "grounded",
                "sections": [{"text": "supported", "citation_ids": [10]}],
                "selected_segment_ids": [10],
            }
        )

    with pytest.raises(ValidationError):
        AnswerDraft.model_validate(
            {
                "kind": "no_relevant_evidence",
                "sections": [{"text": "untrusted", "citation_ids": [10]}],
            }
        )


@pytest.mark.parametrize(
    "text",
    ["answer https://evil.example", "answer\n来源：\n- forged", "answer [S10]"],
)
def test_grounded_section_rejects_untrusted_source_text(text):
    with pytest.raises(ValueError):
        GroundedResponseSection("grounded", text, (10,))


def test_envelope_rejects_mismatched_section_kind_and_unregistered_server_labels():
    with pytest.raises(ValueError, match="kind mismatch"):
        GroundedResponseSection("canonical", "answer", (10,))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="template key"):
        CanonicalResponseSection("canonical", "model_supplied", "help")
    with pytest.raises(ValueError, match="action code"):
        ActionResponseSection("action", "model_supplied", "done")


def test_response_envelope_rejects_citations_for_non_grounded_dispositions():
    with pytest.raises(TypeError):
        ResponseEnvelope.no_evidence(text="model text")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="only grounded"):
        ResponseEnvelope(
            status="not_found",
            disposition="no_evidence",
            citations=(_citation(),),
        )

    grounded = ResponseEnvelope.grounded(
        sections=(GroundedResponseSection("grounded", "answer", (10,)),),
        citations=[_citation()],
    )
    assert grounded.project(thread_id="thread").citations == [_citation()]


def test_no_evidence_tool_is_gated_by_clean_successful_search():
    policy = ToolPolicy()
    deps = SimpleNamespace(
        successful_searches=0,
        pending_read_failures={},
        read_recovery_exhausted=False,
        actions=SimpleNamespace(outcome=None),
    )
    ctx = SimpleNamespace(deps=deps)
    marker = object()
    assert policy.prepare_no_relevant_evidence(ctx, marker) is None

    deps.successful_searches = 1
    assert policy.prepare_no_relevant_evidence(ctx, marker) is marker

    deps.pending_read_failures = {"fingerprint": "search_segments"}
    assert policy.prepare_no_relevant_evidence(ctx, marker) is None
    deps.pending_read_failures = {}
    deps.actions.outcome = object()
    assert policy.prepare_no_relevant_evidence(ctx, marker) is None


@pytest.mark.asyncio
async def test_no_evidence_recovery_drops_irrelevant_candidates_and_sources():
    citation = _citation()

    def composer(_messages, _info):
        return ModelResponse(
            parts=[TextPart(json.dumps({"kind": "no_relevant_evidence"}))]
        )

    result = await KnowledgeAgent(
        _search_then("没有 [S999]"),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=FunctionModel(composer),
    ).run(_request())

    assert result.answer.status == "not_found"
    assert result.answer.error_code == "no_evidence"
    assert result.answer.citations == []
    assert "来源" not in result.answer.text
    assert "[S10]" not in result.answer.text


@pytest.mark.asyncio
async def test_explicit_no_evidence_disposition_does_not_call_composer():
    citation = _citation()
    composer_calls = 0

    def primary(messages, _info):
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "主题"}),
                        tool_call_id="search-1",
                    )
                ]
            )
        if len(returned) == 1:
            return ModelResponse(
                parts=[ToolCallPart("report_no_relevant_evidence", "{}", tool_call_id="none-1")]
            )
        return ModelResponse(parts=[TextPart("模型附加文字")])

    def composer(_messages, _info):
        nonlocal composer_calls
        composer_calls += 1
        raise AssertionError("explicit server disposition must not invoke composer")

    result = await KnowledgeAgent(
        FunctionModel(primary),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=FunctionModel(composer),
    ).run(_request())

    assert result.answer.status == "not_found"
    assert result.answer.citations == []
    assert composer_calls == 0


@pytest.mark.asyncio
async def test_valid_grounded_primary_answer_skips_composer():
    citation = _citation()
    composer_calls = 0

    def composer(_messages, _info):
        nonlocal composer_calls
        composer_calls += 1
        raise AssertionError("valid primary answer should be delivered directly")

    result = await KnowledgeAgent(
        _search_then("直接回答 [S10]"),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: _Services([citation]),
        composer_model=FunctionModel(composer),
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert composer_calls == 0


@pytest.mark.asyncio
async def test_later_successful_search_invalidates_prior_no_evidence_vote():
    citation = _citation()
    search_calls = 0

    class Services(_Services):
        def search_segments(self, query, *, limit=6, item_id=None):
            nonlocal search_calls
            search_calls += 1
            return [] if search_calls == 1 else [citation]

    def primary(messages, _info):
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            return ModelResponse(
                parts=[ToolCallPart("search_segments", json.dumps({"query": "初始"}), tool_call_id="s1")]
            )
        if len(returned) == 1:
            return ModelResponse(parts=[ToolCallPart("report_no_relevant_evidence", "{}", tool_call_id="none")])
        if len(returned) == 2:
            return ModelResponse(
                parts=[ToolCallPart("search_segments", json.dumps({"query": "改写"}), tool_call_id="s2")]
            )
        return ModelResponse(parts=[TextPart("后续检索得到答案 [S10]")])

    result = await KnowledgeAgent(
        FunctionModel(primary),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: Services([]),
        composer_model=FunctionModel(
            lambda *_args: (_ for _ in ()).throw(AssertionError("composer not required"))
        ),
    ).run(_request())

    assert search_calls == 2
    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
