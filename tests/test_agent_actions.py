from dataclasses import replace
import json

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.actions import AgentActionRuntime, AgentActionServices
from app.agent.runtime import KnowledgeAgent
from app.agent.services import ItemDetails
from app.agent.types import AgentRequest, Citation
from app.channels.pending_actions import (
    ConfirmationResult,
    PendingDeleteSnapshot,
    PendingSaveSnapshot,
    PendingValidationError,
)
from app.channels.types import TenantContext
from app.config import Settings
from app.ingest.submission import BatchSaveResult, SaveItemResult


def test_canonical_action_summary_separates_counts_and_safe_failures():
    outcome = AgentActionRuntime._batch_outcome(
        BatchSaveResult(
            (
                SaveItemResult("A1", 0, "queued", item_id=41),
                SaveItemResult(
                    "A2", 1, "already_exists", item_id=42
                ),
                SaveItemResult(
                    "A3",
                    2,
                    "queue_unavailable",
                    item_id=43,
                    safe_error_code="queue_unavailable",
                ),
            )
        )
    )

    assert outcome.error_code == "save_partial"
    assert "已入队 1 个，已存在 1 个，失败 1 个" in outcome.text
    assert "处理队列暂时不可用，请稍后重试该链接" in outcome.text
    assert "queue_unavailable" not in outcome.text


class FakeKnowledgeServices:
    def __init__(self, citations=()):
        self.citations = list(citations)
        self.calls = []

    def search_segments(self, query, *, limit=6):
        self.calls.append("search_segments")
        return self.citations

    def get_neighbors(self, segment_id, *, radius=1):
        self.calls.append("get_neighbors")
        return self.citations

    def get_item(self, item_id):
        self.calls.append("get_item")
        return ItemDetails(
            item_id,
            "title",
            None,
            None,
            "https://example.test",
            "youtube",
            1,
        )

    def open_at(self, segment_id):
        self.calls.append("open_at")
        return self.citations[0]


def _composer_for(segment_id: int = 3, text: str = "answer") -> TestModel:
    return TestModel(
        custom_output_text=json.dumps(
            {
                "kind": "grounded",
                "sections": [
                    {
                        "status": "grounded",
                        "text": text,
                        "citation_ids": [segment_id],
                    }
                ],
            }
        )
    )


class FakeSubmission:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.retry_calls = []
        self.retry_result = SaveItemResult(
            "A1", 0, "queued", item_id=41, state="pending"
        )

    def submit_urls(
        self,
        tenant,
        urls,
        *,
        why_saved,
        request_key,
        source_thread_id=None,
    ):
        self.calls.append(
            {
                "tenant": tenant,
                "urls": list(urls),
                "why_saved": why_saved,
                "request_key": request_key,
                "source_thread_id": source_thread_id,
            }
        )
        return self.result

    def retry_item(
        self,
        tenant,
        item_id,
        *,
        request_key,
        source_thread_id=None,
    ):
        self.retry_calls.append(
            {
                "tenant": tenant,
                "item_id": item_id,
                "request_key": request_key,
                "source_thread_id": source_thread_id,
            }
        )
        return self.retry_result


class FakePending:
    def __init__(self):
        self.request_calls = []
        self.confirm_calls = []
        self.cancel_calls = []
        self.request_error = None
        self.request_result = ConfirmationResult(
            "confirmation_required",
            urls=("https://www.youtube.com/watch?v=dQw4w9WgXcQ",),
            action_id=81,
        )
        self.confirm_result = ConfirmationResult(
            "confirmed",
            urls=("https://www.youtube.com/watch?v=dQw4w9WgXcQ",),
            action_id=81,
        )
        self.cancel_result = ConfirmationResult(
            "cancelled",
            action_id=81,
        )
        self.inspect_calls = []
        self.snapshot = PendingSaveSnapshot(active=False)

    def request_save(self, tenant, thread_id, urls):
        self.request_calls.append((tenant, thread_id, list(urls)))
        if self.request_error is not None:
            raise self.request_error
        return self.request_result

    def confirm_save(self, tenant, thread_id, *, message_id):
        self.confirm_calls.append((tenant, thread_id, message_id))
        return self.confirm_result

    def cancel_save(self, tenant, thread_id):
        self.cancel_calls.append((tenant, thread_id))
        return self.cancel_result

    def inspect_save(self, tenant, thread_id):
        self.inspect_calls.append((tenant, thread_id))
        return self.snapshot


def _request(question):
    return AgentRequest(
        question=question,
        tenant=TenantContext(57, 9, "wechat", "account", "external"),
        thread_db_id=12,
        thread_public_id="thread-public",
        message_id="message-id",
        request_id="request-id",
    )


