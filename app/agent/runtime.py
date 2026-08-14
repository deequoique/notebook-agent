"""PydanticAI runtime with fixed tenant dependencies and evidence enforcement."""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Annotated, Callable, Literal

from pydantic_ai import (
    Agent,
    PromptedOutput,
    RunContext,
    UsageLimits,
    capture_run_messages,
)
from pydantic_ai.exceptions import (
    ModelHTTPError,
    ModelRetry,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage
from pydantic import Field

from app.agent.actions import (
    ActionInputMismatch,
    AgentActionRuntime,
    AgentActionServices,
)
from app.agent.answer_validation import (
    NaturalAnswerValidationError,
    validate_natural_answer,
)
from app.agent.autonomy import (
    ErrorEnvelope,
    RecoveryGrant,
    RecoveryLedger,
    RecoveryPolicy,
    TodoValidationError,
    TurnTodoStore,
)
from app.agent.context import TurnContext
from app.agent.limits import SEARCH_RESULT_LIMIT
from app.agent.provider import composer_model_settings
from app.agent.services import (
    EmbeddingUnavailable,
    KnowledgeNotFound,
    KnowledgeServices,
    RetrievalUnavailable,
)
from app.agent.types import (
    AgentAnswer,
    AgentRequest,
    AnswerDraft,
    Citation,
    RetrievalToolPayload,
)
from app.config import COMPOSER_VALIDATION_REQUEST_LIMIT, Settings
from app.diagnostics import RequestDiagnostics, classify_usage_limit
from app.ingest.submission import normalize_item_reference, parse_message_references


INSTRUCTIONS = """
你是私有知识库检索助手。你只能根据本次会话中的知识库工具结果回答。

规则：
1. 回答知识库事实前必须调用 search_segments；可以改写查询并再次搜索。
2. 需要上下文时调用 get_neighbors，需要元数据时调用 get_item，最终引用位置可调用 open_at。
3. 不得依据模型记忆补写。没有结果或证据不足时，明确说“知识库中未找到足够证据”。
4. 你的本阶段职责是收集可信证据；收集充分后停止调用工具。最终总结、引用编号和来源由服务器的独立回答阶段生成。
5. 工具没有 user_id 参数；不要询问、猜测或尝试改变当前用户。
6. 明确要求保存且消息中有视频 URL 时调用 save_videos；只有裸 URL
   时调用 request_save_confirmation。
7. 用户确认或取消待保存视频时分别调用 confirm_video_save 或
   cancel_video_save；不要从历史文本重建 URL。
8. 询问链接内容不代表保存意图，仍按知识检索规则处理。
9. 多来源问题先比较 search_segments 返回的 excerpt、标题和来源；不要为每一个候选机械展开。
   同一视频的不同时间证据可保留，但不要编造章节标题。
10. “我存了什么/库存/回收站”只能调用 list_saved_items 或 get_saved_item；
    “修改收藏原因”调用 update_saved_item；删除必须先 delete_saved_items 再等待明确确认，
    确认/取消/含糊分别调用 confirm_item_deletion/cancel_item_deletion/clarify_item_deletion；
    恢复调用 restore_saved_items，失败重试调用 retry_item_ingestion。管理工具结果是终止结果，
    不调用 search_segments，也不要把模糊标题直接当成 item_id。
""".strip()


BOUNDED_AUTONOMY_INSTRUCTIONS = """
你是私有知识库助手，由一个有界的主 Agent 处理当前消息。

规则：
1. 问候、感谢、能力询问、澄清和不需要私有知识的普通交流可以直接自然回答，
   不要为了形式调用知识工具，也不要伪造 Citation、来源区块或 URL。
2. 需要私有知识时自行选择合适的知识工具；只有成功的本轮检索证据可以支持知识事实。
   成功检索后，最终回答必须在相关句子中使用本轮工具返回的精确 [S<segment_id>] 标记。
   不得猜测或复用历史 Citation ID，不得输出 URL 或服务器来源区块。
3. 缺少可信指代或信息时先自然询问澄清，不要猜测条目、租户、作用域或工具参数。
4. 只有存在多个相互依赖的短步骤时才使用 todo_write；普通对话和单工具请求不要创建 Todo。
   Todo 只是本轮工作记忆，不代表工具成功、授权或副作用结果。
5. 读工具可以按需组合；保存、删除、确认、取消和其他副作用工具仍由服务器结果决定，
   不要用自己的 prose 声称操作成功。不要调用任何未提供的工具。
6. 工具没有 user_id、tenant、thread、pending 或 claim 参数；不要询问、猜测或改变当前用户。
7. 明确要求保存且消息中有视频 URL 时调用 save_videos；只有裸 URL 时调用 request_save_confirmation。
   询问链接内容不代表保存意图，仍须按知识检索规则处理。
8. “我存了什么/库存/回收站”只能调用库存读取工具；删除必须先发起服务器确认，
   不能把模糊标题直接当成 item_id。确认工具只在可信待确认状态存在时可用。
9. 读工具返回 error 时只能选择返回中的 recovery.allowed 动作；retry_same_read
   必须完全重复失败的工具调用，不能修改参数。空搜索不是错误；只有真正改写查询
   且返回允许 reformulate_search 时才能再次搜索。provider 或 mutation 失败不能自行重试。
""".strip()


NORMAL_RETRIEVAL_CALLS_LIMIT = 5
NORMAL_SEARCH_CALLS_LIMIT = 2
NORMAL_EXPANSION_CALLS_LIMIT = 3
MAX_SOURCE_ITEMS = 5
ANSWER_REQUEST_LIMIT = COMPOSER_VALIDATION_REQUEST_LIMIT
COMPOSER_EVIDENCE_EXCERPT_CHARS = 360
COMPRESSED_EVIDENCE_LIMIT = 8
COMPRESSED_EVIDENCE_EXCERPT_CHARS = 180
FALLBACK_INTRO = "自动总结未完成，以下是知识库中最相关的证据："
BOUNDED_UNAVAILABLE_REMAINDER = "后续读取暂时不可用，未完成的部分不会被臆测。"


_EXPLICIT_SAVE_PATTERNS = (
    re.compile(
        r"(?:^|帮我|替我|给我|请.{0,12}|我(?:想|要|希望).{0,4})"
        r"(?:保存|收藏|存入|加入(?:我的)?知识库)"
    ),
    re.compile(
        r"(?:^|\bplease\s+|\bcan\s+you\s+|\bi\s+(?:want|need)\s+to\s+)"
        r"(?:save|bookmark|add\s+(?:this\s+)?to\s+(?:my\s+)?(?:library|knowledge\s+base))\b",
        re.IGNORECASE,
    ),
)
_NEGATED_SAVE_PATTERN = re.compile(
    r"(?:不要|别|不用|无需|不想|不需要).{0,6}(?:保存|收藏|存入|加入)"
    r"|\b(?:do\s+not|don't|dont|no\s+need\s+to)\s+(?:save|bookmark|add)\b",
    re.IGNORECASE,
)


def _explicit_save_requested(semantic_text: str) -> bool:
    """Conservatively identify a current-message save command.

    Exact-reference content questions hide every write/pending tool.  A
    positive current-message command is required before ``save_videos`` is
    exposed, so stale history cannot turn a question into a terminal action.
    """

    text = semantic_text.strip()
    if not text or _NEGATED_SAVE_PATTERN.search(text):
        return False
    return any(pattern.search(text) for pattern in _EXPLICIT_SAVE_PATTERNS)


def _is_clarification_question(text: str) -> bool:
    """Require a question shape without semantically routing model prose."""

    return isinstance(text, str) and bool(text.strip()) and (
        "?" in text or "？" in text
    )


def _allow_blocked_todo_clarification(
    request: AgentRequest,
    deps: AgentDeps,
    natural_text: str,
) -> bool:
    """Allow blocked Todo finalization only for trusted missing-context traces."""

    store = deps.todo_store
    if store is None or not store.snapshot.items:
        return False
    if store.snapshot.unfinished:
        return False
    if not any(item.status == "blocked" for item in store.snapshot.items):
        return False
    # No knowledge/action read may have supplied evidence for this turn.  A
    # current explicit URL also requires deterministic retrieval and cannot
    # use this clarification escape hatch.
    if deps.search_calls or deps.citations or deps.actions.read_action_results:
        return False
    if deps.reference_scope:
        return False
    if deps.context.recent_inventory:
        return False
    return _is_clarification_question(natural_text)


def _citation_matches_scope(
    citation: Citation,
    scope: tuple[tuple[str, str], ...],
) -> bool:
    """Validate a citation URL against the current-message subject set.

    Every citation must be normalizable through the submission contract and
    match exactly; an article or malformed citation therefore fails closed.
    A stale model result cannot be smuggled into the trusted Citation cache.
    """

    try:
        reference = normalize_item_reference(citation.url)
    except (ValueError, TypeError):
        return False
    return (reference.platform, reference.platform_id) in set(scope)


def _item_details_matches_scope(
    result: dict, scope: tuple[tuple[str, str], ...]
) -> bool:
    try:
        reference = normalize_item_reference(str(result.get("url", "")))
    except (ValueError, TypeError):
        return False
    return (
        result.get("platform") == reference.platform
        and (reference.platform, reference.platform_id) in set(scope)
    )


class RetrievalKind(str, Enum):
    SEARCH = "search"
    EXPANSION = "expansion"


class ReservationResult(str, Enum):
    EXECUTE = "execute"
    SAME_STEP_SKIPPED = "same_step_skipped"
    STAGE_BUDGET_EXHAUSTED = "stage_budget_exhausted"


@dataclass(frozen=True)
class _RecoveryPayload:
    """Internal marker for a safe read failure returned to the model."""

    payload: dict


class _TransientReadUnavailable(RuntimeError):
    """Private signal for an expected read failure with no model-visible detail."""


@dataclass
class AgentDeps:
    services: KnowledgeServices
    actions: AgentActionRuntime
    search_calls: int = 0
    retrieval_calls: int = 0
    expansion_calls: int = 0
    tool_calls: int = 0
    citations: dict[int, Citation] = field(default_factory=dict)
    last_retrieval_run_step: int | None = None
    diagnostics: RequestDiagnostics | None = None
    # Non-empty only when the current message contains supported URLs plus
    # semantic text.  It is server-owned and never model-authored.
    reference_scope: tuple[tuple[str, str], ...] = ()
    reference_save_requested: bool = False
    # Present only on the opt-in bounded-autonomy path.  It is intentionally
    # turn-local and never copied into AgentRequest/history or diagnostics.
    todo_store: TurnTodoStore | None = None
    # Trusted, bounded prior-turn focus.  Item ids in this projection are
    # references only; KnowledgeServices repeats tenant/state checks.
    context: TurnContext = field(default_factory=TurnContext)
    invalid_item_scope_attempt: bool = False
    recovery_ledger: RecoveryLedger | None = None
    recovery_policy: RecoveryPolicy | None = None
    # Pending transient reads are keyed only by a server-side digest.  The
    # model never receives this state or the underlying tool arguments.
    pending_read_failures: dict[str, str] = field(default_factory=dict)
    last_empty_search_fingerprint: str | None = None
    read_recovery_exhausted: bool = False
    todo_used: bool = False
    _tool_lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve_retrieval(
        self, *, run_step: int, kind: RetrievalKind
    ) -> ReservationResult:
        """Atomically reserve the only backend retrieval allowed in a step."""

        with self._tool_lock:
            if self.last_retrieval_run_step == run_step:
                return ReservationResult.SAME_STEP_SKIPPED
            if self.retrieval_calls >= NORMAL_RETRIEVAL_CALLS_LIMIT:
                return ReservationResult.STAGE_BUDGET_EXHAUSTED
            if kind is RetrievalKind.SEARCH:
                if self.search_calls >= NORMAL_SEARCH_CALLS_LIMIT:
                    return ReservationResult.STAGE_BUDGET_EXHAUSTED
                self.search_calls += 1
            else:
                if self.expansion_calls >= NORMAL_EXPANSION_CALLS_LIMIT:
                    return ReservationResult.STAGE_BUDGET_EXHAUSTED
                self.expansion_calls += 1
            self.retrieval_calls += 1
            self.last_retrieval_run_step = run_step
            return ReservationResult.EXECUTE

    def record(self, values: list[Citation] | Citation) -> None:
        rows = values if isinstance(values, list) else [values]
        for citation in rows:
            if self.reference_scope and not _citation_matches_scope(
                citation,
                self.reference_scope,
            ):
                continue
            self.citations[citation.segment_id] = citation

    def tool_event(self, name: str, outcome: str, call_index: int, result_count: int | None = None,
                   exception: BaseException | None = None) -> None:
        if self.diagnostics is not None:
            self.diagnostics.event(
                "tool_call", tool_name=name, tool_outcome=outcome,
                call_index=call_index, result_count=result_count, exception=exception,
                agent_phase="retrieval",
            )


@dataclass
class ComposerDeps:
    """Trusted allow-list passed to the tool-free answer composer only."""

    citations: dict[int, Citation]
    excerpt_chars: int = COMPOSER_EVIDENCE_EXCERPT_CHARS
    diagnostics: RequestDiagnostics | None = None
    invalid_draft_count: int = 0


@dataclass(frozen=True)
class AgentExecution:
    answer: AgentAnswer
    new_messages: list[ModelMessage]


def build_agent(
    model: Model | str,
    *,
    tool_timeout: float = 15.0,
    management_enabled: bool = False,
    bounded_autonomy_enabled: bool = False,
) -> Agent[AgentDeps, str]:
    def normal_retrieval_available(deps: AgentDeps) -> bool:
        if deps.retrieval_calls >= NORMAL_RETRIEVAL_CALLS_LIMIT:
            return False
        return (
            deps.search_calls < NORMAL_SEARCH_CALLS_LIMIT
            or (bool(deps.citations) and deps.expansion_calls < NORMAL_EXPANSION_CALLS_LIMIT)
        )

    def prepare_search(ctx: RunContext[AgentDeps], tool_def):
        deps = ctx.deps
        if bounded_autonomy_enabled and any(
            tool_name == "search_segments"
            for tool_name in deps.pending_read_failures.values()
        ):
            # Keep the tool visible long enough for the server to return a
            # safe denied/exhausted envelope on a repeated failed call.  The
            # read helper still prevents any third backend attempt.
            return tool_def
        if (
            normal_retrieval_available(deps)
            and deps.search_calls < NORMAL_SEARCH_CALLS_LIMIT
        ):
            return tool_def
        return None

    def prepare_expansion(ctx: RunContext[AgentDeps], tool_def):
        deps = ctx.deps
        if (
            deps.citations
            and normal_retrieval_available(deps)
            and deps.expansion_calls < NORMAL_EXPANSION_CALLS_LIMIT
        ):
            return tool_def
        return None

    def prepare_management(ctx: RunContext[AgentDeps], tool_def):
        """Hide inventory/item-management tools for explicit URL questions."""

        if ctx.deps.reference_scope or (
            bounded_autonomy_enabled and not ctx.deps.actions.management_enabled
        ):
            return None
        return tool_def

    def prepare_pending_delete(ctx: RunContext[AgentDeps], tool_def):
        """Expose delete decision tools only for a trusted pending delete."""

        if ctx.deps.reference_scope:
            return None
        if bounded_autonomy_enabled:
            snapshot = ctx.deps.actions.pending_delete_snapshot()
            if snapshot is None or not getattr(snapshot, "active", False):
                return None
        return tool_def

    def prepare_bare_url_action(ctx: RunContext[AgentDeps], tool_def):
        """Bare URLs are handled before the model; scoped prompts cannot use it."""

        if bounded_autonomy_enabled:
            return None
        if ctx.deps.reference_scope:
            return None
        return tool_def

    def prepare_save(ctx: RunContext[AgentDeps], tool_def):
        """Require an explicit current-message save command for scoped URLs."""

        if bounded_autonomy_enabled:
            if not ctx.deps.actions.save_enabled:
                return None
            if not ctx.deps.reference_scope or not ctx.deps.reference_save_requested:
                return None
        elif ctx.deps.reference_scope and not ctx.deps.reference_save_requested:
            return None
        return tool_def

    def prepare_pending_save(ctx: RunContext[AgentDeps], tool_def):
        """An explicit URL question cannot consume or alter an older pending save."""

        if ctx.deps.reference_scope:
            return None
        if bounded_autonomy_enabled:
            if not ctx.deps.actions.save_enabled:
                return None
            if not ctx.deps.actions.pending_save_snapshot().active:
                return None
        return tool_def

    def execute_tool(deps: AgentDeps, name: str, operation):
        """Record only the tool boundary, never its arguments or output."""
        with deps._tool_lock:
            deps.tool_calls += 1
            call_index = deps.tool_calls
        deps.tool_event(name, "started", call_index)
        try:
            return operation(), call_index
        except Exception as exc:
            deps.tool_event(name, "failed", call_index, exception=exc)
            raise

    def optional_knowledge(operation):
        """Convert an expected scoped miss into an empty successful lookup."""

        try:
            return operation()
        except KnowledgeNotFound:
            return None

    def _recovery_diagnostic(
        deps: AgentDeps,
        *,
        error: ErrorEnvelope,
        grant: RecoveryGrant,
        action: str | None = None,
        outcome: str = "granted",
    ) -> None:
        if deps.diagnostics is None:
            return
        deps.diagnostics.event(
            "recovery",
            error_code=error.code,
            error_category=error.category,
            recovery_action=action,
            recovery_outcome=outcome,
            recovery_count=grant.remaining_actions,
            agent_phase="retrieval" if error.operation == "read" else "answer",
        )

    def _recovery_payload(error: ErrorEnvelope, grant: RecoveryGrant) -> _RecoveryPayload:
        """Project only the allow-listed envelope/grant fields to the model."""

        return _RecoveryPayload(
            {
                "status": "error",
                "error": {
                    "category": error.category,
                    "code": error.code,
                    "operation": error.operation,
                    "safe_message": error.safe_message,
                    "partial_evidence": error.partial_evidence,
                },
                "recovery": {
                    "allowed": list(grant.allowed),
                    "remaining_actions": grant.remaining_actions,
                },
            }
        )

    def _read_failure(
        ctx: RunContext[AgentDeps],
        *,
        tool_name: str,
        arguments: dict,
        operation,
    ) -> tuple[object, int] | _RecoveryPayload:
        """Execute one bounded read, granting at most one exact retry."""

        deps = ctx.deps
        if not bounded_autonomy_enabled or deps.recovery_ledger is None or deps.recovery_policy is None:
            # This helper is only used by flag-on tool definitions.  The
            # defensive branch keeps alternate callers fail-closed.
            value, call_index = execute_tool(deps, tool_name, operation)
            return value, call_index
        ledger = deps.recovery_ledger
        policy = deps.recovery_policy
        fingerprint = ledger.fingerprint_read(tool_name, arguments)
        pending_tool = deps.pending_read_failures.get(fingerprint)
        pending_same_tool = any(
            value == tool_name for value in deps.pending_read_failures.values()
        )
        retrying = pending_tool == tool_name
        if pending_same_tool and not retrying:
            error = ErrorEnvelope.from_category(
                "read_unavailable",
                operation="read",
                code="retry_not_allowed",
                partial_evidence=bool(deps.citations),
            )
            grant = policy.grant(
                error,
                has_evidence=bool(deps.citations),
                read_fingerprint=fingerprint,
                retrieval_budget_remaining=(
                    NORMAL_RETRIEVAL_CALLS_LIMIT - deps.retrieval_calls
                ),
            )
            # A different argument set is never an exact retry of the failed
            # call.  Do not let the model turn a transient error into a free
            # alternate read.
            grant = RecoveryGrant(
                tuple(
                    action
                    for action in grant.allowed
                    if action != "retry_same_read"
                ),
                grant.remaining_actions,
            )
            _recovery_diagnostic(
                deps, error=error, grant=grant, outcome="denied"
            )
            return _recovery_payload(error, grant)

        if retrying:
            error = ErrorEnvelope.from_category(
                "transient_read",
                operation="read",
                partial_evidence=bool(deps.citations),
            )
            grant = policy.grant(
                error,
                has_evidence=bool(deps.citations),
                read_fingerprint=fingerprint,
                retrieval_budget_remaining=(
                    NORMAL_RETRIEVAL_CALLS_LIMIT - deps.retrieval_calls
                ),
            )
            if not grant.permits("retry_same_read") or not ledger.record_same_read_retry(
                fingerprint
            ):
                deps.read_recovery_exhausted = True
                _recovery_diagnostic(
                    deps, error=error, grant=grant, outcome="exhausted"
                )
                return _recovery_payload(error, grant)
            _recovery_diagnostic(
                deps,
                error=error,
                grant=grant,
                action="retry_same_read",
                outcome="consumed",
            )

        try:
            value, call_index = execute_tool(deps, tool_name, operation)
        except (EmbeddingUnavailable, RetrievalUnavailable, _TransientReadUnavailable):
            # Raw exception classes/details remain server-side.  The failed
            # fingerprint is retained only in memory to bind the next retry.
            deps.pending_read_failures[fingerprint] = tool_name
            error = ErrorEnvelope.from_category(
                "transient_read",
                operation="read",
                partial_evidence=bool(deps.citations),
            )
            grant = policy.grant(
                error,
                has_evidence=bool(deps.citations),
                read_fingerprint=fingerprint,
                retrieval_budget_remaining=(
                    NORMAL_RETRIEVAL_CALLS_LIMIT - deps.retrieval_calls
                ),
            )
            if retrying and not grant.permits("retry_same_read"):
                deps.read_recovery_exhausted = True
            _recovery_diagnostic(deps, error=error, grant=grant)
            return _recovery_payload(error, grant)
        else:
            if retrying:
                deps.pending_read_failures.pop(fingerprint, None)
            return value, call_index

    def skipped_payload(
        ctx: RunContext[AgentDeps], name: str, kind: RetrievalKind
    ) -> RetrievalToolPayload | None:
        """Reserve a retrieval or provide a truthful no-side-effect result."""

        reservation = ctx.deps.reserve_retrieval(
            run_step=ctx.run_step,
            kind=kind,
        )
        if reservation is ReservationResult.EXECUTE:
            return None
        with ctx.deps._tool_lock:
            ctx.deps.tool_calls += 1
            call_index = ctx.deps.tool_calls
        reason: Literal["same_model_step", "budget_exhausted"]
        reason = (
            "same_model_step"
            if reservation is ReservationResult.SAME_STEP_SKIPPED
            else "budget_exhausted"
        )
        ctx.deps.tool_event(name, "skipped", call_index, 0)
        return {"status": "skipped", "evidence": [], "reason": reason}

    agent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=str,
        instructions=(
            BOUNDED_AUTONOMY_INSTRUCTIONS
            if bounded_autonomy_enabled
            else INSTRUCTIONS
        ),
        retries={"tools": 1},
        tool_timeout=tool_timeout,
    )

    if bounded_autonomy_enabled:

        @agent.instructions
        def bounded_context_instruction(ctx: RunContext[AgentDeps]) -> str:
            """Expose only safe prior-turn focus and inventory references."""

            context = ctx.deps.context
            rows: list[str] = []
            if context.recent_inventory:
                rows.append(
                    "近期库存参考（仅用于解析用户说的‘第几个’；item_id 是引用，不是授权，"
                    "使用前仍须调用工具确认）："
                )
                rows.extend(
                    f"{item.ordinal}. 《{item.title}》 (item_id={item.item_id})"
                    for item in context.recent_inventory
                )
            if context.recent_sources:
                rows.append(
                    "近期来源焦点仅用于理解对话；历史 segment_id 不是本轮证据，"
                    "没有本轮检索返回就绝不能写入 [S<id>] 引用："
                )
                rows.extend(
                    f"item_id={source.item_id}, segment_id={source.segment_id}, 《{source.title}》"
                    for source in context.recent_sources
                )
            return "\n".join(rows)

        @agent.tool
        def todo_write(
            ctx: RunContext[AgentDeps], items: list[dict[str, str]]
        ) -> dict:
            """Replace the current turn's short, non-authoritative Todo."""

            if ctx.deps.todo_store is None:
                # This should be unreachable because the tool is registered
                # only for the flag-on Agent, but fail closed if a caller
                # constructs incompatible dependencies.
                raise ModelRetry("Todo is unavailable for this turn")
            if not ctx.deps.todo_used:
                ctx.deps.todo_used = True
                if ctx.deps.diagnostics is not None:
                    ctx.deps.diagnostics.event(
                        "todo_used",
                        todo_used=True,
                        agent_phase="retrieval",
                    )
            try:
                snapshot, call_index = execute_tool(
                    ctx.deps,
                    "todo_write",
                    lambda: ctx.deps.todo_store.write({"items": items}),
                )
            except TodoValidationError:
                # Do not expose validator details or model-supplied fields in
                # a retry prompt.  The attempted call has already consumed the
                # ordinary tool-call budget through execute_tool().
                raise ModelRetry(
                    "Use at most six short turn-local steps with unique ids, "
                    "one in_progress step, and only pending/in_progress/completed/blocked states."
                ) from None
            ctx.deps.tool_event(
                "todo_write", "succeeded", call_index, len(snapshot.items)
            )
            return {
                "items": [
                    {"id": item.id, "title": item.title, "status": item.status}
                    for item in snapshot.items
                ]
            }

    @agent.instructions
    def retrieval_convergence_instruction(ctx: RunContext[AgentDeps]) -> str:
        """State the server-enforced retrieval phase without exposing internals."""

        deps = ctx.deps
        if not normal_retrieval_available(deps):
            return (
                "检索轮次已经结束。只能根据已返回的工具证据作答；"
                "若证据不足，明确说明证据不足，不要继续检索或依据模型记忆补写。"
            )
        return (
            "优先基于已有证据作答。搜索结果是待比较的候选，不要为每个候选机械展开；"
            "仅在上下文确实不足时选择最有希望的代表片段扩展。"
        )

    @agent.instructions
    def pending_save_instruction(ctx: RunContext[AgentDeps]) -> str:
        """Inject only the current run's server-verified confirmation state."""

        if ctx.deps.reference_scope:
            return ""
        snapshot = ctx.deps.actions.pending_save_snapshot()
        if not snapshot.active:
            return ""
        return (
            "可信服务器状态：当前 conversation 有 "
            f"{snapshot.count} 个视频等待保存确认。\n"
            "把本条短回复作为确认语义判断：明确肯定（包括“需要”）调用 "
            "confirm_video_save；明确否定调用 cancel_video_save；含糊则调用 "
            "clarify_save_confirmation；"
            "明显无关的新问题按正常知识流程处理并保留待确认状态。"
            "不要为确认回复调用知识检索。"
        )

    @agent.instructions
    def pending_delete_instruction(ctx: RunContext[AgentDeps]) -> str:
        """Expose only count/kind for a trusted pending delete action."""

        if ctx.deps.reference_scope:
            return ""
        snapshot = ctx.deps.actions.pending_delete_snapshot()
        if snapshot is None or not snapshot.active:
            return ""
        return (
            "可信服务器状态：当前 conversation 有 "
            f"{snapshot.count} 个条目等待删除确认。明确肯定调用 confirm_item_deletion；"
            "明确否定调用 cancel_item_deletion；含糊调用 clarify_item_deletion。"
            "不要从模型历史重建删除目标。"
            + "当前用户消息必须包含服务端显示的确认码，否则请先重新发起删除，"
            "不要猜测、复用旧确认或仅回复‘是/确认’。"
        )

    def _run_search_segments(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = SEARCH_RESULT_LIMIT,
        item_id: int | None = None,
    ) -> RetrievalToolPayload:
        """Execute a tenant-bound search, optionally narrowed to an observation."""

        context_item_ids = set(ctx.deps.context.inventory_item_ids)
        if (
            item_id is not None
            and not ctx.deps.actions.is_observed_item(item_id)
            and item_id not in context_item_ids
        ):
            # The model may only use an item reference returned by a successful
            # current-run inventory/detail observation or a recent trusted
            # inventory context reference.  No backend call is attempted for
            # an unobserved or forged ID.
            ctx.deps.invalid_item_scope_attempt = True
            raise ModelRetry(
                "先从本轮成功返回的知识库条目中选择一个条目，再进行限定检索。"
            )
        normalized_query = query.strip()
        arguments = {
            "query": normalized_query,
            "limit": int(limit),
            "item_id": item_id,
        }
        fingerprint = (
            ctx.deps.recovery_ledger.fingerprint_read(
                "search_segments", arguments
            )
            if bounded_autonomy_enabled and ctx.deps.recovery_ledger is not None
            else None
        )
        if bounded_autonomy_enabled and fingerprint is not None:
            if fingerprint == ctx.deps.last_empty_search_fingerprint:
                # Repeating an empty query is still the same observation, not
                # a reformulation and not a backend call.
                return {
                    "status": "ok",
                    "evidence": [],
                    "reason": None,
                }
            if ctx.deps.last_empty_search_fingerprint is not None:
                policy = ctx.deps.recovery_policy
                ledger = ctx.deps.recovery_ledger
                grant = (
                    policy.grant_for_empty_search(
                        search_budget_remaining=(
                            NORMAL_SEARCH_CALLS_LIMIT - ctx.deps.search_calls
                        ),
                        retrieval_budget_remaining=(
                            NORMAL_RETRIEVAL_CALLS_LIMIT - ctx.deps.retrieval_calls
                        ),
                    )
                    if policy is not None
                    else RecoveryGrant((), 0)
                )
                if not grant.permits("reformulate_search") or ledger is None or not ledger.record_recovery(
                    "reformulate_search", category="transient_read"
                ):
                    ctx.deps.read_recovery_exhausted = True
                    if policy is not None:
                        error = ErrorEnvelope.from_category(
                            "read_unavailable",
                            operation="read",
                            code="retry_not_allowed",
                            partial_evidence=bool(ctx.deps.citations),
                        )
                        _recovery_diagnostic(
                            ctx.deps,
                            error=error,
                            grant=grant,
                            outcome="exhausted",
                        )
                    return {
                        "status": "ok",
                        "evidence": [],
                        "reason": None,
                    }
                error = ErrorEnvelope.from_category(
                    "transient_read",
                    operation="read",
                    partial_evidence=bool(ctx.deps.citations),
                )
                _recovery_diagnostic(
                    ctx.deps,
                    error=error,
                    grant=grant,
                    action="reformulate_search",
                    outcome="consumed",
                )

        pending_same = (
            fingerprint is not None
            and fingerprint in ctx.deps.pending_read_failures
        )
        retry_already_used = bool(
            pending_same
            and ctx.deps.recovery_ledger is not None
            and ctx.deps.recovery_ledger.same_read_retry_count(fingerprint) >= 1
        )
        pending_other = (
            bounded_autonomy_enabled
            and any(
                tool_name == "search_segments"
                for tool_name in ctx.deps.pending_read_failures.values()
            )
            and not pending_same
        )
        if not (retry_already_used or pending_other):
            if skipped := skipped_payload(ctx, "search_segments", RetrievalKind.SEARCH):
                return skipped

        if item_id is None:
            operation = lambda: ctx.deps.services.search_segments(
                normalized_query, limit=limit
            )
        else:
            operation = lambda: ctx.deps.services.search_segments(
                normalized_query, limit=limit, item_id=item_id
            )
        read_result = _read_failure(
            ctx,
            tool_name="search_segments",
            arguments=arguments,
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        citations, call_index = read_result
        ctx.deps.record(citations)
        citations = [
            citation
            for citation in citations
            if not ctx.deps.reference_scope
            or _citation_matches_scope(
                citation,
                ctx.deps.reference_scope,
            )
        ]
        ctx.deps.tool_event("search_segments", "succeeded", call_index, len(citations))
        ctx.deps.diagnostics and ctx.deps.diagnostics.retrieval_detail(tool_name="search_segments", call_index=call_index, query=query, limit=limit)
        for citation in citations:
            ctx.deps.diagnostics and ctx.deps.diagnostics.retrieval_detail(tool_name="search_segments", call_index=call_index, query=query, limit=limit, item_id=citation.item_id, segment_id=citation.segment_id, title=citation.title, url=citation.url, excerpt=citation.excerpt, score=getattr(citation, "_retrieval_score", None), start=citation.start_sec)
        if bounded_autonomy_enabled and ctx.deps.recovery_ledger is not None:
            if citations:
                ctx.deps.last_empty_search_fingerprint = None
            elif fingerprint is not None:
                ctx.deps.last_empty_search_fingerprint = fingerprint
        return {
            "status": "ok",
            "evidence": [value.model_dump() for value in citations],
            "reason": None,
        }

    def _run_management_read(
        ctx: RunContext[AgentDeps],
        *,
        tool_name: str,
        arguments: dict,
        operation,
    ) -> dict:
        """Return a composable read observation or a typed recovery envelope."""

        def checked_operation():
            outcome = operation()
            if outcome.status == "failed" and outcome.error_code == "management_failed":
                raise _TransientReadUnavailable()
            return outcome

        read_result = _read_failure(
            ctx,
            tool_name=tool_name,
            arguments=arguments,
            operation=checked_operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        outcome, call_index = read_result
        ctx.deps.tool_event(
            tool_name,
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    if bounded_autonomy_enabled:

        @agent.tool(prepare=prepare_search)
        def search_segments(
            ctx: RunContext[AgentDeps],
            query: str,
            limit: int = SEARCH_RESULT_LIMIT,
            item_id: Annotated[int | None, Field(gt=0)] = None,
        ) -> dict:
            """Search globally or within a trusted current-run item reference."""

            return _run_search_segments(ctx, query, limit, item_id)

    else:

        @agent.tool(prepare=prepare_search)
        def search_segments(
            ctx: RunContext[AgentDeps], query: str, limit: int = SEARCH_RESULT_LIMIT
        ) -> RetrievalToolPayload:
            """Search the current user's knowledge segments for evidence."""

            if skipped := skipped_payload(ctx, "search_segments", RetrievalKind.SEARCH):
                return skipped
            citations, call_index = execute_tool(
                ctx.deps,
                "search_segments",
                lambda: ctx.deps.services.search_segments(query, limit=limit),
            )
            ctx.deps.record(citations)
            citations = [
                citation
                for citation in citations
                if not ctx.deps.reference_scope
                or _citation_matches_scope(citation, ctx.deps.reference_scope)
            ]
            ctx.deps.tool_event(
                "search_segments", "succeeded", call_index, len(citations)
            )
            ctx.deps.diagnostics and ctx.deps.diagnostics.retrieval_detail(
                tool_name="search_segments",
                call_index=call_index,
                query=query,
                limit=limit,
            )
            for citation in citations:
                ctx.deps.diagnostics and ctx.deps.diagnostics.retrieval_detail(
                    tool_name="search_segments",
                    call_index=call_index,
                    query=query,
                    limit=limit,
                    item_id=citation.item_id,
                    segment_id=citation.segment_id,
                    title=citation.title,
                    url=citation.url,
                    excerpt=citation.excerpt,
                    score=getattr(citation, "_retrieval_score", None),
                    start=citation.start_sec,
                )
            return {
                "status": "ok",
                "evidence": [value.model_dump() for value in citations],
                "reason": None,
            }

    @agent.tool(prepare=prepare_expansion)
    def get_neighbors(
        ctx: RunContext[AgentDeps], segment_id: int, radius: int = 1
    ) -> dict:
        """Read nearby segments around a search result."""

        if skipped := skipped_payload(ctx, "get_neighbors", RetrievalKind.EXPANSION):
            return skipped
        operation = lambda: optional_knowledge(
            lambda: ctx.deps.services.get_neighbors(segment_id, radius=radius)
        )
        if bounded_autonomy_enabled:
            read_result = _read_failure(
                ctx,
                tool_name="get_neighbors",
                arguments={"segment_id": int(segment_id), "radius": int(radius)},
                operation=operation,
            )
            if isinstance(read_result, _RecoveryPayload):
                return read_result.payload
            citations, call_index = read_result
        else:
            citations, call_index = execute_tool(
                ctx.deps, "get_neighbors", operation
            )
        citations = citations or []
        citations = [
            citation
            for citation in citations
            if not ctx.deps.reference_scope
            or _citation_matches_scope(
                citation,
                ctx.deps.reference_scope,
            )
        ]
        ctx.deps.record(citations)
        ctx.deps.tool_event("get_neighbors", "succeeded", call_index, len(citations))
        for citation in citations:
            ctx.deps.diagnostics and ctx.deps.diagnostics.retrieval_detail(tool_name="get_neighbors", call_index=call_index, segment_id=citation.segment_id, radius=radius, item_id=citation.item_id, title=citation.title, url=citation.url, excerpt=citation.excerpt, start=citation.start_sec)
        return {
            "status": "ok",
            "evidence": [value.model_dump() for value in citations],
            "reason": None,
        }

    @agent.tool(prepare=prepare_expansion)
    def get_item(ctx: RunContext[AgentDeps], item_id: int) -> dict:
        """Read metadata for a knowledge item returned by search."""

        if skipped := skipped_payload(ctx, "get_item", RetrievalKind.EXPANSION):
            return skipped
        operation = lambda: optional_knowledge(
            lambda: ctx.deps.services.get_item(item_id)
        )
        if bounded_autonomy_enabled:
            read_result = _read_failure(
                ctx,
                tool_name="get_item",
                arguments={"item_id": int(item_id)},
                operation=operation,
            )
            if isinstance(read_result, _RecoveryPayload):
                return read_result.payload
            details, call_index = read_result
        else:
            details, call_index = execute_tool(ctx.deps, "get_item", operation)
        result = asdict(details) if details is not None else {}
        if ctx.deps.reference_scope and result and not _item_details_matches_scope(
            result, ctx.deps.reference_scope
        ):
            result = {}
        ctx.deps.tool_event("get_item", "succeeded", call_index, 1 if result else 0)
        ctx.deps.diagnostics and ctx.deps.diagnostics.retrieval_detail(tool_name="get_item", call_index=call_index, item_id=item_id, title=result.get("title"), author=result.get("author"), description=result.get("description"), url=result.get("url"))
        return {"status": "ok", "evidence": [result] if result else [], "reason": None}

    @agent.tool(prepare=prepare_expansion)
    def open_at(ctx: RunContext[AgentDeps], segment_id: int) -> dict:
        """Resolve a segment to a clickable timestamp or article anchor."""

        if skipped := skipped_payload(ctx, "open_at", RetrievalKind.EXPANSION):
            return skipped
        operation = lambda: optional_knowledge(
            lambda: ctx.deps.services.open_at(segment_id)
        )
        if bounded_autonomy_enabled:
            read_result = _read_failure(
                ctx,
                tool_name="open_at",
                arguments={"segment_id": int(segment_id)},
                operation=operation,
            )
            if isinstance(read_result, _RecoveryPayload):
                return read_result.payload
            citation, call_index = read_result
        else:
            citation, call_index = execute_tool(ctx.deps, "open_at", operation)
        if (
            citation is not None
            and ctx.deps.reference_scope
            and not _citation_matches_scope(
                citation,
                ctx.deps.reference_scope,
            )
        ):
            citation = None
        if citation is not None:
            ctx.deps.record(citation)
        ctx.deps.tool_event(
            "open_at", "succeeded", call_index, 1 if citation is not None else 0
        )
        if citation is not None:
            ctx.deps.diagnostics and ctx.deps.diagnostics.retrieval_detail(tool_name="open_at", call_index=call_index, segment_id=segment_id, item_id=citation.item_id, title=citation.title, url=citation.url, excerpt=citation.excerpt, start=citation.start_sec)
        return {
            "status": "ok",
            "evidence": [citation.model_dump()] if citation is not None else [],
            "reason": None,
        }

    @agent.tool(prepare=prepare_bare_url_action)
    def request_save_confirmation(
        ctx: RunContext[AgentDeps], urls: list[str]
    ) -> dict:
        """Persist a bounded bare-URL batch for explicit confirmation."""

        try:
            outcome, call_index = execute_tool(ctx.deps, "request_save_confirmation", lambda: ctx.deps.actions.request_confirmation(urls))
        except ActionInputMismatch:
            raise ModelRetry(
                "Include every URL from the current user message exactly once "
                "per occurrence and in the original order; do not add URLs."
            ) from None
        ctx.deps.tool_event("request_save_confirmation", "succeeded", call_index, len(outcome.results))
        return outcome.tool_payload()

    @agent.tool(prepare=prepare_save)
    def save_videos(
        ctx: RunContext[AgentDeps],
        urls: list[str],
        why_saved: str | None = None,
    ) -> dict:
        """Queue current-message video URLs for the private tenant."""

        try:
            outcome, call_index = execute_tool(ctx.deps, "save_videos", lambda: ctx.deps.actions.save_videos(urls, why_saved=why_saved))
        except ActionInputMismatch:
            raise ModelRetry(
                "Include every URL from the current user message exactly once "
                "per occurrence and in the original order; do not add URLs."
            ) from None
        ctx.deps.tool_event("save_videos", "succeeded", call_index, len(outcome.results))
        return outcome.tool_payload()

    @agent.tool(prepare=prepare_pending_save)
    def confirm_video_save(ctx: RunContext[AgentDeps]) -> dict:
        """Consume the current conversation's persisted pending batch."""

        outcome, call_index = execute_tool(ctx.deps, "confirm_video_save", ctx.deps.actions.confirm)
        ctx.deps.tool_event("confirm_video_save", "succeeded", call_index, len(outcome.results))
        return outcome.tool_payload()

    @agent.tool(prepare=prepare_pending_save)
    def clarify_save_confirmation(ctx: RunContext[AgentDeps]) -> dict:
        """Ask for a clear decision without consuming the pending batch."""

        outcome, call_index = execute_tool(ctx.deps, "clarify_save_confirmation", ctx.deps.actions.clarify_confirmation)
        ctx.deps.tool_event("clarify_save_confirmation", "succeeded", call_index, len(outcome.results))
        return outcome.tool_payload()

    @agent.tool(prepare=prepare_pending_save)
    def cancel_video_save(ctx: RunContext[AgentDeps]) -> dict:
        """Cancel the current conversation's persisted pending batch."""

        outcome, call_index = execute_tool(ctx.deps, "cancel_video_save", ctx.deps.actions.cancel)
        ctx.deps.tool_event("cancel_video_save", "succeeded", call_index, len(outcome.results))
        return outcome.tool_payload()

    if management_enabled:
        @agent.tool(prepare=prepare_management)
        def list_saved_items(
            ctx: RunContext[AgentDeps],
            kind: Literal["video", "article"] | None = None,
            platform: Literal[
                "youtube", "bilibili", "wechat_mp", "ntu_kaltura"
            ] | None = None,
            state: Literal[
                "pending", "fetching", "needs_extension", "needs_asr", "chunking",
                "embedding", "ready", "failed", "no_text",
            ] | None = None,
            location: Literal["library", "trash"] = "library",
            limit: Annotated[int, Field(ge=1, le=50)] = 20,
            cursor: Annotated[str | None, Field(max_length=512)] = None,
        ) -> dict:
            """List the current tenant's bounded inventory projection."""
            arguments = {
                "kind": kind,
                "platform": platform,
                "state": state,
                "location": location,
                "limit": int(limit),
                "cursor": cursor,
            }
            if bounded_autonomy_enabled:
                return _run_management_read(
                    ctx,
                    tool_name="list_saved_items",
                    arguments=arguments,
                    operation=lambda: ctx.deps.actions.list_saved_items(
                        **arguments
                    ),
                )
            outcome, call_index = execute_tool(
                ctx.deps,
                "list_saved_items",
                lambda: ctx.deps.actions.list_saved_items(
                    kind=kind, platform=platform, state=state,
                    location=location, limit=limit, cursor=cursor,
                ),
            )
            ctx.deps.tool_event("list_saved_items", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_management)
        def get_saved_item(
            ctx: RunContext[AgentDeps], item_id: Annotated[int, Field(gt=0)]
        ) -> dict:
            """Read one item previously identified by the inventory list."""
            if bounded_autonomy_enabled:
                return _run_management_read(
                    ctx,
                    tool_name="get_saved_item",
                    arguments={"item_id": int(item_id)},
                    operation=lambda: ctx.deps.actions.get_saved_item(item_id),
                )
            outcome, call_index = execute_tool(ctx.deps, "get_saved_item", lambda: ctx.deps.actions.get_saved_item(item_id))
            ctx.deps.tool_event("get_saved_item", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_management)
        def update_saved_item(
            ctx: RunContext[AgentDeps],
            item_id: Annotated[int, Field(gt=0)],
            why_saved: Annotated[str | None, Field(max_length=500)],
        ) -> dict:
            """Update or clear only the user's saved reason."""
            outcome, call_index = execute_tool(ctx.deps, "update_saved_item", lambda: ctx.deps.actions.update_saved_item(item_id, why_saved))
            ctx.deps.tool_event("update_saved_item", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_management)
        def delete_saved_items(
            ctx: RunContext[AgentDeps],
            item_ids: Annotated[
                list[Annotated[int, Field(gt=0)]],
                Field(min_length=1, max_length=10),
            ],
        ) -> dict:
            """Create a durable deletion confirmation; never deletes immediately."""
            outcome, call_index = execute_tool(ctx.deps, "delete_saved_items", lambda: ctx.deps.actions.request_delete(item_ids))
            ctx.deps.tool_event("delete_saved_items", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_pending_delete)
        def confirm_item_deletion(ctx: RunContext[AgentDeps]) -> dict:
            outcome, call_index = execute_tool(
                ctx.deps,
                "confirm_item_deletion",
                ctx.deps.actions.confirm_delete,
            )
            ctx.deps.tool_event("confirm_item_deletion", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_pending_delete)
        def clarify_item_deletion(ctx: RunContext[AgentDeps]) -> dict:
            outcome, call_index = execute_tool(ctx.deps, "clarify_item_deletion", ctx.deps.actions.clarify_delete)
            ctx.deps.tool_event("clarify_item_deletion", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_pending_delete)
        def cancel_item_deletion(ctx: RunContext[AgentDeps]) -> dict:
            outcome, call_index = execute_tool(ctx.deps, "cancel_item_deletion", ctx.deps.actions.cancel_delete)
            ctx.deps.tool_event("cancel_item_deletion", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_management)
        def restore_saved_items(
            ctx: RunContext[AgentDeps],
            item_ids: Annotated[
                list[Annotated[int, Field(gt=0)]],
                Field(min_length=1, max_length=10),
            ],
        ) -> dict:
            outcome, call_index = execute_tool(ctx.deps, "restore_saved_items", lambda: ctx.deps.actions.restore_saved_items(item_ids))
            ctx.deps.tool_event("restore_saved_items", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

        @agent.tool(prepare=prepare_management)
        def retry_item_ingestion(
            ctx: RunContext[AgentDeps], item_id: Annotated[int, Field(gt=0)]
        ) -> dict:
            outcome, call_index = execute_tool(ctx.deps, "retry_item_ingestion", lambda: ctx.deps.actions.retry_item_ingestion(item_id))
            ctx.deps.tool_event("retry_item_ingestion", "succeeded" if outcome.status == "ok" else "failed", call_index, len(outcome.results))
            return outcome.tool_payload()

    return agent


COMPOSER_INSTRUCTIONS = """
你是私有知识库的回答编辑器。只能依据服务器提供的证据写回答，不能使用模型记忆补充事实。
输出结构化 sections；每个 section 写简洁中文文本，并列出支持该 section 的 segment ID。
不要输出 URL、视频标题、[S…] 标记、章节标题或服务器未提供的事实。最多引用五个不同视频。
""".strip()


def build_composer(
    model: Model | str,
    *,
    tool_timeout: float = 15.0,
    output_retries: int = 1,
) -> Agent[ComposerDeps, AnswerDraft]:
    """Build the answer-only stage with no retrieval or action tools."""

    composer = Agent(
        model,
        deps_type=ComposerDeps,
        output_type=PromptedOutput(AnswerDraft),
        instructions=COMPOSER_INSTRUCTIONS,
        retries={"output": max(0, int(output_retries))},
        tool_timeout=tool_timeout,
    )

    @composer.instructions
    def bounded_evidence_instruction(ctx: RunContext[ComposerDeps]) -> str:
        return _render_composer_evidence(
            ctx.deps.citations.values(),
            excerpt_chars=ctx.deps.excerpt_chars,
        )

    @composer.output_validator
    def validate_draft(ctx: RunContext[ComposerDeps], draft: AnswerDraft) -> AnswerDraft:
        cited_ids = {
            segment_id
            for section in draft.sections
            for segment_id in section.citation_ids
        }
        allowed = set(ctx.deps.citations)
        item_ids = {
            ctx.deps.citations[segment_id].item_id
            for segment_id in cited_ids
            if segment_id in ctx.deps.citations
        }
        if cited_ids and cited_ids.issubset(allowed) and len(item_ids) <= MAX_SOURCE_ITEMS:
            return draft
        ctx.deps.invalid_draft_count += 1
        if ctx.deps.diagnostics is not None:
            ctx.deps.diagnostics.event(
                "citation_validated",
                error_code="answer_unavailable",
                retry_count=ctx.deps.invalid_draft_count,
                agent_phase="answer",
            )
        raise ModelRetry(
            "Every section must cite only an allowed segment ID, and all sections together may cite no more than five videos."
        )

    return composer


class KnowledgeAgent:
    """Run the model and convert every outcome to a fail-closed answer."""

    def __init__(
        self,
        model: Model | str,
        settings: Settings,
        service_factory: Callable[[AgentRequest], KnowledgeServices],
        action_factory: (
            Callable[[AgentRequest], AgentActionServices] | None
        ) = None,
        *,
        composer_model: Model | str | None = None,
    ) -> None:
        # ``Settings`` owns validation, while the fallback keeps lightweight
        # deployment/test settings objects created before the rollout field
        # was introduced compatible with the flag-off path.
        self._bounded_autonomy_enabled = bool(
            getattr(settings, "agent_bounded_autonomy_enabled", False)
        )
        self._agent = build_agent(
            model,
            tool_timeout=settings.agent_tool_timeout_seconds,
            management_enabled=settings.agent_item_management_enabled,
            bounded_autonomy_enabled=self._bounded_autonomy_enabled,
        )
        answer_model = composer_model or model
        self._composer = build_composer(
            answer_model,
            tool_timeout=settings.agent_tool_timeout_seconds,
            output_retries=0 if self._bounded_autonomy_enabled else 1,
        )
        self._composer_model_settings = composer_model_settings(
            answer_model,
            max_tokens=settings.agent_composer_max_tokens,
        )
        self._settings = settings
        self._service_factory = service_factory
        self._action_factory = action_factory

    async def run(
        self,
        request: AgentRequest,
        *,
        diagnostics: RequestDiagnostics | None = None,
    ) -> AgentExecution:
        diagnostics = diagnostics or RequestDiagnostics.start(
            request.request_id, request.tenant.app_user_id,
            allow_retrieval_content=self._settings.notebook_agent_log_retrieval_content,
            environment=self._settings.notebook_agent_env,
        )
        parsed_references = parse_message_references(request.question)
        reference_scope = (
            parsed_references.references
            if parsed_references.has_supported_urls
            and parsed_references.has_semantic_text
            else ()
        )
        action_services = (
            self._action_factory(request)
            if (self._settings.agent_save_enabled or self._settings.agent_item_management_enabled)
            and self._action_factory is not None
            else None
        )
        actions = AgentActionRuntime(
            request,
            action_services,
            enabled=self._settings.agent_save_enabled,
            management_enabled=self._settings.agent_item_management_enabled,
            composable_reads=self._bounded_autonomy_enabled,
        )
        diagnostics.event("agent_started", agent_phase="retrieval")

        # Supported batches take the product's deterministic confirmation
        # route.  URL-only malformed/unsupported batches use the same action
        # boundary so its existing safe validation codes are preserved.
        if parsed_references.is_url_only_batch:
            try:
                outcome = actions.request_confirmation(
                    list(parsed_references.ordered_urls)
                )
            except ActionInputMismatch:
                outcome = actions.finalize_input_mismatch()
            diagnostics.event("action_validated", error_code=outcome.error_code)
            return AgentExecution(
                AgentAnswer(
                    status=outcome.status,
                    text=outcome.text,
                    action_results=list(outcome.results),
                    thread_id=request.thread_public_id,
                    error_code=outcome.error_code,
                ),
                [],
            )
        services = self._service_factory(request)
        if isinstance(services, KnowledgeServices):
            services.set_diagnostics(diagnostics)
        # Alternate service implementations used by deployments/tests can
        # opt into the same request-scoped exact-reference contract without a
        # new factory signature.  Production KnowledgeServices always does.
        set_reference_scope = getattr(services, "set_reference_scope", None)
        if callable(set_reference_scope):
            set_reference_scope(reference_scope or None)
        deps = AgentDeps(
            services,
            actions,
            diagnostics=diagnostics,
            reference_scope=reference_scope,
            reference_save_requested=_explicit_save_requested(
                parsed_references.semantic_remainder
            ),
            context=request.context,
            todo_store=(
                TurnTodoStore() if self._bounded_autonomy_enabled else None
            ),
            recovery_ledger=(
                RecoveryLedger() if self._bounded_autonomy_enabled else None
            ),
        )
        if self._bounded_autonomy_enabled and deps.recovery_ledger is not None:
            deps.recovery_policy = RecoveryPolicy(deps.recovery_ledger)

        def attach_read_observations(execution: AgentExecution) -> AgentExecution:
            if (
                self._bounded_autonomy_enabled
                and deps.actions.outcome is None
                and actions.read_action_results
            ):
                execution.answer.action_results = list(actions.read_action_results)
            return execution

        def partial_read_fallback() -> AgentExecution | None:
            """Preserve trusted read observations when a later stage cannot finish."""

            if not self._bounded_autonomy_enabled or not actions.read_action_texts:
                return None
            if deps.todo_store is not None and deps.todo_store.snapshot.unfinished:
                try:
                    deps.todo_store.mark_blocked()
                except TodoValidationError:
                    pass
            partial_text = "\n".join(actions.read_action_texts)
            partial_text += "\n" + BOUNDED_UNAVAILABLE_REMAINDER
            return AgentExecution(
                AgentAnswer(
                    status="ok",
                    text=partial_text,
                    action_results=list(actions.read_action_results),
                    thread_id=request.thread_public_id,
                ),
                _canonical_history(request.question, partial_text),
            )

        usage = RunUsage()
        model_attempts = 0
        agent_result = None

        def record_model_attempt(_context):
            nonlocal model_attempts
            model_attempts += 1
            diagnostics.event(
                "model_attempt",
                call_index=model_attempts,
                agent_phase="retrieval",
            )
            return {"parallel_tool_calls": False}
        try:
            message_history = ModelMessagesTypeAdapter.validate_python(
                list(request.history)
            )
            async with asyncio.timeout(self._settings.agent_timeout_seconds):
                with self._agent.parallel_tool_call_execution_mode("sequential"):
                    agent_result = await self._agent.run(
                        request.question,
                        deps=deps,
                        message_history=message_history,
                        usage_limits=UsageLimits(
                            request_limit=self._settings.agent_request_limit,
                            tool_calls_limit=self._settings.agent_tool_calls_limit,
                            output_tokens_limit=self._settings.agent_output_token_limit,
                        ),
                        usage=usage,
                        model_settings=record_model_attempt,
                    )
        except TimeoutError:
            diagnostics.event(
                "agent_failed", error_code="timeout", agent_phase="retrieval"
            )
            if deps.citations:
                if self._bounded_autonomy_enabled:
                    return attach_read_observations(
                        self._evidence_fallback(
                            request, list(deps.citations.values())
                        )
                    )
                return attach_read_observations(
                    await self._compose_or_fallback(request, deps, diagnostics)
                )
            if partial := partial_read_fallback():
                return partial
            return attach_read_observations(self._failure(
                request, "模型响应超时，请稍后重试。", "timeout", diagnostics,
                log_event=False,
            ))
        except UsageLimitExceeded as exc:
            kind, limit, used = classify_usage_limit(exc)
            diagnostics.event(
                "agent_failed", error_code="limit", exception=exc,
                limit_kind=kind, limit_value=limit,
                used_value=deps.tool_calls if kind == "tool_calls" else used,
                projected_value=used if kind == "tool_calls" else None,
                agent_phase="retrieval",
            )
            if deps.invalid_item_scope_attempt:
                return attach_read_observations(self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                ))
            if deps.citations:
                if self._bounded_autonomy_enabled:
                    return attach_read_observations(
                        self._evidence_fallback(
                            request, list(deps.citations.values())
                        )
                    )
                return attach_read_observations(
                    await self._compose_or_fallback(request, deps, diagnostics)
                )
            if partial := partial_read_fallback():
                return partial
            return attach_read_observations(self._failure(
                request, self._limit_text(kind, phase="retrieval"), "limit", diagnostics,
                log_event=False,
            ))
        except EmbeddingUnavailable:
            if self._bounded_autonomy_enabled and deps.citations:
                return attach_read_observations(
                    self._evidence_fallback(request, list(deps.citations.values()))
                )
            return attach_read_observations(self._failure(
                request,
                "查询能力暂时不可用，请稍后重试。",
                "embedding_unavailable",
                diagnostics,
            ))
        except RetrievalUnavailable:
            if self._bounded_autonomy_enabled and deps.citations:
                return attach_read_observations(
                    self._evidence_fallback(request, list(deps.citations.values()))
                )
            return attach_read_observations(self._failure(
                request,
                "查询能力暂时不可用，请稍后重试。",
                "retrieval_unavailable",
                diagnostics,
            ))
        except ModelHTTPError as exc:
            diagnostics.event(
                "agent_failed",
                error_code="runtime_error",
                exception=exc,
                http_status=exc.status_code,
                agent_phase="retrieval",
            )
            if self._bounded_autonomy_enabled and deps.citations:
                return attach_read_observations(
                    self._evidence_fallback(request, list(deps.citations.values()))
                )
            if partial := partial_read_fallback():
                return partial
            return attach_read_observations(self._failure(
                request,
                "知识库暂时无法完成检索，请稍后重试。",
                "runtime_error",
                diagnostics,
                log_event=False,
            ))
        except UnexpectedModelBehavior as exc:
            if deps.invalid_item_scope_attempt:
                return attach_read_observations(self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                ))
            if deps.actions.input_mismatch:
                outcome = deps.actions.finalize_input_mismatch()
                diagnostics.event(
                    "action_validated", error_code=outcome.error_code
                )
                return AgentExecution(
                    AgentAnswer(
                        status=outcome.status,
                        text=outcome.text,
                        action_results=list(outcome.results),
                        thread_id=request.thread_public_id,
                        error_code=outcome.error_code,
                    ),
                    [],
                )
            diagnostics.event(
                "agent_failed", error_code="runtime_error", exception=exc,
                agent_phase="retrieval",
            )
            if self._bounded_autonomy_enabled and deps.citations:
                return attach_read_observations(
                    self._evidence_fallback(request, list(deps.citations.values()))
                )
            if partial := partial_read_fallback():
                return partial
            return attach_read_observations(self._failure(
                request, "知识库暂时无法完成检索，请稍后重试。", "runtime_error", diagnostics,
                log_event=False,
            ))
        except KnowledgeNotFound:
            return attach_read_observations(
                self._failure(request, "请求的知识片段不存在。", "not_found", diagnostics)
            )
        except Exception as exc:
            if deps.invalid_item_scope_attempt:
                return attach_read_observations(self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                ))
            diagnostics.event(
                "agent_failed",
                error_code="runtime_error",
                exception=exc,
                agent_phase="retrieval",
            )
            if self._bounded_autonomy_enabled and deps.citations:
                return attach_read_observations(
                    self._evidence_fallback(request, list(deps.citations.values()))
                )
            if partial := partial_read_fallback():
                return partial
            return attach_read_observations(self._failure(
                request, "知识库暂时无法完成检索，请稍后重试。", "runtime_error", diagnostics,
                log_event=False,
            ))

        if deps.actions.outcome is not None:
            outcome = deps.actions.outcome
            diagnostics.event(
                "action_validated", error_code=outcome.error_code
            )
            return AgentExecution(
                AgentAnswer(
                    status=outcome.status,
                    text=outcome.text,
                    action_results=list(outcome.results),
                    thread_id=request.thread_public_id,
                    error_code=outcome.error_code,
                ),
                # Management reads and outcomes are canonical, bounded
                # server-rendered context for follow-up turns (for example,
                # “next page” or “the second item”).  Save/pending actions
                # retain the old empty-history contract so raw URLs and
                # pending payloads never enter model history.
                _canonical_history(request.question, outcome.text)
                if outcome.history_visible
                else [],
            )

        if self._bounded_autonomy_enabled:
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                )
            if deps.read_recovery_exhausted or deps.pending_read_failures:
                # A successful inventory observation is authoritative even
                # when a later read cannot be recovered.  Do not accept the
                # model's prose as a replacement for the unavailable
                # remainder.
                if deps.todo_store is not None and deps.todo_store.snapshot.unfinished:
                    try:
                        deps.todo_store.mark_blocked()
                    except TodoValidationError:
                        pass
                if actions.read_action_texts:
                    diagnostics.event(
                        "recovery",
                        error_code="read_unavailable",
                        error_category="read_unavailable",
                        recovery_outcome="exhausted",
                        recovery_count=(
                            deps.recovery_ledger.remaining_actions
                            if deps.recovery_ledger is not None
                            else 0
                        ),
                        agent_phase="retrieval",
                    )
                    partial = partial_read_fallback()
                    if partial is not None:
                        return partial
                if deps.citations:
                    return self._evidence_fallback(request, list(deps.citations.values()))
                return self._failure(
                    request,
                    "后续读取暂时不可用，请稍后重试。",
                    "read_unavailable",
                    diagnostics,
                    log_event=False,
                )
            natural_text = getattr(agent_result, "output", None)
            if not isinstance(natural_text, str):
                diagnostics.event(
                    "agent_failed", error_code="answer_unavailable", agent_phase="answer"
                )
                return self._failure(
                    request,
                    "暂时无法生成回答，请稍后重试。",
                    "answer_unavailable",
                    diagnostics,
                    log_event=False,
                )

            # Todo is turn-local working memory only.  A terminal action above
            # wins before this check; ordinary completion still requires no
            # pending/in-progress items.  The only blocked exception is a
            # trusted no-tool clarification for missing context.
            allow_blocked_todo = _allow_blocked_todo_clarification(
                request, deps, natural_text
            )
            try:
                if deps.todo_store is not None:
                    deps.todo_store.finalize(allow_blocked=allow_blocked_todo)
            except TodoValidationError:
                diagnostics.event(
                    "agent_failed", error_code="todo_incomplete", agent_phase="retrieval"
                )
                return self._failure(
                    request,
                    "当前步骤尚未完成，请说明要继续哪一步。",
                    "todo_incomplete",
                    diagnostics,
                    log_event=False,
                )

            if deps.search_calls < 1:
                if deps.invalid_item_scope_attempt:
                    return self._failure(
                        request,
                        "只能依据本轮已返回的条目继续限定检索。",
                        "item_scope_required",
                        diagnostics,
                    )
                # Explicit current-message URL questions retain their
                # deterministic grounding obligation even when the model
                # elects to return prose without tools.
                if reference_scope:
                    return self._failure(
                        request,
                        "未完成必要的知识库检索，因此不返回无来源答案。",
                        "search_required",
                        diagnostics,
                    )
                if actions.read_action_texts:
                    # Inventory/detail observations are server-rendered public
                    # projections.  When no knowledge search followed them,
                    # use that canonical text instead of trusting unconstrained
                    # model prose to restate item state, titles, or counts.
                    read_text = "\n".join(actions.read_action_texts)
                    diagnostics.event("citation_validated", agent_phase="answer")
                    return AgentExecution(
                        AgentAnswer(
                            status="ok",
                            text=read_text,
                            action_results=list(actions.read_action_results),
                            thread_id=request.thread_public_id,
                        ),
                        _canonical_history(request.question, read_text),
                    )
                try:
                    validated = validate_natural_answer(natural_text)
                except NaturalAnswerValidationError:
                    diagnostics.event(
                        "citation_validated",
                        error_code="answer_unavailable",
                        agent_phase="answer",
                    )
                    return self._failure(
                        request,
                        "回答未通过安全校验，请稍后重试。",
                        "answer_unavailable",
                        diagnostics,
                        log_event=False,
                    )
                diagnostics.event("citation_validated", agent_phase="answer")
                return AgentExecution(
                    AgentAnswer(
                        status="ok",
                        text=validated.text,
                        action_results=list(actions.read_action_results),
                        thread_id=request.thread_public_id,
                    ),
                    _canonical_history(request.question, validated.text),
                )

            if not deps.citations:
                diagnostics.event(
                    "citation_validated",
                    error_code="no_evidence",
                    agent_phase="retrieval",
                )
                return AgentExecution(
                    AgentAnswer(
                        status="not_found",
                        text="知识库中未找到足够证据。",
                        action_results=list(actions.read_action_results),
                        thread_id=request.thread_public_id,
                        error_code="no_evidence",
                    ),
                    [],
                )

            try:
                validated = validate_natural_answer(
                    natural_text,
                    citations=deps.citations,
                    knowledge_search_succeeded=True,
                    explicit_reference_scope=reference_scope,
                    citation_matches_scope=_citation_matches_scope,
                    max_source_items=MAX_SOURCE_ITEMS,
                )
            except NaturalAnswerValidationError:
                # Phase B keeps the existing same-evidence Composer/fallback as
                # a compatibility repair.  It never launches another search.
                diagnostics.event(
                    "citation_validated",
                    error_code="answer_unavailable",
                    agent_phase="answer",
                )
                repaired = await self._repair_bounded_answer(request, deps, diagnostics)
                repaired.answer.action_results = list(actions.read_action_results)
                return repaired

            selected_ids = set(validated.citation_ids)
            # Preserve the server's trusted retrieval order when rendering
            # sources; marker order remains model-authored prose only.
            selected = [
                citation
                for citation in deps.citations.values()
                if citation.segment_id in selected_ids
            ]
            answer_text = _append_sources(validated.text, selected)
            diagnostics.event(
                "citation_validated",
                result_count=len(selected),
                agent_phase="answer",
            )
            return AgentExecution(
                AgentAnswer(
                    status="ok",
                    text=answer_text,
                    citations=selected,
                    action_results=list(actions.read_action_results),
                    thread_id=request.thread_public_id,
                ),
                _canonical_history(request.question, answer_text),
            )

        if deps.search_calls < 1:
            return self._failure(
                request,
                "未完成必要的知识库检索，因此不返回无来源答案。",
                "search_required",
                diagnostics,
            )
        if not deps.citations:
            diagnostics.event(
                "citation_validated",
                error_code="no_evidence",
                agent_phase="retrieval",
            )
            return AgentExecution(
                AgentAnswer(
                    status="not_found",
                    text="知识库中未找到足够证据。",
                    thread_id=request.thread_public_id,
                    error_code="no_evidence",
                ),
                [],
            )
        return await self._compose_or_fallback(request, deps, diagnostics)

    async def _repair_bounded_answer(
        self,
        request: AgentRequest,
        deps: AgentDeps,
        diagnostics: RequestDiagnostics,
    ) -> AgentExecution:
        """Attempt one tool-free same-evidence answer repair on the bounded path."""

        citations = _limit_citations_by_item(list(deps.citations.values()))
        ledger = deps.recovery_ledger
        policy = deps.recovery_policy
        if ledger is None or policy is None:
            return self._evidence_fallback(request, citations)
        error = ErrorEnvelope.from_category(
            "answer_validation",
            operation="answer",
            partial_evidence=True,
        )
        grant = policy.grant(
            error,
            has_evidence=True,
            request_budget_remaining=self._settings.agent_request_limit,
            provider_budget_remaining=1,
            answer_budget_remaining=1,
            output_budget_remaining=self._settings.agent_output_token_limit,
        )
        if not grant.permits("repair_answer") or not ledger.record_answer_repair():
            diagnostics.event(
                "recovery",
                error_code=error.code,
                error_category=error.category,
                recovery_outcome="denied",
                recovery_count=grant.remaining_actions,
                agent_phase="answer",
            )
            return self._evidence_fallback(request, citations)
        diagnostics.event(
            "recovery",
            error_code=error.code,
            error_category=error.category,
            recovery_action="repair_answer",
            recovery_outcome="consumed",
            # Keep the diagnostic count aligned with the server-issued grant:
            # it reports how many whole-turn actions were available when this
            # repair was authorized, before consuming the action.
            recovery_count=grant.remaining_actions,
            agent_phase="answer",
        )
        composer_deps = ComposerDeps(
            {citation.segment_id: citation for citation in citations},
            diagnostics=diagnostics,
        )
        attempts = 0

        def record_answer_attempt(_context):
            nonlocal attempts
            attempts += 1
            diagnostics.event(
                "model_attempt", call_index=attempts, agent_phase="answer"
            )
            return dict(self._composer_model_settings)

        try:
            async with asyncio.timeout(self._settings.agent_timeout_seconds):
                result = await self._composer.run(
                    request.question.strip(),
                    deps=composer_deps,
                    usage_limits=UsageLimits(
                        request_limit=1,
                        output_tokens_limit=self._settings.agent_output_token_limit,
                    ),
                    usage=RunUsage(),
                    model_settings=record_answer_attempt,
                )
        except Exception as exc:
            diagnostics.event(
                "agent_failed",
                error_code="answer_unavailable",
                exception=exc,
                agent_phase="answer",
            )
            return self._evidence_fallback(request, citations)

        cited_ids = {
            segment_id
            for section in result.output.sections
            for segment_id in section.citation_ids
        }
        selected = [
            citation for citation in citations if citation.segment_id in cited_ids
        ]
        answer_text = _render_sections(result.output, selected)
        diagnostics.event(
            "citation_validated",
            result_count=len(selected),
            retry_count=composer_deps.invalid_draft_count,
            agent_phase="answer",
        )
        return AgentExecution(
            AgentAnswer(
                status="ok",
                text=answer_text,
                citations=selected,
                thread_id=request.thread_public_id,
            ),
            _canonical_history(request.question, answer_text),
        )

    async def _compose_or_fallback(
        self,
        request: AgentRequest,
        deps: AgentDeps,
        diagnostics: RequestDiagnostics,
    ) -> AgentExecution:
        """Run a fresh-budget answer-only stage, preserving trusted evidence."""

        citations = _limit_citations_by_item(list(deps.citations.values()))
        composer_deps = ComposerDeps(
            {citation.segment_id: citation for citation in citations},
            diagnostics=diagnostics,
        )
        attempts = 0

        def record_answer_attempt(_context):
            nonlocal attempts
            attempts += 1
            diagnostics.event(
                "model_attempt", call_index=attempts, agent_phase="answer"
            )
            return dict(self._composer_model_settings)

        async def run_attempt(attempt_deps: ComposerDeps):
            return await self._composer.run(
                request.question.strip(),
                deps=attempt_deps,
                usage_limits=UsageLimits(
                    request_limit=ANSWER_REQUEST_LIMIT,
                    output_tokens_limit=self._settings.agent_output_token_limit,
                ),
                usage=RunUsage(),
                model_settings=record_answer_attempt,
            )

        try:
            async with asyncio.timeout(self._settings.agent_timeout_seconds):
                active_deps = composer_deps
                first_error: BaseException | None = None
                with capture_run_messages() as attempt_messages:
                    try:
                        result = await run_attempt(active_deps)
                    except (
                        UsageLimitExceeded,
                        UnexpectedModelBehavior,
                        ModelRetry,
                    ) as exc:
                        first_error = exc
                limit_kind = _compression_retry_kind(first_error, attempt_messages)
                if first_error is not None and limit_kind is None:
                    raise first_error
                if limit_kind is not None:
                    compressed_deps = _compressed_composer_deps(composer_deps)
                    if compressed_deps is None:
                        if first_error is not None:
                            raise first_error
                        raise UnexpectedModelBehavior(
                            "Composer response reached provider length limit"
                        )
                    diagnostics.event(
                        "context_compressed",
                        retry_count=1,
                        result_count=len(compressed_deps.citations),
                        projected_value=len(composer_deps.citations),
                        limit_kind=limit_kind,
                        agent_phase="answer",
                    )
                    active_deps = compressed_deps
                    with capture_run_messages() as compressed_messages:
                        result = await run_attempt(active_deps)
                    if _captured_length_cutoff(compressed_messages):
                        raise UnexpectedModelBehavior(
                            "Compressed Composer response reached provider length limit"
                        )
        except TimeoutError:
            diagnostics.event(
                "agent_failed", error_code="timeout", agent_phase="answer"
            )
            return self._evidence_fallback(request, citations)
        except UsageLimitExceeded as exc:
            kind, limit, used = classify_usage_limit(exc)
            diagnostics.event(
                "agent_failed",
                error_code="limit",
                exception=exc,
                limit_kind=kind,
                limit_value=limit,
                used_value=used,
                agent_phase="answer",
            )
            return self._evidence_fallback(request, citations)
        except ModelHTTPError as exc:
            diagnostics.event(
                "agent_failed",
                error_code="answer_unavailable",
                exception=exc,
                http_status=exc.status_code,
                agent_phase="answer",
            )
            return self._evidence_fallback(request, citations)
        except (UnexpectedModelBehavior, ModelRetry) as exc:
            diagnostics.event(
                "agent_failed",
                error_code="answer_unavailable",
                exception=exc,
                agent_phase="answer",
            )
            return self._evidence_fallback(request, citations)
        except Exception as exc:
            diagnostics.event(
                "agent_failed",
                error_code="answer_unavailable",
                exception=exc,
                agent_phase="answer",
            )
            return self._evidence_fallback(request, citations)

        cited_ids = {
            segment_id
            for section in result.output.sections
            for segment_id in section.citation_ids
        }
        selected = [
            citation for citation in citations if citation.segment_id in cited_ids
        ]
        answer_text = _render_sections(result.output, selected)
        diagnostics.event(
            "citation_validated",
            result_count=len(selected),
            retry_count=active_deps.invalid_draft_count,
            agent_phase="answer",
        )
        return AgentExecution(
            AgentAnswer(
                status="ok",
                text=answer_text,
                citations=selected,
                thread_id=request.thread_public_id,
            ),
            _canonical_history(request.question, answer_text),
        )

    @staticmethod
    def _evidence_fallback(
        request: AgentRequest, citations: list[Citation]
    ) -> AgentExecution:
        answer_text = _append_sources(FALLBACK_INTRO, citations)
        return AgentExecution(
            AgentAnswer(
                status="ok",
                text=answer_text,
                citations=citations,
                thread_id=request.thread_public_id,
            ),
            _canonical_history(request.question, answer_text),
        )

    @staticmethod
    def _limit_text(kind: str, *, phase: Literal["retrieval", "answer"]) -> str:
        prefix = "检索阶段" if phase == "retrieval" else "回答阶段"
        if kind == "output_tokens":
            return f"{prefix}的模型输出超过安全上限，请稍后重试。"
        if kind == "request":
            return f"{prefix}的模型请求次数超过安全上限，请稍后重试。"
        if kind == "tool_calls":
            return "检索工具调用超过安全上限，请稍后重试。"
        return f"{prefix}达到安全上限，请稍后重试。"

    @staticmethod
    def _failure(
        request: AgentRequest,
        text: str,
        code: str,
        diagnostics: RequestDiagnostics,
        *,
        log_event: bool = True,
    ) -> AgentExecution:
        if log_event:
            diagnostics.event(
                "agent_failed", error_code=code, agent_phase="retrieval"
            )
        return AgentExecution(
            AgentAnswer(
                status="failed",
                text=text,
                thread_id=request.thread_public_id,
                error_code=code,
            ),
            [],
        )


