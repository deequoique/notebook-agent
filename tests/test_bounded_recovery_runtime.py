from __future__ import annotations

import json
from dataclasses import replace

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.actions import AgentActionServices
from app.agent.management import SavedItem
from app.agent.runtime import KnowledgeAgent
from app.diagnostics import RequestDiagnostics
from app.agent.services import EmbeddingUnavailable, RetrievalUnavailable
from app.agent.types import AgentRequest, Citation
from app.channels.types import TenantContext
from app.config import Settings
from datetime import UTC, datetime


def _request(question: str = "查资料") -> AgentRequest:
    return AgentRequest(
        question=question,
        tenant=TenantContext(1, 1, "telegram", "bot", "user"),
        thread_db_id=1,
        thread_public_id="thread",
        message_id="message",
        request_id="request",
    )


def _settings(**kwargs):
    return replace(Settings(), agent_timeout_seconds=2, **kwargs)


class _FlakyReadServices:
    def __init__(self, *, failures: int, result=()):
        self.failures = failures
        self.result = list(result)
        self.calls: list[tuple[str, int | None]] = []

    def search_segments(self, query, *, limit=6, item_id=None):
        self.calls.append((query, item_id))
        if self.failures:
            self.failures -= 1
            raise RetrievalUnavailable("private backend detail")
        return self.result

    def get_neighbors(self, *_args, **_kwargs):
        return []

    def get_item(self, *_args, **_kwargs):
        return None

    def open_at(self, *_args, **_kwargs):
        return None


def _search_then_text(model_text: str = "有证据 [S10]"):
    def model(messages, _info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "主题"}),
                        tool_call_id="search-1",
                    )
                ]
            )
        if len(returns) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "主题"}),
                        tool_call_id="search-2",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(model_text)])

    return FunctionModel(model)


@pytest.mark.asyncio
async def test_transient_read_exact_retry_succeeds_once():
    citation = Citation(
        item_id=1,
        segment_id=10,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    services = _FlakyReadServices(failures=1, result=[citation])
    result = await KnowledgeAgent(
        _search_then_text(), _settings(), lambda _request: services
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert services.calls == [("主题", None), ("主题", None)]


@pytest.mark.asyncio
async def test_transient_read_can_stop_without_retry_and_stays_distinct_from_empty_search():
    services = _FlakyReadServices(failures=1)

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if has_return:
            return ModelResponse(parts=[TextPart("暂时无法读取。")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "主题"}),
                    tool_call_id="search-1",
                )
            ]
        )

    result = await KnowledgeAgent(
        FunctionModel(model), _settings(), lambda _request: services
    ).run(_request())

    assert result.answer.status == "failed"
    assert result.answer.error_code == "read_unavailable"
    assert services.calls == [("主题", None)]


@pytest.mark.asyncio
async def test_transient_read_exhaustion_never_makes_third_backend_call():
    services = _FlakyReadServices(failures=10)

    def model(messages, _info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if len(returns) < 3:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "主题"}),
                        tool_call_id=f"search-{len(returns) + 1}",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("我来猜测一下")])

    result = await KnowledgeAgent(
        FunctionModel(model), _settings(), lambda _request: services
    ).run(_request())

    assert len(services.calls) == 2
    assert result.answer.status == "failed"
    assert result.answer.error_code == "read_unavailable"
    assert "猜测" not in result.answer.text


@pytest.mark.asyncio
async def test_empty_search_same_query_does_not_spend_reformulation_then_changed_query_does():
    citation = Citation(
        item_id=1,
        segment_id=10,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    services = _FlakyReadServices(failures=0, result=[])
    calls = 0

    def search(query, *, limit=6, item_id=None):
        nonlocal calls
        calls += 1
        services.calls.append((query, item_id))
        return [] if calls == 1 else [citation]

    services.search_segments = search

    def model(messages, _info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if len(returns) == 0:
            query = "原问题"
        elif len(returns) == 1:
            query = "原问题"  # same empty observation; no backend call
        elif len(returns) == 2:
            query = "改写问题"  # consumes reformulate_search
        else:
            return ModelResponse(parts=[TextPart("改写后答案 [S10]")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": query}),
                    tool_call_id=f"search-{len(returns) + 1}",
                )
            ]
        )

    result = await KnowledgeAgent(
        FunctionModel(model), _settings(), lambda _request: services
    ).run(_request())

    assert services.calls == [("原问题", None), ("改写问题", None)]
    assert result.answer.citations == [citation]


@pytest.mark.asyncio
async def test_provider_failure_uses_three_answer_attempts_without_fallback():
    citation = Citation(
        item_id=1,
        segment_id=10,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    provider_calls = 0

    def model(messages, _info):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "主题"}),
                        tool_call_id="search-1",
                    )
                ]
            )
        raise RuntimeError("provider body must not be exposed")

    class Services(_FlakyReadServices):
        def search_segments(self, query, *, limit=6, item_id=None):
            self.calls.append((query, item_id))
            return [citation]

    services = Services(failures=0)
    result = await KnowledgeAgent(
        FunctionModel(model), _settings(), lambda _request: services
    ).run(_request())

    assert provider_calls == 5
    assert result.answer.status == "failed"
    assert result.answer.error_code == "answer_unavailable"
    assert result.answer.citations == []
    assert "自动总结未完成" not in result.answer.text


