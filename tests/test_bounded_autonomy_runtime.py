from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.answer_validation import (
    NaturalAnswerValidationError,
    parse_inline_citation_ids,
    validate_natural_answer,
)
from app.agent.actions import AgentActionServices
from app.agent.context import ContextCitationRef, ContextItemRef, TurnContext
from app.agent.management import SavedItem
from app.agent.runtime import KnowledgeAgent, build_agent
from app.agent.types import AgentRequest, Citation
from app.channels.pending_actions import PendingDeleteSnapshot, PendingSaveSnapshot
from app.channels.types import TenantContext
from app.config import Settings


class Services:
    def __init__(self, citations=()):
        self.citations = list(citations)
        self.calls: list[str] = []

    def search_segments(self, _query, *, limit=6, item_id=None):
        self.calls.append("search_segments")
        return self.citations

    def get_neighbors(self, *_args, **_kwargs):
        self.calls.append("get_neighbors")
        return self.citations

    def get_item(self, *_args, **_kwargs):
        return None

    def open_at(self, *_args, **_kwargs):
        return self.citations[0] if self.citations else None


def request(question: str = "你好") -> AgentRequest:
    return AgentRequest(
        question=question,
        tenant=TenantContext(1, 1, "telegram", "bot", "user"),
        thread_db_id=1,
        thread_public_id="thread",
        message_id="message",
        request_id="request",
    )


def autonomy_settings():
    return replace(Settings(), agent_timeout_seconds=2)


def composer_for(*segment_ids: int, text: str = "根据知识库证据的总结") -> TestModel:
    return TestModel(
        custom_output_text=json.dumps(
            {
                "kind": "grounded",
                "sections": [
                    {
                        "status": "grounded",
                        "text": text,
                        "citation_ids": list(segment_ids),
                    }
                ],
            }
        )
    )


class Management:
    def __init__(self, rows):
        self.rows = list(rows)

    def list_items(self, _tenant, **_filters):
        return type("Page", (), {"items": self.rows, "next_cursor": None})()

    def get_item(self, _tenant, item_id):
        return next(row for row in self.rows if row.item_id == item_id)


class FlakyManagement(Management):
    def __init__(self, rows, *, failures: int):
        super().__init__(rows)
        self.failures = failures
        self.calls = 0

    def list_items(self, _tenant, **_filters):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("private database detail")
        return super().list_items(_tenant, **_filters)


class Pending:
    def inspect_save(self, *_args):
        return PendingSaveSnapshot(active=False)

    def inspect_delete(self, *_args):
        return PendingDeleteSnapshot(active=False)


class PendingState(Pending):
    def __init__(self, *, save: bool = False, delete: bool = False):
        self.save = save
        self.delete = delete

    def inspect_save(self, *_args):
        return PendingSaveSnapshot(active=self.save, count=1 if self.save else 0)

    def inspect_delete(self, *_args):
        return PendingDeleteSnapshot(
            active=self.delete,
            count=1 if self.delete else 0,
            requires_code=self.delete,
        )


def test_inline_validator_accepts_current_ids_and_rejects_forged_or_malformed():
    citation = Citation(
        item_id=1,
        segment_id=3,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    assert parse_inline_citation_ids("summary [S3] [S3]") == (3,)
    valid = validate_natural_answer(
        "## Summary\n\n- **grounded** `detail` [S3]",
        citations={3: citation},
        knowledge_search_succeeded=True,
    )
    assert valid.citations == (citation,)
    with pytest.raises(NaturalAnswerValidationError):
        validate_natural_answer(
            "summary [S99]",
            citations={3: citation},
            knowledge_search_succeeded=True,
        )
    with pytest.raises(NaturalAnswerValidationError):
        parse_inline_citation_ids("summary [S0]")
    with pytest.raises(NaturalAnswerValidationError):
        validate_natural_answer("clean [S3]", knowledge_search_succeeded=False)
    with pytest.raises(NaturalAnswerValidationError):
        validate_natural_answer("Sources:\n- https://example.test")


@pytest.mark.asyncio
async def test_flag_on_greeting_returns_natural_text_without_composer_or_retrieval():
    services = Services()
    composer_called = False

    def composer(_messages, _info):
        nonlocal composer_called
        composer_called = True
        return ModelResponse(parts=[TextPart("unexpected composer")])

    result = await KnowledgeAgent(
        TestModel(call_tools=[], custom_output_text="你好，很高兴帮助你。"),
        autonomy_settings(),
        lambda _: services,
        composer_model=FunctionModel(composer),
    ).run(request())

    assert result.answer.status == "ok"
    assert result.answer.text == "你好，很高兴帮助你。"
    assert result.answer.citations == []
    assert services.calls == []
    assert composer_called is False
    assert len(result.new_messages) == 2


@pytest.mark.asyncio
async def test_flag_on_search_requires_current_run_marker_and_appends_server_sources():
    citation = Citation(
        item_id=1,
        segment_id=3,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    services = Services([citation])
    result = await KnowledgeAgent(
        TestModel(call_tools=["search_segments"], custom_output_text="总结 [S3]"),
        autonomy_settings(),
        lambda _: services,
        composer_model=composer_for(3, text="总结"),
    ).run(request("总结我的资料"))

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert "总结 [S3]" in result.answer.text
    assert "来源：" in result.answer.text
    assert services.calls == ["search_segments"]


@pytest.mark.asyncio
async def test_flag_on_forged_marker_uses_same_evidence_composer_without_retrieval():
    citation = Citation(
        item_id=1,
        segment_id=3,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    services = Services([citation])
    composer_calls = 0

    def composer(_messages, _info):
        nonlocal composer_calls
        composer_calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "kind": "grounded",
                            "sections": [{"text": "safe", "citation_ids": [3]}],
                        }
                    )
                )
            ]
        )

    result = await KnowledgeAgent(
        TestModel(call_tools=["search_segments"], custom_output_text="bad [S999]"),
        autonomy_settings(),
        lambda _: services,
        composer_model=FunctionModel(composer),
    ).run(request("总结我的资料"))

    assert result.answer.status == "ok"
    assert "safe [S3]" in result.answer.text
    assert "S999" not in result.answer.text
    assert composer_calls == 1
    assert services.calls == ["search_segments"]


