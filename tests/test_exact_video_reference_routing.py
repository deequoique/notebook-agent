from __future__ import annotations

import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ModelMessagesTypeAdapter,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.actions import AgentActionServices
from app.agent.runtime import KnowledgeAgent
from app.agent.services import ItemDetails, KnowledgeNotFound, KnowledgeServices
from app.agent.types import AgentRequest, Citation
from app.channels.pending_actions import ConfirmationResult, PendingValidationError
from app.channels.types import TenantContext
from app.config import Settings
from app.diagnostics import RequestDiagnostics
from app.ingest.submission import parse_message_references
from app.retrieval.search import bm25_search, vector_search


def _request(question: str, *, history: tuple[dict, ...] = ()) -> AgentRequest:
    return AgentRequest(
        question=question,
        tenant=TenantContext(57, 9, "wechat", "account", "external"),
        thread_db_id=12,
        thread_public_id="thread-public",
        message_id="message-id",
        request_id="request-id",
        history=history,
    )


class _Pending:
    def __init__(self):
        self.calls: list[list[str]] = []

    def request_save(self, _tenant, _thread_id, urls):
        self.calls.append(list(urls))
        return ConfirmationResult(
            "confirmation_required", urls=tuple(urls), action_id=81
        )

    def inspect_save(self, *_args):
        # The deterministic route must not need to inspect or interpret old
        # conversation state before requesting the new confirmation.
        raise AssertionError("pending snapshot should not be read")


class _RejectingPending(_Pending):
    def request_save(self, _tenant, _thread_id, urls):
        self.calls.append(list(urls))
        raise PendingValidationError("unsupported_url")


class _Submission:
    def submit_urls(self, *_args, **_kwargs):
        raise AssertionError("bare URL must not submit before confirmation")


class _Services:
    def __init__(self, citations=()):
        self.citations = list(citations)
        self.scope = None
        self.calls = 0

    def set_reference_scope(self, scope):
        self.scope = scope

    def search_segments(self, _query, *, limit=6):
        self.calls += 1
        return list(self.citations)

    def get_neighbors(self, *_args, **_kwargs):
        return []

    def get_item(self, item_id):
        raise AssertionError(f"unexpected item expansion: {item_id}")

    def open_at(self, *_args, **_kwargs):
        raise AssertionError("unexpected open_at expansion")


class _OutOfScopeExpansionServices(_Services):
    def __init__(self, citations):
        super().__init__(citations)
        self.expansion_calls: list[str] = []

    def get_neighbors(self, *_args, **_kwargs):
        self.expansion_calls.append("get_neighbors")
        return [self.citations[0]]

    def get_item(self, item_id):
        self.expansion_calls.append(f"get_item:{item_id}")
        return ItemDetails(
            item_id=1,
            title="wrong",
            author=None,
            description="wrong",
            url=self.citations[0].url,
            platform="youtube",
            duration_sec=1,
        )

    def open_at(self, *_args, **_kwargs):
        self.expansion_calls.append("open_at")
        return self.citations[0]


class _UnavailableReferenceServices(_Services):
    def __init__(self, unavailable_id: str, state: str):
        super().__init__()
        self.unavailable_id = unavailable_id
        self.state = state

    def search_segments(self, _query, *, limit=6):
        self.calls += 1
        if self.scope == (("youtube", self.unavailable_id),):
            return []
        raise AssertionError("unexpected reference scope")


class _MissingExpansionServices(_Services):
    def get_neighbors(self, *_args, **_kwargs):
        raise KnowledgeNotFound("scoped segment not found")


def test_message_reference_parser_preserves_order_deduplicates_scope_and_text():
    short = "https://youtu.be/dQw4w9WgXcQ"
    canonical = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    other = "https://youtu.be/M7lc1UVf-VE"

    parsed = parse_message_references(f"{short}，{canonical} {other}。")

    assert parsed.ordered_urls == (short, canonical, other)
    assert parsed.supported_urls == parsed.ordered_urls
    assert parsed.references == (
        ("youtube", "dQw4w9WgXcQ"),
        ("youtube", "M7lc1UVf-VE"),
    )
    assert parsed.is_bare_supported_url_batch is True

    question = parse_message_references(f"{short}，讲了什么？")
    assert question.semantic_remainder == "讲了什么"
    assert question.is_bare_supported_url_batch is False

    adjacent_question = parse_message_references(f"{short}讲了什么？")
    assert adjacent_question.references == (("youtube", "dQw4w9WgXcQ"),)
    assert adjacent_question.semantic_remainder == "讲了什么"
    assert adjacent_question.is_url_only_batch is False


def test_nonempty_malformed_service_scope_stays_fail_closed():
    service = KnowledgeServices(
        _request("question").tenant,
        lambda: pytest.fail("database must not open"),
    )

    service.set_reference_scope([("youtube", "")])

    assert service._reference_scope == ()
    assert service._reference_predicates()
    assert service._search_scope_kwargs() == {
        "platform": "__no_matching_platform__",
        "platform_ids": (),
    }