@pytest.mark.asyncio
async def test_answer_repair_is_one_provider_attempt_after_invalid_marker():
    citation = Citation(
        item_id=1,
        segment_id=10,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    repair_calls = 0

    def repair_model(_messages, _info):
        nonlocal repair_calls
        repair_calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "kind": "grounded",
                            "sections": [{"text": "修复", "citation_ids": [10]}],
                        }
                    )
                )
            ]
        )

    result = await KnowledgeAgent(
        TestModel(call_tools=["search_segments"], custom_output_text="坏 [S999]"),
        _settings(),
        lambda _request: _FlakyReadServices(failures=0, result=[citation]),
        composer_model=FunctionModel(repair_model),
    ).run(_request())

    assert result.answer.status == "ok"
    assert "修复 [S10]" in result.answer.text
    assert repair_calls == 1


@pytest.mark.asyncio
async def test_exact_retry_and_answer_recovery_do_not_share_action_ceiling(caplog):
    citation = Citation(
        item_id=1,
        segment_id=10,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    repair_calls = 0

    def repair_model(_messages, _info):
        nonlocal repair_calls
        repair_calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "kind": "grounded",
                            "sections": [{"text": "修复", "citation_ids": [10]}],
                        }
                    )
                )
            ]
        )

    diagnostics = RequestDiagnostics.start("a" * 32, 1)
    with caplog.at_level("INFO", logger="notebook_agent.runtime"):
        result = await KnowledgeAgent(
            _search_then_text("坏 [S999]"),
            _settings(),
            lambda _request: _FlakyReadServices(failures=1, result=[citation]),
            composer_model=FunctionModel(repair_model),
        ).run(_request(), diagnostics=diagnostics)

    recovery = [
        record.diagnostic_payload
        for record in caplog.records
        if record.diagnostic_payload.get("stage") == "recovery"
    ]
    assert result.answer.status == "ok"
    assert "修复 [S10]" in result.answer.text
    assert repair_calls == 1
    actions = [entry for entry in recovery if entry.get("recovery_action")]
    assert [entry.get("recovery_action") for entry in actions] == [
        "retry_same_read",
    ]
    assert [entry.get("recovery_count") for entry in actions] == [2]


@pytest.mark.asyncio
async def test_invalid_answer_recovery_exhausts_three_attempts_without_fallback():
    citation = Citation(
        item_id=1,
        segment_id=10,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    repair_calls = 0

    def invalid_repair(_messages, _info):
        nonlocal repair_calls
        repair_calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "kind": "grounded",
                            "sections": [{"text": "坏修复", "citation_ids": [999]}],
                        }
                    )
                )
            ]
        )

    result = await KnowledgeAgent(
        TestModel(call_tools=["search_segments"], custom_output_text="坏 [S999]"),
        _settings(),
        lambda _request: _FlakyReadServices(failures=0, result=[citation]),
        composer_model=FunctionModel(invalid_repair),
    ).run(_request())

    assert result.answer.status == "failed"
    assert result.answer.error_code == "answer_unavailable"
    assert "自动总结未完成" not in result.answer.text
    assert result.answer.citations == []
    assert repair_calls == 3