def _tool_model(tool_name, arguments):
    def model(messages, _info):
        returned = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if returned:
            return ModelResponse(parts=[TextPart("model action draft")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name,
                    json.dumps(arguments),
                    tool_call_id=f"{tool_name}-call",
                )
            ]
        )

    return FunctionModel(model)


def _retrying_batch_model(tool_name, incomplete_urls, complete_urls):
    calls = []

    def model(messages, _info):
        calls.append(messages)
        last_request = next(
            message
            for message in reversed(messages)
            if isinstance(message, ModelRequest)
        )
        if any(
            isinstance(part, ToolReturnPart)
            for part in last_request.parts
        ):
            return ModelResponse(parts=[TextPart("untrusted action draft")])
        retry = any(
            isinstance(part, RetryPromptPart)
            for part in last_request.parts
        )
        urls = complete_urls if retry else incomplete_urls
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name,
                    json.dumps({"urls": urls}),
                    tool_call_id=f"{tool_name}-{'retry' if retry else 'first'}",
                )
            ]
        )

    return FunctionModel(model), calls


def _runtime(
    model,
    submission,
    pending,
    *,
    enabled=True,
    knowledge=None,
    composer_model=None,
):
    settings = replace(
        Settings(),
        agent_timeout_seconds=2,
    )
    services = AgentActionServices(submission, pending)
    return KnowledgeAgent(
        model,
        settings,
        lambda _request: knowledge or FakeKnowledgeServices(),
        action_factory=lambda _request: services,
        composer_model=composer_model,
    )


def _queued_result():
    return BatchSaveResult(
        (
            SaveItemResult(
                "A1", 0, "queued", item_id=41, state="pending"
            ),
        )
    )


def _dynamic_pending_model(tool_name, observed_instructions):
    """A fake model that verifies the Agent, not channel code, chose a tool."""

    def model(messages, info):
        observed_instructions.append(info.instructions)
        returned = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if returned:
            return ModelResponse(parts=[TextPart("untrusted action draft")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name,
                    "{}",
                    tool_call_id=f"{tool_name}-call",
                )
            ]
        )

    return FunctionModel(model)


@pytest.mark.asyncio
async def test_live_pending_snapshot_is_dynamic_trusted_context_and_read_once():
    submission = FakeSubmission(_queued_result())
    pending = FakePending()
    pending.snapshot = PendingSaveSnapshot(active=True, count=2)
    observed_instructions = []
    runtime = _runtime(
        _dynamic_pending_model("confirm_video_save", observed_instructions),
        submission,
        pending,
    )

    result = await runtime.run(_request("需要"))

    assert result.answer.error_code == "save_accepted"
    assert pending.confirm_calls == [
        (_request("需要").tenant, 12, "message-id")
    ]
    assert len(submission.calls) == 1
    assert pending.inspect_calls == [(_request("需要").tenant, 12)]
    instruction = "\n".join(observed_instructions)
    assert "可信服务器状态：当前 conversation 有 2 个视频等待保存确认。" in instruction
    assert "confirm_video_save" in instruction
    assert "cancel_video_save" in instruction
    assert "https://" not in instruction
    assert "external" not in instruction


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "tool_name", "expected_code"),
    [
        ("需要", "confirm_video_save", "save_accepted"),
        ("不用了", "cancel_video_save", "save_cancelled"),
        ("我还不确定", "clarify_save_confirmation", "save_confirmation_required"),
    ],
)
async def test_pending_short_reply_routing_is_selected_by_the_model(
    question, tool_name, expected_code
):
    submission = FakeSubmission(_queued_result())
    pending = FakePending()
    pending.snapshot = PendingSaveSnapshot(active=True, count=1)
    runtime = _runtime(
        _dynamic_pending_model(tool_name, []), submission, pending
    )

    result = await runtime.run(_request(question))

    assert result.answer.error_code == expected_code
    assert pending.inspect_calls == [(_request(question).tenant, 12)]
    if tool_name == "confirm_video_save":
        assert len(submission.calls) == 1
    else:
        assert submission.calls == []
    if tool_name == "clarify_save_confirmation":
        assert result.answer.text == "需要把这个视频保存到知识库吗？"