def test_vector_and_lexical_queries_include_exact_reference_predicates():
    class RecordingDB:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement):
            self.statements.append(str(statement))
            return SimpleNamespace(all=lambda: [])

    db = RecordingDB()
    kwargs = {
        "user_id": 57,
        "platform": "youtube",
        "platform_ids": ("M7lc1UVf-VE",),
    }

    assert vector_search(db, [0.1, 0.2], **kwargs) == []
    assert bm25_search(db, "target", **kwargs) == []

    assert len(db.statements) == 2
    for statement in db.statements:
        assert "content_item.user_id =" in statement
        assert "content_item.deleted_at IS NULL" in statement
        assert "content_item.state =" in statement
        assert "content_item.platform =" in statement
        assert "content_item.platform_id IN" in statement


@pytest.mark.asyncio
async def test_bare_supported_url_is_history_independent_and_never_calls_model():
    pending = _Pending()
    actions = AgentActionServices(_Submission(), pending)
    calls = 0

    def forbidden_model(_messages, _info):
        nonlocal calls
        calls += 1
        raise AssertionError("bare URL routing must not call the model")

    runtime = KnowledgeAgent(
        FunctionModel(forbidden_model),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: pytest.fail("bare URL must not construct retrieval services"),
        action_factory=lambda _request: actions,
    )
    url = "https://youtu.be/dQw4w9WgXcQ"
    result = await runtime.run(
        _request(
            f"{url} {url}",
            history=({"kind": "inventory", "text": "wrong old item"},),
        )
    )

    assert calls == 0
    assert result.answer.error_code == "save_confirmation_required"
    assert result.answer.action_results[0]["count"] == 2
    assert pending.calls == [[url, url]]


@pytest.mark.asyncio
async def test_bare_unsupported_url_retains_safe_validation_without_model():
    pending = _RejectingPending()
    actions = AgentActionServices(_Submission(), pending)

    def forbidden_model(_messages, _info):
        raise AssertionError("URL-only validation must not call the model")

    runtime = KnowledgeAgent(
        FunctionModel(forbidden_model),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: pytest.fail("URL-only validation must not construct retrieval services"),
        action_factory=lambda _request: actions,
    )
    url = "https://example.test/not-supported"

    result = await runtime.run(_request(url))

    assert result.answer.status == "failed"
    assert result.answer.error_code == "unsupported_url"
    assert pending.calls == [[url]]


@pytest.mark.asyncio
async def test_explicit_url_scope_filters_wrong_video_before_composer():
    wrong = Citation(
        item_id=1,
        segment_id=11,
        title="wrong",
        excerpt="wrong evidence",
        url="https://youtu.be/dQw4w9WgXcQ?t=10",
    )
    right = Citation(
        item_id=2,
        segment_id=22,
        title="right",
        excerpt="right evidence",
        url="https://youtu.be/M7lc1UVf-VE?t=20",
    )
    services = _Services([wrong, right])
    planner = TestModel(call_tools=["search_segments"], custom_output_text="stop")
    composer = TestModel(
        custom_output_text=json.dumps(
            {"sections": [{"text": "only target", "citation_ids": [22]}]}
        )
    )
    runtime = KnowledgeAgent(
        planner,
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: services,
        composer_model=composer,
    )

    result = await runtime.run(_request("https://youtu.be/M7lc1UVf-VE 讲了什么"))

    assert services.scope == (("youtube", "M7lc1UVf-VE"),)
    assert result.answer.citations == [right]
    assert "wrong evidence" not in result.answer.text


@pytest.mark.asyncio
async def test_history_cannot_turn_explicit_content_question_into_save_action():
    right = Citation(
        item_id=2,
        segment_id=22,
        title="right",
        excerpt="right evidence",
        url="https://youtu.be/M7lc1UVf-VE?t=20",
    )
    services = _Services([right])
    pending = _Pending()
    actions = AgentActionServices(_Submission(), pending)

    def planner(messages, info):
        visible = {tool.name for tool in info.function_tools}
        if "save_videos" in visible:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "save_videos",
                        json.dumps({"urls": ["https://youtu.be/M7lc1UVf-VE"]}),
                        tool_call_id="stale-save",
                    )
                ]
            )
        returned = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if not returned:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "目标视频"}),
                        tool_call_id="search",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("stop")])

    composer = TestModel(
        custom_output_text=json.dumps(
            {"sections": [{"text": "only target", "citation_ids": [22]}]}
        )
    )
    runtime = KnowledgeAgent(
        FunctionModel(planner),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: services,
        action_factory=lambda _request: actions,
        composer_model=composer,
    )
    history = tuple(
        ModelMessagesTypeAdapter.dump_python(
            [
                ModelRequest(
                    parts=[UserPromptPart("save the old inventory item")]
                ),
                ModelResponse(parts=[TextPart("old terminal save answer")]),
            ],
            mode="json",
        )
    )

    result = await runtime.run(
        _request("https://youtu.be/M7lc1UVf-VE 讲了什么", history=history)
    )

    assert result.answer.citations == [right]
    assert pending.calls == []