def _limit_citations_by_item(citations: list[Citation]) -> list[Citation]:
    """Project deterministic top-five item evidence in retrieval order."""

    selected: list[Citation] = []
    item_ids: set[int] = set()
    for citation in citations:
        if citation.item_id not in item_ids:
            if len(item_ids) >= MAX_SOURCE_ITEMS:
                continue
            item_ids.add(citation.item_id)
        selected.append(citation)
    return selected


def _render_composer_evidence(
    citations: Iterable[Citation],
    *,
    excerpt_chars: int,
) -> str:
    """Render the trusted Composer view with an explicit excerpt projection."""

    rows: list[str] = []
    for citation in citations:
        excerpt = " ".join(citation.excerpt.split())[:excerpt_chars]
        timestamp = (
            f"，时间 {int(citation.start_sec)} 秒"
            if citation.start_sec is not None
            else ""
        )
        rows.append(
            f"ID {citation.segment_id}，视频《{citation.title}》{timestamp}：{excerpt}"
        )
    return "可用证据（仅可引用以下 ID）：\n" + "\n".join(rows)


def _compressed_citations(citations: list[Citation]) -> list[Citation]:
    """Select coverage first, then fill remaining slots in retrieval order."""

    selected: list[Citation] = []
    selected_segments: set[int] = set()
    covered_items: set[int] = set()
    for citation in citations:
        if citation.item_id in covered_items:
            continue
        selected.append(citation)
        selected_segments.add(citation.segment_id)
        covered_items.add(citation.item_id)
        if len(selected) >= COMPRESSED_EVIDENCE_LIMIT:
            return selected
    for citation in citations:
        if citation.segment_id in selected_segments:
            continue
        selected.append(citation)
        selected_segments.add(citation.segment_id)
        if len(selected) >= COMPRESSED_EVIDENCE_LIMIT:
            break
    return selected