@pytest.mark.asyncio
async def test_flag_on_explicit_url_question_cannot_finish_without_search():
    services = Services()
    result = await KnowledgeAgent(
        TestModel(call_tools=[], custom_output_text="这是一个回答。"),
        autonomy_settings(),
        lambda _: services,
    ).run(request("https://youtu.be/dQw4w9WgXcQ 讲了什么"))

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert result.answer.text == "这是一个回答。"
    assert services.calls == []


def test_primary_agent_always_registers_bounded_and_management_tools():
    agent = build_agent(TestModel())
    assert {
        "todo_write",
        "search_segments",
        "save_videos",
        "list_saved_items",
        "delete_saved_items",
        "restore_saved_items",
    } <= set(agent._function_toolset.tools)


async def _visible_tools(
    *,
    settings,
    question: str = "你好",
    pending: PendingState | None = None,
) -> dict[str, dict]:
    observed: dict[str, dict] = {}

    def model(_messages, info):
        for tool in info.function_tools:
            observed[tool.name] = tool.parameters_json_schema
        return ModelResponse(parts=[TextPart("请问需要我澄清什么？")])

    action_factory = lambda _request: AgentActionServices(
        submission=None,  # type: ignore[arg-type]
        pending=pending or PendingState(),  # type: ignore[arg-type]
        management=Management([]),  # type: ignore[arg-type]
    )
    await KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: Services(),
        action_factory=action_factory,
    ).run(request(question))
    return observed


@pytest.mark.asyncio
async def test_tool_visibility_follows_pending_kind_and_exact_scope():
    base = autonomy_settings()
    base_tools = await _visible_tools(settings=base)
    assert {
        "search_segments",
        "todo_write",
        "list_saved_items",
        "get_saved_item",
        "update_saved_item",
        "delete_saved_items",
        "restore_saved_items",
        "retry_item_ingestion",
        "save_videos",
    } <= set(base_tools)
    assert not {
        "request_save_confirmation",
        "confirm_video_save",
        "clarify_save_confirmation",
        "cancel_video_save",
        "confirm_item_deletion",
        "clarify_item_deletion",
        "cancel_item_deletion",
    } & set(base_tools)

    save_pending = await _visible_tools(
        settings=base,
        pending=PendingState(save=True),
    )
    assert {
        "confirm_video_save",
        "clarify_save_confirmation",
        "cancel_video_save",
    } <= set(save_pending)
    assert "save_videos" in save_pending

    scoped_save = await _visible_tools(
        settings=base,
        question="请保存 https://youtu.be/dQw4w9WgXcQ 到知识库",
        pending=PendingState(save=True, delete=True),
    )
    assert "save_videos" in scoped_save
    assert not {
        "list_saved_items",
        "get_saved_item",
        "confirm_video_save",
        "confirm_item_deletion",
    } & set(scoped_save)

    delete_pending = await _visible_tools(
        settings=base,
        pending=PendingState(delete=True),
    )
    assert {
        "confirm_item_deletion",
        "clarify_item_deletion",
        "cancel_item_deletion",
    } <= set(delete_pending)

    for schema in delete_pending.values():
        parameter_names = set(schema.get("properties", {}))
        assert not {
            "user_id",
            "tenant",
            "thread",
            "pending_action",
            "claim",
        } & parameter_names