@pytest.mark.asyncio
async def test_out_of_scope_expansion_ids_cannot_enter_trusted_citations():
    wrong = Citation(
        item_id=1,
        segment_id=11,
        title="wrong",
        excerpt="wrong evidence",
        url="https://youtu.be/dQw4w9WgXcQ?t=10",
    )
    right = Citation(
        item_id=2,
        segment_id=22,
        title="right",
        excerpt="right evidence",
        url="https://youtu.be/M7lc1UVf-VE?t=20",
    )
    services = _OutOfScopeExpansionServices([wrong, right])
    planner_calls = 0

    def planner(messages, _info):
        nonlocal planner_calls
        planner_calls += 1
        returned = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        tools = {
            0: ("search_segments", {"query": "目标"}),
            1: ("get_neighbors", {"segment_id": 11}),
            2: ("get_item", {"item_id": 1}),
            3: ("open_at", {"segment_id": 11}),
        }
        if returned in tools:
            name, arguments = tools[returned]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        name,
                        json.dumps(arguments),
                        tool_call_id=f"call-{returned}",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("stop")])

    composer = TestModel(
        custom_output_text=json.dumps(
            {"sections": [{"text": "only target", "citation_ids": [22]}]}
        )
    )
    runtime = KnowledgeAgent(
        FunctionModel(planner),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: services,
        composer_model=composer,
    )

    result = await runtime.run(_request("https://youtu.be/M7lc1UVf-VE 讲了什么"))

    assert planner_calls == 5
    assert services.expansion_calls == ["get_neighbors", "get_item:1", "open_at"]
    assert result.answer.citations == [right]
    assert "wrong evidence" not in result.answer.text


@pytest.mark.asyncio
async def test_scoped_missing_lookup_has_one_truthful_tool_outcome(caplog):
    right = Citation(
        item_id=2,
        segment_id=22,
        title="right",
        excerpt="right evidence",
        url="https://youtu.be/M7lc1UVf-VE?t=20",
    )
    services = _MissingExpansionServices([right])

    def planner(messages, _info):
        returned = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if returned == 0:
            return ModelResponse(
                parts=[ToolCallPart("search_segments", {"query": "目标"})]
            )
        if returned == 1:
            return ModelResponse(
                parts=[ToolCallPart("get_neighbors", {"segment_id": 999})]
            )
        return ModelResponse(parts=[TextPart("stop")])

    runtime = KnowledgeAgent(
        FunctionModel(planner),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: services,
        composer_model=TestModel(
            custom_output_text=json.dumps(
                {"sections": [{"text": "target", "citation_ids": [22]}]}
            )
        ),
    )
    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        await runtime.run(
            _request("https://youtu.be/M7lc1UVf-VE 讲了什么"),
            diagnostics=RequestDiagnostics.start("request", 57, "a" * 32),
        )

    outcomes = [
        record.diagnostic_payload["tool_outcome"]
        for record in caplog.records
        if getattr(record, "diagnostic_payload", {}).get("tool_name")
        == "get_neighbors"
    ]
    assert outcomes == ["started", "succeeded"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("video_id", "state"),
    [("dQw4w9WgXcQ", "deleted"), ("M7lc1UVf-VE", "pending")],
)
async def test_deleted_or_non_ready_exact_reference_has_no_fallback_evidence(
    video_id, state
):
    services = _UnavailableReferenceServices(video_id, state)
    composer_calls = 0

    def forbidden_composer(_messages, _info):
        nonlocal composer_calls
        composer_calls += 1
        raise AssertionError("no-evidence exact reference must skip composer")

    runtime = KnowledgeAgent(
        TestModel(call_tools=["search_segments"], custom_output_text="stop"),
        replace(Settings(), agent_timeout_seconds=2),
        lambda _request: services,
        composer_model=FunctionModel(forbidden_composer),
    )

    result = await runtime.run(_request(f"https://youtu.be/{video_id} 讲了什么"))

    assert result.answer.status == "not_found"
    assert result.answer.error_code == "no_evidence"
    assert result.answer.citations == []
    assert composer_calls == 0


@pytest.mark.asyncio
async def test_management_tools_are_hidden_for_explicit_url_questions():
    pending = _Pending()
    actions = AgentActionServices(_Submission(), pending, management=object())
    visible_tools: list[str] = []

    def planner(_messages, info):
        visible_tools.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("no search")])

    runtime = KnowledgeAgent(
        FunctionModel(planner),
        replace(
            Settings(),
            agent_timeout_seconds=2,
        ),
        lambda _request: _Services(),
        action_factory=lambda _request: actions,
    )

    result = await runtime.run(_request("https://youtu.be/M7lc1UVf-VE 讲了什么"))

    assert result.answer.error_code == "search_required"
    assert "list_saved_items" not in visible_tools
    assert "get_saved_item" not in visible_tools
    assert "delete_saved_items" not in visible_tools
    assert "save_videos" not in visible_tools
    assert "request_save_confirmation" not in visible_tools
    assert "confirm_video_save" not in visible_tools
