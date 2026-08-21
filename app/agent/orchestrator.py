"""Product-level bounded Agent orchestration and finalization."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic_ai import UsageLimits
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from app.agent.actions import ActionInputMismatch, AgentActionRuntime, AgentActionServices
from app.agent.agent_builder import build_agent
from app.agent.answer_pipeline import (
    AnswerPipeline,
    _canonical_history,
    build_composer,
)
from app.agent.answer_validation import NaturalAnswerValidationError, validate_natural_answer
from app.agent.autonomy import RecoveryLedger, RecoveryPolicy, TodoValidationError, TurnTodoStore
from app.agent.context import TurnContext
from app.agent.provider import composer_model_settings
from app.agent.response import ResponseEnvelope
from app.agent.runtime_state import (
    AgentDeps,
    AgentExecution,
)
from app.agent.services import (
    EmbeddingUnavailable,
    KnowledgeNotFound,
    KnowledgeServices,
    RetrievalUnavailable,
)
from app.agent.types import AgentAnswer, AgentRequest
from app.config import Settings
from app.diagnostics import RequestDiagnostics, classify_usage_limit
from app.ingest.submission import parse_message_references

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
    text = semantic_text.strip()
    if not text or _NEGATED_SAVE_PATTERN.search(text):
        return False
    return any(pattern.search(text) for pattern in _EXPLICIT_SAVE_PATTERNS)

def _is_clarification_question(text: str) -> bool:
    return isinstance(text, str) and bool(text.strip()) and ("?" in text or "？" in text)

def _allow_blocked_todo_clarification(
    deps: AgentDeps,
    natural_text: str,
) -> bool:
    store = deps.todo_store
    if store is None or not store.snapshot.items or store.snapshot.unfinished:
        return False
    if not any(item.status == "blocked" for item in store.snapshot.items):
        return False
    if deps.search_calls or deps.citations or deps.actions.read_action_results:
        return False
    if deps.reference_scope or deps.semantic_url_question or deps.context.recent_inventory:
        return False
    return _is_clarification_question(natural_text)


@dataclass(frozen=True)
class _PrimaryResult:
    value: Any


class KnowledgeAgent:
    """Run the bounded Agent and convert every outcome to a fail-closed answer."""

    def __init__(
        self,
        model: Model | str,
        settings: Settings,
        service_factory: Callable[[AgentRequest], KnowledgeServices],
        action_factory: Callable[[AgentRequest], AgentActionServices] | None = None,
        *,
        composer_model: Model | str | None = None,
    ) -> None:
        self._agent = build_agent(
            model,
            tool_timeout=settings.agent_tool_timeout_seconds,
        )
        answer_model = composer_model or model
        self._composer = build_composer(
            answer_model,
            tool_timeout=settings.agent_tool_timeout_seconds,
            output_retries=0,
        )
        self._composer_model_settings = composer_model_settings(
            answer_model,
            max_tokens=settings.agent_composer_max_tokens,
        )
        self._answer_pipeline = AnswerPipeline(
            self._composer,
            composer_model_settings=self._composer_model_settings,
            settings=settings,
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
            request.request_id,
            request.tenant.app_user_id,
            allow_retrieval_content=self._settings.notebook_agent_log_retrieval_content,
            environment=self._settings.notebook_agent_env,
        )
        parsed = parse_message_references(request.question)
        # A URL in a semantic question is model context, not a server-owned
        # exact retrieval scope.  The primary Agent can decide whether to
        # search the tenant library or narrow a search with ``item_id``; the
        # service layer remains the hard tenant/visibility boundary.  Bare URL
        # messages still take the deterministic save-confirmation route below.
        reference_scope: tuple[tuple[str, str], ...] = ()
        actions = self._build_actions(request)
        diagnostics.event("agent_started", agent_phase="retrieval")

        if parsed.is_url_only_batch:
            return self._bare_url_action(request, actions, parsed.ordered_urls, diagnostics)

        services = self._service_factory(request)
        if isinstance(services, KnowledgeServices):
            services.set_diagnostics(diagnostics)
        # Do not propagate parsed URL references as an exact search scope.
        # ``KnowledgeServices`` defaults to unrestricted tenant-wide search;
        # any optional item narrowing is supplied by the model and checked by
        # its tenant-scoped service query.  If a trusted caller constructed a
        # narrower service scope, preserve that server-owned constraint rather
        # than widening it by resetting the service here.
        deps = self._build_deps(
            request,
            actions,
            services,
            diagnostics,
            reference_scope,
            parsed.semantic_remainder,
            parsed.has_supported_urls and parsed.has_semantic_text,
        )
        primary = await self._run_primary_agent(request, deps, diagnostics)
        if isinstance(primary, AgentExecution):
            return self._attach_read_observations(primary, deps)
        return await self._finalize_primary_result(
            request,
            deps,
            primary.value,
            diagnostics,
            reference_scope,
        )

    def _build_actions(self, request: AgentRequest) -> AgentActionRuntime:
        services = self._action_factory(request) if self._action_factory is not None else None
        return AgentActionRuntime(
            request,
            services,
            enabled=True,
            management_enabled=True,
            composable_reads=True,
        )

    @staticmethod
    def _search_completed_without_evidence(deps: AgentDeps) -> bool:
        """Whether a clean successful search permits no-evidence projection."""

        return bool(
            deps.successful_searches
            and not deps.citations
            and not deps.pending_read_failures
            and not deps.read_recovery_exhausted
        )

    def _build_deps(
        self,
        request: AgentRequest,
        actions: AgentActionRuntime,
        services: KnowledgeServices,
        diagnostics: RequestDiagnostics,
        reference_scope: tuple[tuple[str, str], ...],
        semantic_text: str,
        semantic_url_question: bool,
    ) -> AgentDeps:
        deps = AgentDeps(
            services,
            actions,
            diagnostics=diagnostics,
            reference_scope=reference_scope,
            semantic_url_question=semantic_url_question,
            reference_save_requested=_explicit_save_requested(semantic_text),
            context=request.context,
            todo_store=TurnTodoStore(),
            recovery_ledger=RecoveryLedger(),
        )
        deps.recovery_policy = RecoveryPolicy(deps.recovery_ledger)
        return deps

    async def _run_primary_agent(
        self,
        request: AgentRequest,
        deps: AgentDeps,
        diagnostics: RequestDiagnostics,
    ) -> AgentExecution | _PrimaryResult:
        usage = RunUsage()
        attempts = 0

        def record_model_attempt(_context):
            nonlocal attempts
            attempts += 1
            diagnostics.event(
                "model_attempt",
                call_index=attempts,
                agent_phase="retrieval",
            )
            return {"parallel_tool_calls": False}

        try:
            history = ModelMessagesTypeAdapter.validate_python(list(request.history))
            async with asyncio.timeout(self._settings.agent_timeout_seconds):
                with self._agent.parallel_tool_call_execution_mode("sequential"):
                    result = await self._agent.run(
                        request.question,
                        deps=deps,
                        message_history=history,
                        usage_limits=UsageLimits(
                            request_limit=self._settings.agent_request_limit,
                            tool_calls_limit=self._settings.agent_tool_calls_limit,
                            output_tokens_limit=self._settings.agent_output_token_limit,
                        ),
                        usage=usage,
                        model_settings=record_model_attempt,
                    )
            return _PrimaryResult(result)
        except TimeoutError:
            diagnostics.event("agent_failed", error_code="timeout", agent_phase="retrieval")
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            if deps.citations:
                recovered = await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
                if recovered.answer.status == "ok":
                    recovered.answer.action_results = list(
                        deps.actions.read_action_results
                    )
                return recovered
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            if partial := self._partial_read_fallback(request, deps):
                return partial
            return self._failure(
                request,
                "模型响应超时，请稍后重试。",
                "timeout",
                diagnostics,
                log_event=False,
            )
        except UsageLimitExceeded as exc:
            kind, limit, used = classify_usage_limit(exc)
            diagnostics.event(
                "agent_failed",
                error_code="limit",
                exception=exc,
                limit_kind=kind,
                limit_value=limit,
                used_value=deps.tool_calls if kind == "tool_calls" else used,
                projected_value=used if kind == "tool_calls" else None,
                agent_phase="retrieval",
            )
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            if partial := self._partial_read_fallback(request, deps):
                return partial
            return self._failure(
                request,
                self._limit_text(kind, phase="retrieval"),
                "limit",
                diagnostics,
                log_event=False,
            )
        except EmbeddingUnavailable:
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            return self._failure(
                request,
                "查询能力暂时不可用，请稍后重试。",
                "embedding_unavailable",
                diagnostics,
            )
        except RetrievalUnavailable:
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            return self._failure(
                request,
                "查询能力暂时不可用，请稍后重试。",
                "retrieval_unavailable",
                diagnostics,
            )
        except ModelHTTPError as exc:
            diagnostics.event(
                "agent_failed",
                error_code="runtime_error",
                exception=exc,
                http_status=exc.status_code,
                agent_phase="retrieval",
            )
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            if partial := self._partial_read_fallback(request, deps):
                return partial
            return self._failure(
                request,
                "知识库暂时无法完成检索，请稍后重试。",
                "runtime_error",
                diagnostics,
                log_event=False,
            )
        except UnexpectedModelBehavior as exc:
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.actions.input_mismatch:
                outcome = deps.actions.finalize_input_mismatch()
                diagnostics.event("action_validated", error_code=outcome.error_code)
                envelope = ResponseEnvelope.action(
                    status=outcome.status,
                    text=outcome.text,
                    action_code=outcome.error_code or "action_failed",
                    results=outcome.results,
                    error_code=outcome.error_code,
                )
                return AgentExecution(
                    envelope.project(thread_id=request.thread_public_id),
                    [],
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            diagnostics.event(
                "agent_failed",
                error_code="runtime_error",
                exception=exc,
                agent_phase="retrieval",
            )
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            if partial := self._partial_read_fallback(request, deps):
                return partial
            return self._failure(
                request,
                "知识库暂时无法完成检索，请稍后重试。",
                "runtime_error",
                diagnostics,
                log_event=False,
            )
        except KnowledgeNotFound:
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            return self._failure(
                request,
                "请求的知识片段不存在。",
                "not_found",
                diagnostics,
            )
        except Exception as exc:
            diagnostics.event(
                "agent_failed",
                error_code="runtime_error",
                exception=exc,
                agent_phase="retrieval",
            )
            if deps.actions.outcome is not None:
                return self._terminal_action_execution(
                    request, deps.actions.outcome, diagnostics
                )
            if deps.invalid_item_scope_attempt:
                return self._failure(
                    request,
                    "只能依据本轮已返回的条目继续限定检索。",
                    "item_scope_required",
                    diagnostics,
                    log_event=False,
                )
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            if self._search_completed_without_evidence(deps):
                return self._answer_pipeline.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            if partial := self._partial_read_fallback(request, deps):
                return partial
            return self._failure(
                request,
                "知识库暂时无法完成检索，请稍后重试。",
                "runtime_error",
                diagnostics,
                log_event=False,
            )

    async def _finalize_primary_result(
        self,
        request: AgentRequest,
        deps: AgentDeps,
        agent_result: Any,
        diagnostics: RequestDiagnostics,
        reference_scope: tuple[tuple[str, str], ...],
    ) -> AgentExecution:
        if deps.actions.outcome is not None:
            return self._terminal_action_execution(request, deps.actions.outcome, diagnostics)

        if deps.invalid_item_scope_attempt:
            return self._failure(
                request,
                "只能依据本轮已返回的条目继续限定检索。",
                "item_scope_required",
                diagnostics,
            )
        if deps.read_recovery_exhausted or deps.pending_read_failures:
            if deps.todo_store.snapshot.unfinished:
                try:
                    deps.todo_store.mark_blocked()
                except TodoValidationError:
                    pass
            if deps.actions.read_action_texts:
                diagnostics.event(
                    "recovery",
                    error_code="read_unavailable",
                    error_category="read_unavailable",
                    recovery_outcome="exhausted",
                    recovery_count=deps.recovery_ledger.remaining_actions,
                    agent_phase="retrieval",
                )
            if deps.citations:
                recovered = await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
                if recovered.answer.status == "ok":
                    recovered.answer.action_results = list(
                        deps.actions.read_action_results
                    )
                return recovered
            if deps.actions.read_action_texts:
                if partial := self._partial_read_fallback(request, deps):
                    return partial
            return self._failure(
                request,
                "后续读取暂时不可用，请稍后重试。",
                "read_unavailable",
                diagnostics,
                log_event=False,
            )

        natural_text = getattr(agent_result, "output", None)
        if not isinstance(natural_text, str):
            diagnostics.event("agent_failed", error_code="answer_unavailable", agent_phase="answer")
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            return self._failure(
                request,
                "暂时无法生成回答，请稍后重试。",
                "answer_unavailable",
                diagnostics,
                log_event=False,
            )
        try:
            deps.todo_store.finalize(
                allow_blocked=_allow_blocked_todo_clarification(deps, natural_text)
            )
        except TodoValidationError:
            diagnostics.event("agent_failed", error_code="todo_incomplete", agent_phase="retrieval")
            if deps.citations:
                return await self._answer_pipeline.recover_answer(
                    request, deps, diagnostics
                )
            return self._failure(
                request,
                "当前步骤尚未完成，请说明要继续哪一步。",
                "todo_incomplete",
                diagnostics,
                log_event=False,
            )

        if deps.search_calls < 1:
            if reference_scope:
                return self._failure(
                    request,
                    "未完成必要的知识库检索，因此不返回无来源答案。",
                    "search_required",
                    diagnostics,
                )
            if deps.actions.read_action_texts:
                read_text = "\n".join(deps.actions.read_action_texts)
                diagnostics.event("citation_validated", agent_phase="answer")
                envelope = ResponseEnvelope.canonical(
                    text=read_text,
                    template_key="management_read",
                    action_results=deps.actions.read_action_results,
                )
                return AgentExecution(
                    envelope.project(thread_id=request.thread_public_id),
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
                    action_results=list(deps.actions.read_action_results),
                    thread_id=request.thread_public_id,
                ),
                _canonical_history(request.question, validated.text),
            )

        if deps.successful_searches and not deps.citations:
            return self._answer_pipeline.no_evidence(
                request, diagnostics, deps.actions.read_action_results
            )

        # Once evidence exists, always use the structured Composer. A natural
        # answer with one citation cannot safely express which sentence is
        # unsupported; wrapping the whole text as one grounded section would
        # falsely attribute unsupported claims to that citation.
        if deps.citations:
            repaired = await self._answer_pipeline.recover_answer(
                request, deps, diagnostics
            )
            if repaired.answer.status == "ok":
                repaired.answer.action_results = list(deps.actions.read_action_results)
            return repaired

        # A reserved/skipped retrieval without a completed backend read is
        # neither clean empty evidence nor a safe natural-answer path.
        return self._failure(
            request,
            "暂时无法生成可靠回答，请稍后重试。",
            "answer_unavailable",
            diagnostics,
            log_event=False,
        )

    @staticmethod
    def _attach_read_observations(
        execution: AgentExecution,
        deps: AgentDeps,
    ) -> AgentExecution:
        if (
            execution.answer.status != "failed"
            and deps.actions.outcome is None
            and deps.actions.read_action_results
        ):
            execution.answer.action_results = list(deps.actions.read_action_results)
        return execution

    @staticmethod
    def _partial_read_fallback(
        request: AgentRequest,
        deps: AgentDeps,
    ) -> AgentExecution | None:
        if not deps.actions.read_action_texts:
            return None
        if deps.todo_store.snapshot.unfinished:
            try:
                deps.todo_store.mark_blocked()
            except TodoValidationError:
                pass
        partial_text = "\n".join(deps.actions.read_action_texts)
        partial_text += "\n" + BOUNDED_UNAVAILABLE_REMAINDER
        return AgentExecution(
            ResponseEnvelope.canonical(
                text=partial_text,
                template_key="partial_read",
                action_results=deps.actions.read_action_results,
            ).project(thread_id=request.thread_public_id),
            _canonical_history(request.question, partial_text),
        )

    @staticmethod
    def _terminal_action_execution(
        request: AgentRequest,
        outcome,
        diagnostics: RequestDiagnostics,
    ) -> AgentExecution:
        diagnostics.event("action_validated", error_code=outcome.error_code)
        envelope = ResponseEnvelope.action(
            status=outcome.status,
            text=outcome.text,
            action_code=outcome.error_code or "action_result",
            results=outcome.results,
            error_code=outcome.error_code,
        )
        return AgentExecution(
            envelope.project(thread_id=request.thread_public_id),
            _canonical_history(request.question, outcome.text)
            if outcome.history_visible
            else [],
        )

    @staticmethod
    def _bare_url_action(
        request: AgentRequest,
        actions: AgentActionRuntime,
        urls,
        diagnostics: RequestDiagnostics,
    ) -> AgentExecution:
        try:
            outcome = actions.request_confirmation(list(urls))
        except ActionInputMismatch:
            outcome = actions.finalize_input_mismatch()
        diagnostics.event("action_validated", error_code=outcome.error_code)
        envelope = ResponseEnvelope.action(
            status=outcome.status,
            text=outcome.text,
            action_code=outcome.error_code or "save_confirmation_required",
            results=outcome.results,
            error_code=outcome.error_code,
        )
        return AgentExecution(
            envelope.project(thread_id=request.thread_public_id),
            [],
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
            diagnostics.event("agent_failed", error_code=code, agent_phase="retrieval")
        envelope = ResponseEnvelope.failed(text=text, error_code=code)
        return AgentExecution(
            envelope.project(thread_id=request.thread_public_id),
            [],
        )


__all__ = ["KnowledgeAgent"]