@pytest.mark.asyncio
async def test_incomplete_todo_finalization_fails_closed_without_persisting_content():
    def model(_messages, info):
        has_todo_return = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in _messages
        )
        if not has_todo_return and any(
            tool.name == "todo_write" for tool in info.function_tools
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "todo_write",
                        json.dumps(
                            {
                                "items": [
                                    {
                                        "id": "step",
                                        "title": "Do the step",
                                        "status": "in_progress",
                                    }
                                ]
                            }
                        ),
                        tool_call_id="todo-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("unfinished")])

    result = await KnowledgeAgent(
        FunctionModel(model), autonomy_settings(), lambda _: Services()
    ).run(request("请安排步骤"))
    assert result.answer.status == "failed"
    assert result.answer.error_code == "todo_incomplete"
    assert result.new_messages == []


def _saved_item(item_id: int, title: str) -> SavedItem:
    return SavedItem(
        item_id=item_id,
        platform="youtube",
        kind="video",
        title=title,
        url=f"https://youtu.be/video{item_id:07d}",
        saved_at=datetime(2026, 8, 1, tzinfo=UTC),
        ingestion_state="ready",
    )


@pytest.mark.asyncio
async def test_flag_on_inventory_read_is_a_nonterminal_observation():
    rows = [_saved_item(11, "First"), _saved_item(12, "Second")]
    services = Services()

    def model(messages, _info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in getattr(message, "parts", ())):
            return ModelResponse(parts=[TextPart("模型错误地说已经删除全部收藏。")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "list_saved_items", "{}", tool_call_id="list-1"
                )
            ]
        )

    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _: services,
        composer_model=composer_for(120, text="第二项总结"),
        action_factory=lambda _request: AgentActionServices(
            submission=None, pending=Pending(), management=Management(rows)  # type: ignore[arg-type]
        ),
    ).run(request("列出我的收藏"))

    assert result.answer.status == "ok"
    assert result.answer.action_results[0]["items"][0]["item_id"] == 11
    assert result.answer.action_results[0]["items"][1]["item_id"] == 12
    assert result.answer.citations == []
    assert "First" in result.answer.text
    assert "Second" in result.answer.text
    assert "模型错误" not in result.answer.text


@pytest.mark.asyncio
async def test_flag_on_inventory_transient_failure_retries_exact_read_once():
    rows = [_saved_item(11, "Recovered")]
    management = FlakyManagement(rows, failures=1)

    def model(messages, _info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if len(returns) < 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "list_saved_items",
                        "{}",
                        tool_call_id=f"list-{len(returns) + 1}",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("模型自由改写。")])

    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _request: Services(),
        action_factory=lambda _request: AgentActionServices(
            submission=None,  # type: ignore[arg-type]
            pending=PendingState(),  # type: ignore[arg-type]
            management=management,  # type: ignore[arg-type]
        ),
    ).run(request("列出我的收藏"))

    assert result.answer.status == "ok"
    assert "Recovered" in result.answer.text
    assert "模型自由改写" not in result.answer.text
    assert management.calls == 2


@pytest.mark.asyncio
async def test_flag_on_list_then_scoped_search_uses_only_observed_second_item():
    rows = [_saved_item(11, "First"), _saved_item(12, "Second")]
    citation = Citation(
        item_id=12,
        segment_id=120,
        title="Second",
        excerpt="second evidence",
        url=rows[1].url,
    )
    services = Services([citation])
    observed_item_ids = []

    def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        visible = {tool.name for tool in info.function_tools}
        if not returns:
            return ModelResponse(
                parts=[ToolCallPart("list_saved_items", "{}", tool_call_id="list-1")]
            )
        if len(returns) == 1 and "search_segments" in visible:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "summary", "item_id": 12}),
                        tool_call_id="search-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("第二项总结 [S120]")])

    original_search = services.search_segments

    def tracked_search(query, *, limit=6, item_id=None):
        observed_item_ids.append(item_id)
        return original_search(query, limit=limit, item_id=item_id)

    services.search_segments = tracked_search
    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _: services,
        composer_model=composer_for(120, text="第二项总结"),
        action_factory=lambda _request: AgentActionServices(
            submission=None, pending=Pending(), management=Management(rows)  # type: ignore[arg-type]
        ),
    ).run(request("列出我的收藏，然后总结第二个"))

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert observed_item_ids == [12]
    assert result.answer.action_results[0]["items"][1]["item_id"] == 12