@pytest.mark.asyncio
async def test_unrelated_question_with_live_pending_keeps_knowledge_guards():
    citation = Citation(
        item_id=2,
        segment_id=3,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    pending = FakePending()
    pending.snapshot = PendingSaveSnapshot(active=True, count=1)

    def model(messages, _info):
        returned = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if returned:
            return ModelResponse(parts=[TextPart("answer [S3]")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "知识库有什么"}),
                    tool_call_id="search-call",
                )
            ]
        )

    knowledge = FakeKnowledgeServices([citation])
    runtime = _runtime(
        FunctionModel(model),
        FakeSubmission(_queued_result()),
        pending,
        knowledge=knowledge,
        composer_model=_composer_for(3),
    )
    result = await runtime.run(_request("知识库有什么"))

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert knowledge.calls == ["search_segments"]
    assert pending.confirm_calls == []
    assert pending.cancel_calls == []


@pytest.mark.asyncio
async def test_explicit_save_uses_trusted_request_context_once():
    submission = FakeSubmission(_queued_result())
    pending = FakePending()
    runtime = _runtime(
        _tool_model(
            "save_videos",
            {
                "urls": ["https://youtu.be/dQw4w9WgXcQ"],
                "why_saved": "稍后看",
            },
        ),
        submission,
        pending,
    )

    result = await runtime.run(
        _request("帮我保存这个视频 https://youtu.be/dQw4w9WgXcQ")
    )

    assert result.answer.status == "ok"
    assert result.answer.error_code == "save_accepted"
    assert result.answer.citations == []
    assert result.answer.action_results[0]["status"] == "queued"
    assert result.new_messages == []
    assert "model action draft" not in result.answer.text
    assert len(submission.calls) == 1
    call = submission.calls[0]
    assert call["tenant"].app_user_id == 57
    assert call["request_key"] == "thread-public:message-id:save"
    assert call["source_thread_id"] == 12


def test_retry_uses_trusted_request_thread_for_new_dispatch():
    submission = FakeSubmission(_queued_result())
    runtime = AgentActionRuntime(
        _request("重试条目 41"),
        AgentActionServices(submission, FakePending(), object()),
        enabled=True,
        management_enabled=True,
    )

    outcome = runtime.retry_item_ingestion(41)

    assert outcome.error_code == "retry_queued"
    assert submission.retry_calls == [
        {
            "tenant": _request("重试条目 41").tenant,
            "item_id": 41,
            "request_key": "thread-public:message-id:retry",
            "source_thread_id": 12,
        }
    ]


@pytest.mark.asyncio
async def test_bare_url_requests_confirmation_without_submission():
    submission = FakeSubmission(BatchSaveResult(()))
    pending = FakePending()
    runtime = _runtime(
        _tool_model(
            "request_save_confirmation",
            {"urls": ["https://youtu.be/dQw4w9WgXcQ"]},
        ),
        submission,
        pending,
    )

    result = await runtime.run(
        _request("https://youtu.be/dQw4w9WgXcQ")
    )

    assert result.answer.status == "ok"
    assert result.answer.error_code == "save_confirmation_required"
    assert "需要把这个视频保存到知识库吗？" in result.answer.text
    assert submission.calls == []
    assert len(pending.request_calls) == 1


@pytest.mark.asyncio
async def test_explicit_batch_retry_requires_complete_order_and_duplicates():
    short_a = "https://youtu.be/dQw4w9WgXcQ"
    short_b = "https://youtu.be/9bZkp7q19f0"
    canonical_a = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    complete = [canonical_a, short_b, canonical_a]
    model, model_calls = _retrying_batch_model(
        "save_videos",
        [short_a, short_b],
        complete,
    )
    submission = FakeSubmission(
        BatchSaveResult(
            tuple(
                SaveItemResult(
                    f"A{index + 1}",
                    index,
                    "queued",
                    item_id=41 + index,
                    state="pending",
                )
                for index in range(3)
            )
        )
    )
    pending = FakePending()
    runtime = _runtime(model, submission, pending)

    result = await runtime.run(
        _request(f"保存 {short_a} {short_b} {short_a}")
    )

    assert result.answer.status == "ok"
    assert result.answer.error_code == "save_accepted"
    assert [row["status"] for row in result.answer.action_results] == [
        "queued",
        "queued",
        "queued",
    ]
    assert len(model_calls) == 3
    assert len(submission.calls) == 1
    assert submission.calls[0]["urls"] == complete
    assert pending.request_calls == []