def _compressed_composer_deps(deps: ComposerDeps) -> ComposerDeps | None:
    """Create a smaller local evidence view, or skip a useless retry."""

    original = list(deps.citations.values())
    selected = _compressed_citations(original)
    compressed = ComposerDeps(
        {citation.segment_id: citation for citation in selected},
        excerpt_chars=COMPRESSED_EVIDENCE_EXCERPT_CHARS,
        diagnostics=deps.diagnostics,
    )
    original_size = len(
        _render_composer_evidence(original, excerpt_chars=deps.excerpt_chars)
    )
    compressed_size = len(
        _render_composer_evidence(
            compressed.citations.values(),
            excerpt_chars=compressed.excerpt_chars,
        )
    )
    if len(compressed.citations) >= len(deps.citations) and compressed_size >= original_size:
        return None
    return compressed


def _compression_retry_kind(
    exc: BaseException | None,
    messages: list[ModelMessage],
) -> str | None:
    """Classify only output-budget or structured-output exhaustion for retry."""

    if isinstance(exc, UsageLimitExceeded):
        kind, _, _ = classify_usage_limit(exc)
        return "output_tokens" if kind == "output_tokens" else None
    if (
        exc is None or isinstance(exc, (UnexpectedModelBehavior, ModelRetry))
    ) and _captured_length_cutoff(messages):
        return "unknown"
    return None