@pytest.mark.asyncio
async def test_current_run_search_citation_can_scope_follow_up_search():
    global_citation = Citation(
        item_id=12,
        segment_id=120,
        title="Second",
        excerpt="global evidence",
        url="https://youtu.be/video0000012",
    )
    scoped_citation = Citation(
        item_id=12,
        segment_id=121,
        title="Second",
        excerpt="refined evidence",
        url="https://youtu.be/video0000012",
    )

    class SearchThenScopedServices(Services):
        def __init__(self):
            super().__init__()
            self.item_ids: list[int | None] = []

        def search_segments(self, _query, *, limit=6, item_id=None):
            self.calls.append("search_segments")
            self.item_ids.append(item_id)
            return [scoped_citation] if item_id == 12 else [global_citation]

    services = SearchThenScopedServices()

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
                        json.dumps({"query": "global"}),
                        tool_call_id="global-search",
                    )
                ]
            )
        if len(returns) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "refined", "item_id": 12}),
                        tool_call_id="scoped-search",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("第二项总结 [S121]")])

    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _request: services,
        composer_model=composer_for(121, text="第二项总结"),
    ).run(request("先找相关内容，再限定到第二项"))

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert result.answer.citations == [scoped_citation]
    assert services.item_ids == [None, 12]
    assert services.calls == ["search_segments", "search_segments"]


@pytest.mark.asyncio
async def test_prior_inventory_context_can_scope_search_and_use_current_evidence():
    citation = Citation(
        item_id=12,
        segment_id=120,
        title="Second",
        excerpt="second evidence",
        url="https://youtu.be/video0000012",
    )
    services = Services([citation])
    scoped_ids: list[int | None] = []

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if has_return:
            return ModelResponse(parts=[TextPart("第二项总结 [S120]")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "summary", "item_id": 12}),
                    tool_call_id="search-1",
                )
            ]
        )

    original_search = services.search_segments

    def tracked_search(query, *, limit=6, item_id=None):
        scoped_ids.append(item_id)
        return original_search(query, limit=limit, item_id=item_id)

    services.search_segments = tracked_search
    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _request: services,
        composer_model=composer_for(120, text="第二项总结"),
    ).run(
        replace(
            request("总结第二个"),
            context=TurnContext(
                recent_inventory=(
                    ContextItemRef(item_id=12, title="Second", ordinal=2),
                )
            ),
        )
    )

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert scoped_ids == [12]


@pytest.mark.asyncio
async def test_prior_inventory_context_with_empty_backend_returns_no_evidence():
    services = Services([])

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if has_return:
            return ModelResponse(parts=[TextPart("没有找到 [S999]")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "summary", "item_id": 12}),
                    tool_call_id="search-1",
                )
            ]
        )

    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _request: services,
    ).run(
        replace(
            request("总结第二个"),
            context=TurnContext(
                recent_inventory=(
                    ContextItemRef(item_id=12, title="Stale second", ordinal=2),
                )
            ),
        )
    )

    assert result.answer.status == "not_found"
    assert result.answer.error_code == "no_evidence"
    assert result.answer.citations == []


@pytest.mark.asyncio
async def test_prior_source_context_cannot_authorize_scoped_search():
    services = Services([])

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if has_return:
            return ModelResponse(parts=[TextPart("无法继续。")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "summary", "item_id": 12}),
                    tool_call_id="search-1",
                )
            ]
        )

    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _request: services,
    ).run(
        replace(
            request("总结此前来源"),
            context=TurnContext(
                recent_sources=(
                    ContextCitationRef(
                        item_id=12,
                        segment_id=120,
                        title="Prior source",
                    ),
                ),
                history=({"item_id": 12},),
            ),
        )
    )

    assert result.answer.status == "not_found"
    assert result.answer.error_code == "no_evidence"
    assert services.calls == ["search_segments"]


@pytest.mark.asyncio
async def test_flag_on_unobserved_item_scope_fails_closed_without_backend_search():
    rows = [_saved_item(11, "First")]
    services = Services([])

    def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in getattr(message, "parts", ())):
            return ModelResponse(parts=[TextPart("无法继续。")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "summary", "item_id": 999}),
                    tool_call_id="search-1",
                )
            ]
        )

    result = await KnowledgeAgent(
        FunctionModel(model),
        autonomy_settings(),
        lambda _: services,
        action_factory=lambda _request: AgentActionServices(
            submission=None, pending=Pending(), management=Management(rows)  # type: ignore[arg-type]
        ),
    ).run(request("总结条目 999"))

    assert result.answer.status == "not_found"
    assert result.answer.error_code == "no_evidence"
    assert services.calls == ["search_segments"]