@pytest.mark.asyncio
async def test_bare_batch_retry_requires_complete_order_and_duplicates():
    short_a = "https://youtu.be/M7lc1UVf-VE"
    short_b = "https://youtu.be/ScMzIvxBSi4"
    canonical_a = "https://www.youtube.com/watch?v=M7lc1UVf-VE"
    complete = [canonical_a, short_b, canonical_a]
    model, model_calls = _retrying_batch_model(
        "request_save_confirmation",
        [short_a, short_b],
        complete,
    )
    submission = FakeSubmission(BatchSaveResult(()))
    pending = FakePending()
    pending.request_result = ConfirmationResult(
        "confirmation_required",
        urls=tuple(complete),
        action_id=81,
    )
    runtime = _runtime(model, submission, pending)

    result = await runtime.run(
        _request(f"{short_a} {short_b} {short_a}")
    )

    assert result.answer.status == "ok"
    assert result.answer.error_code == "save_confirmation_required"
    assert result.answer.action_results[0]["count"] == 3
    # Bare supported URL batches are routed directly to the durable
    # save-confirmation path; model history cannot alter order or duplicates.
    assert len(model_calls) == 0
    assert len(pending.request_calls) == 1
    assert pending.request_calls[0][2] == [short_a, short_b, short_a]
    assert submission.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "url", "pending_error", "expected_code"),
    [
        (
            "not-a-url",
            "not-a-url",
            PendingValidationError("invalid_url"),
            "runtime_error",
        ),
        (
            "https://example.test/video",
            "https://example.test/video",
            PendingValidationError("unsupported_url"),
            "unsupported_url",
        ),
    ],
)
async def test_bare_invalid_or_unsupported_input_has_no_side_effect(
    question, url, pending_error, expected_code
):
    submission = FakeSubmission(BatchSaveResult(()))
    pending = FakePending()
    pending.request_error = pending_error
    runtime = _runtime(
        _tool_model(
            "request_save_confirmation",
            {"urls": [url]},
        ),
        submission,
        pending,
    )

    result = await runtime.run(_request(question))

    assert result.answer.status == "failed"
    assert result.answer.error_code == expected_code
    assert submission.calls == []


@pytest.mark.asyncio
async def test_confirm_uses_server_urls_and_cancel_does_not_submit():
    submission = FakeSubmission(_queued_result())
    pending = FakePending()
    # The runtime now requires a trusted pending-save summary before routing
    # confirmation/cancel actions.  Seed the fixture with one active save;
    # this test is about server-owned URLs and source-thread propagation.
    pending.snapshot = PendingSaveSnapshot(active=True, count=1)
    pending.confirm_result = ConfirmationResult(
        "confirmed",
        urls=("https://www.youtube.com/watch?v=dQw4w9WgXcQ",),
        action_id=81,
        thread_id=34,
    )
    confirmed_runtime = _runtime(
        _tool_model("confirm_video_save", {}),
        submission,
        pending,
    )

    confirmed = await confirmed_runtime.run(_request("是，保存吧"))

    assert confirmed.answer.error_code == "save_accepted"
    assert submission.calls[0]["urls"] == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    ]
    assert submission.calls[0]["request_key"] == (
        "thread-public:message-id:confirm"
    )
    assert submission.calls[0]["source_thread_id"] == 34

    cancelled_submission = FakeSubmission(_queued_result())
    cancelled_runtime = _runtime(
        _tool_model("cancel_video_save", {}),
        cancelled_submission,
        pending,
    )
    cancelled = await cancelled_runtime.run(_request("不用了"))

    assert cancelled.answer.status == "ok"
    assert cancelled.answer.error_code == "save_cancelled"
    assert cancelled_submission.calls == []


@pytest.mark.asyncio
async def test_model_cannot_save_url_absent_from_current_message():
    submission = FakeSubmission(BatchSaveResult(()))
    runtime = _runtime(
        _tool_model(
            "save_videos",
            {"urls": ["https://youtu.be/9bZkp7q19f0"]},
        ),
        submission,
        FakePending(),
    )

    result = await runtime.run(
        _request("保存 https://youtu.be/dQw4w9WgXcQ")
    )

    assert result.answer.status == "failed"
    assert result.answer.error_code == "invalid_url"
    assert result.answer.action_results[0]["status"] == "invalid_url"
    assert submission.calls == []


@pytest.mark.asyncio
async def test_mixed_search_then_save_returns_action_without_citation_retry():
    citation = Citation(
        item_id=2,
        segment_id=3,
        title="source",
        excerpt="evidence",
        url="https://example.test/source",
    )
    knowledge = FakeKnowledgeServices([citation])
    submission = FakeSubmission(_queued_result())

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
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "existing evidence"}),
                        tool_call_id="search-call",
                    ),
                    ToolCallPart(
                        "save_videos",
                        json.dumps(
                            {
                                "urls": [
                                    "https://youtu.be/dQw4w9WgXcQ"
                                ]
                            }
                        ),
                        tool_call_id="save-call",
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart("action draft without S marker")])

    runtime = _runtime(
        FunctionModel(model),
        submission,
        FakePending(),
        knowledge=knowledge,
    )
    result = await runtime.run(
        _request("保存并处理 https://youtu.be/dQw4w9WgXcQ")
    )

    assert result.answer.status == "ok"
    assert result.answer.error_code == "save_accepted"
    assert result.answer.citations == []
    assert knowledge.calls == ["search_segments"]
    assert len(submission.calls) == 1