def _captured_length_cutoff(messages: list[ModelMessage]) -> bool:
    """Return whether the provider explicitly marked any response truncated."""

    return any(
        isinstance(message, ModelResponse) and message.finish_reason == "length"
        for message in messages
    )


def _render_sections(draft: AnswerDraft, citations: list[Citation]) -> str:
    """Render server-owned markers after structured citation validation."""

    allowed = {citation.segment_id for citation in citations}
    sections: list[str] = []
    for section in draft.sections:
        ids = [
            segment_id
            for segment_id in section.citation_ids
            if segment_id in allowed
        ]
        markers = " ".join(f"[S{segment_id}]" for segment_id in ids)
        sections.append(f"{section.text.strip()} {markers}".strip())
    return _append_sources("\n\n".join(sections), citations)


def _canonical_history(question: str, answer: str) -> list[ModelMessage]:
    """Persist only the normalized user question and visible final answer."""

    normalized_question = " ".join(question.split())
    return [
        ModelRequest(parts=[UserPromptPart(normalized_question)]),
        ModelResponse(parts=[TextPart(answer)]),
    ]


def _append_sources(text: str, citations: list[Citation]) -> str:
    """Render ranked source groups without inventing chapter metadata.

    ``citations`` arrives in the retrieval order retained by the server.  That
    order is the only trustworthy relevance signal at this boundary.  A video
    gets one top-level entry, while every distinct cited segment remains a
    nested timestamp link; exact duplicate segment ids were already collapsed
    when evidence was recorded.
    """

    groups: dict[int, list[Citation]] = {}
    for citation in citations:
        if citation.item_id not in groups:
            if len(groups) >= MAX_SOURCE_ITEMS:
                continue
            groups[citation.item_id] = []
        groups[citation.item_id].append(citation)

    lines = [text.rstrip(), "", "来源："]
    for group in groups.values():
        title = group[0].title
        lines.append(f"- {title}")
        for citation in group:
            excerpt = " ".join(citation.excerpt.split())
            if len(excerpt) > 180:
                excerpt = f"{excerpt[:177]}…"
            lines.append(
                f"  - [S{citation.segment_id}] {citation.url} — {excerpt}"
            )
    return "\n".join(lines)