@pytest.mark.asyncio
async def test_inventory_success_plus_read_exhaustion_returns_canonical_partial():
    row = SavedItem(
        item_id=12,
        platform="youtube",
        kind="video",
        title="Canonical inventory item",
        url="https://youtu.be/video0000012",
        saved_at=datetime(2026, 8, 1, tzinfo=UTC),
        ingestion_state="ready",
    )

    class Management:
        def list_items(self, _tenant, **_filters):
            return type("Page", (), {"items": [row], "next_cursor": None})()

        def get_item(self, _tenant, _item_id):
            return row

    class Pending:
        def inspect_delete(self, *_args):
            return type("Pending", (), {"active": False})()

    services = _FlakyReadServices(failures=10)

    def model(messages, _info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if len(returns) == 0:
            return ModelResponse(
                parts=[ToolCallPart("list_saved_items", "{}", tool_call_id="list-1")]
            )
        if len(returns) < 3:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "主题"}),
                        tool_call_id=f"search-{len(returns)}",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("模型猜测的未授权总结")])

    result = await KnowledgeAgent(
        FunctionModel(model),
        _settings(),
        lambda _request: services,
        action_factory=lambda _request: AgentActionServices(
            submission=None,
            pending=Pending(),
            management=Management(),
        ),
    ).run(_request("列出收藏并总结"))

    assert result.answer.status == "ok"
    assert "Canonical inventory item" in result.answer.text
    assert "后续读取暂时不可用" in result.answer.text
    assert "模型猜测" not in result.answer.text
    assert result.answer.citations == []
    assert result.answer.action_results[0]["items"][0]["item_id"] == 12


@pytest.mark.asyncio
async def test_inventory_success_plus_provider_failure_returns_canonical_partial_without_retry():
    row = SavedItem(
        item_id=12,
        platform="youtube",
        kind="video",
        title="Canonical inventory item",
        url="https://youtu.be/video0000012",
        saved_at=datetime(2026, 8, 1, tzinfo=UTC),
        ingestion_state="ready",
    )

    class Management:
        def list_items(self, _tenant, **_filters):
            return type("Page", (), {"items": [row], "next_cursor": None})()

    class Pending:
        def inspect_delete(self, *_args):
            return type("Pending", (), {"active": False})()

    provider_calls = 0

    def model(messages, _info):
        nonlocal provider_calls
        provider_calls += 1
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if not has_return:
            return ModelResponse(
                parts=[ToolCallPart("list_saved_items", "{}", tool_call_id="list-1")]
            )
        raise ModelHTTPError(503, "test-model", body="private provider body")

    result = await KnowledgeAgent(
        FunctionModel(model),
        _settings(
        ),
        lambda _request: _FlakyReadServices(failures=0),
        action_factory=lambda _request: AgentActionServices(
            submission=None,
            pending=Pending(),
            management=Management(),
        ),
    ).run(_request("列出收藏并总结"))

    assert result.answer.status == "ok"
    assert "Canonical inventory item" in result.answer.text
    assert "后续读取暂时不可用" in result.answer.text
    assert result.answer.action_results[0]["items"][0]["item_id"] == 12
    assert provider_calls == 2


@pytest.mark.asyncio
async def test_missing_context_blocked_todo_allows_no_tool_clarification_without_retrieval():
    services = _FlakyReadServices(failures=0)

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if not has_return:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "todo_write",
                        json.dumps(
                            {
                                "items": [
                                    {
                                        "id": "choose",
                                        "title": "Choose the requested item",
                                        "status": "blocked",
                                    }
                                ]
                            }
                        ),
                        tool_call_id="todo-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("请告诉我你要总结哪一个条目？")])

    result = await KnowledgeAgent(
        FunctionModel(model), _settings(), lambda _request: services
    ).run(_request("总结第二个"))

    assert result.answer.status == "ok"
    assert result.answer.text == "请告诉我你要总结哪一个条目？"
    assert services.calls == []


@pytest.mark.asyncio
async def test_missing_context_clarification_is_not_limited_to_a_semantic_token_list():
    services = _FlakyReadServices(failures=0)

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if not has_return:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "todo_write",
                        json.dumps(
                            {
                                "items": [
                                    {
                                        "id": "choose",
                                        "title": "Resolve the ambiguous reference",
                                        "status": "blocked",
                                    }
                                ]
                            }
                        ),
                        tool_call_id="todo-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("能补充一下你指的对象吗？")])

    result = await KnowledgeAgent(
        FunctionModel(model), _settings(), lambda _request: services
    ).run(_request("总结那个"))

    assert result.answer.status == "ok"
    assert result.answer.text == "能补充一下你指的对象吗？"
    assert services.calls == []


@pytest.mark.asyncio
async def test_blocked_todo_without_clarification_shape_still_fails_closed():
    services = _FlakyReadServices(failures=0)

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if not has_return:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "todo_write",
                        json.dumps(
                            {
                                "items": [
                                    {
                                        "id": "choose",
                                        "title": "Choose the requested item",
                                        "status": "blocked",
                                    }
                                ]
                            }
                        ),
                        tool_call_id="todo-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("我已经完成了。")])

    result = await KnowledgeAgent(
        FunctionModel(model), _settings(), lambda _request: services
    ).run(_request("总结第二个"))

    assert result.answer.status == "failed"
    assert result.answer.error_code == "todo_incomplete"
    assert services.calls == []