@pytest.mark.asyncio
async def test_terminal_action_wins_over_later_invalid_scoped_search():
    submission = FakeSubmission(_queued_result())
    pending = FakePending()

    def model(messages, _info):
        has_return = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if has_return:
            return ModelResponse(parts=[TextPart("untrusted mixed draft")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "save_videos",
                    json.dumps(
                        {
                            "urls": ["https://youtu.be/dQw4w9WgXcQ"],
                            "why_saved": "稍后复习",
                        }
                    ),
                    tool_call_id="save-first",
                ),
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "forged", "item_id": 999}),
                    tool_call_id="scope-after-action",
                ),
            ]
        )

    runtime = KnowledgeAgent(
        FunctionModel(model),
        replace(Settings(), agent_timeout_seconds=2, agent_tool_calls_limit=2),
        lambda _request: FakeKnowledgeServices(),
        action_factory=lambda _request: AgentActionServices(submission, pending),
    )
    result = await runtime.run(
        _request("帮我保存这个视频 https://youtu.be/dQw4w9WgXcQ")
    )

    assert result.answer.status == "ok"
    assert result.answer.error_code == "save_accepted"
    assert result.answer.citations == []
    assert len(submission.calls) == 1


@pytest.mark.asyncio
async def test_mixed_search_and_delete_uses_canonical_per_item_outcome():
    rows = (
        {"item_id": 1, "status": "deleted", "safe_error_code": None},
        {"item_id": 2, "status": "already_restored", "safe_error_code": None},
        {"item_id": 3, "status": "already_deleted", "safe_error_code": None},
    )

    class MixedPending:
        def inspect_delete(self, tenant, thread_id):
            return PendingDeleteSnapshot(active=True, count=3)

        def confirm_delete(self, tenant, thread_id, **kwargs):
            return ConfirmationResult(
                "confirmed",
                item_ids=(1, 2, 3),
                results=rows,
                action_id=17,
            ), None

    class MixedKnowledge:
        def __init__(self):
            self.calls = []

        def search_segments(self, query, *, limit=6):
            self.calls.append(query)
            return []

    knowledge = MixedKnowledge()

    def model(messages, _info):
        has_tool_returns = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if has_tool_returns:
            return ModelResponse(parts=[TextPart("untrusted mixed draft")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_segments",
                    json.dumps({"query": "irrelevant evidence"}),
                    tool_call_id="search-mixed",
                ),
                ToolCallPart(
                    "confirm_item_deletion",
                    "{}",
                    tool_call_id="confirm-mixed",
                ),
            ]
        )

    settings = replace(
        Settings(),
        agent_timeout_seconds=2,
    )
    services = AgentActionServices(
        submission=FakeSubmission(BatchSaveResult(())),
        pending=MixedPending(),
        management=object(),  # the fake pending owns the canonical outcome
    )
    runtime = KnowledgeAgent(
        FunctionModel(model),
        settings,
        lambda _request: knowledge,
        action_factory=lambda _request: services,
    )
    result = await runtime.run(_request("确认删除"))

    assert result.answer.status == "ok"
    assert result.answer.error_code == "items_deleted"
    assert result.answer.citations == []
    assert result.answer.action_results == list(rows)
    assert "已移入回收站" in result.answer.text
    assert "已恢复，未重复删除" in result.answer.text
    assert "此前已在回收站" in result.answer.text
    assert knowledge.calls == ["irrelevant evidence"]


@pytest.mark.asyncio
async def test_link_content_question_uses_search_not_write_tool():
    citation = Citation(
        item_id=2,
        segment_id=3,
        title="source",
        excerpt="evidence",
        url="https://youtu.be/dQw4w9WgXcQ?t=42",
    )
    knowledge = FakeKnowledgeServices([citation])
    submission = FakeSubmission(BatchSaveResult(()))
    runtime = _runtime(
        TestModel(
            call_tools=["search_segments"],
            custom_output_text="answer [S3]",
        ),
        submission,
        FakePending(),
        knowledge=knowledge,
        composer_model=_composer_for(3),
    )

    result = await runtime.run(
        _request("这个链接讲了什么 https://youtu.be/dQw4w9WgXcQ")
    )

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert submission.calls == []
    assert knowledge.calls == ["search_segments"]
