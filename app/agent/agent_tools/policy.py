"""Shared dynamic tool gating, accounting, and recovery policy.

Tool registrations use this object for server-owned budgets, visibility, and
bounded read recovery. It has no dependency on product orchestration.
"""

from __future__ import annotations

from typing import Literal

from pydantic_ai import RunContext

from app.agent.autonomy import ErrorEnvelope, RecoveryGrant
from app.agent.runtime_state import (
    AgentDeps,
    NORMAL_EXPANSION_CALLS_LIMIT,
    NORMAL_RETRIEVAL_CALLS_LIMIT,
    NORMAL_SEARCH_CALLS_LIMIT,
    ReservationResult,
    RetrievalKind,
    _RecoveryPayload,
)
from app.agent.services import (
    EmbeddingUnavailable,
    KnowledgeNotFound,
    RetrievalUnavailable,
)
from app.agent.types import RetrievalToolPayload


class _TransientReadUnavailable(RuntimeError):
    """Private signal for an expected read failure with no model-visible detail."""


class ToolPolicy:
    """Run-independent policy shared by the always-on primary Agent."""

    def normal_retrieval_available(self, deps: AgentDeps) -> bool:
        if deps.retrieval_calls >= NORMAL_RETRIEVAL_CALLS_LIMIT:
            return False
        return (
            deps.search_calls < NORMAL_SEARCH_CALLS_LIMIT
            or (
                bool(deps.citations)
                and deps.expansion_calls < NORMAL_EXPANSION_CALLS_LIMIT
            )
        )

    def prepare_search(self, ctx: RunContext[AgentDeps], tool_def):
        deps = ctx.deps
        if any(
            tool_name == "search_segments"
            for tool_name in deps.pending_read_failures.values()
        ):
            # Keep the tool visible long enough for the server to return a
            # safe denied/exhausted envelope on a repeated failed call.  The
            # read helper still prevents any third backend attempt.
            return tool_def
        if (
            self.normal_retrieval_available(deps)
            and deps.search_calls < NORMAL_SEARCH_CALLS_LIMIT
        ):
            return tool_def
        return None

    def prepare_expansion(self, ctx: RunContext[AgentDeps], tool_def):
        deps = ctx.deps
        if (
            deps.citations
            and self.normal_retrieval_available(deps)
            and deps.expansion_calls < NORMAL_EXPANSION_CALLS_LIMIT
        ):
            return tool_def
        return None

    def prepare_management(self, ctx: RunContext[AgentDeps], tool_def):
        """Hide inventory/item-management tools for explicit URL questions."""

        if ctx.deps.reference_scope:
            return None
        return tool_def

    def prepare_pending_delete(self, ctx: RunContext[AgentDeps], tool_def):
        """Expose delete decision tools only for a trusted pending delete."""

        if ctx.deps.reference_scope:
            return None
        snapshot = ctx.deps.actions.pending_delete_snapshot()
        if snapshot is None or not getattr(snapshot, "active", False):
            return None
        return tool_def

    def prepare_bare_url_action(self, ctx: RunContext[AgentDeps], tool_def):
        """Bare URLs are handled before the model; scoped prompts cannot use it."""

        return None

    def prepare_save(self, ctx: RunContext[AgentDeps], tool_def):
        """Require an explicit current-message save command for scoped URLs."""

        if not ctx.deps.actions.save_enabled:
            return None
        if ctx.deps.reference_scope and not ctx.deps.reference_save_requested:
            return None
        return tool_def

    def prepare_pending_save(self, ctx: RunContext[AgentDeps], tool_def):
        """Hide pending save decisions during explicit URL questions."""

        if ctx.deps.reference_scope:
            return None
        if not ctx.deps.actions.save_enabled:
            return None
        if not ctx.deps.actions.pending_save_snapshot().active:
            return None
        return tool_def

    def prepare_no_relevant_evidence(self, ctx: RunContext[AgentDeps], tool_def):
        """Expose the no-evidence disposition only after a clean search."""

        deps = ctx.deps
        if (
            deps.successful_searches > 0
            and not deps.pending_read_failures
            and not deps.read_recovery_exhausted
            and deps.actions.outcome is None
        ):
            return tool_def
        return None

    def execute_tool(self, deps: AgentDeps, name: str, operation):
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

    def optional_knowledge(self, operation):
        """Convert an expected scoped miss into an empty successful lookup."""

        try:
            return operation()
        except KnowledgeNotFound:
            return None

    def _recovery_diagnostic(
        self,
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

    def _recovery_payload(
        self,
        error: ErrorEnvelope,
        grant: RecoveryGrant,
    ) -> _RecoveryPayload:
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
        self,
        ctx: RunContext[AgentDeps],
        *,
        tool_name: str,
        arguments: dict,
        operation,
    ) -> tuple[object, int] | _RecoveryPayload:
        """Execute one bounded read, granting at most one exact retry."""

        deps = ctx.deps
        ledger = deps.recovery_ledger
        policy = deps.recovery_policy
        if ledger is None or policy is None:
            # Recovery state is required by the only supported runtime path;
            # keep a defensive server-side fallback for direct tool tests.
            return self.execute_tool(deps, tool_name, operation)
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
            self._recovery_diagnostic(
                deps, error=error, grant=grant, outcome="denied"
            )
            return self._recovery_payload(error, grant)

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
            if (
                not grant.permits("retry_same_read")
                or not ledger.record_same_read_retry(fingerprint)
            ):
                deps.read_recovery_exhausted = True
                self._recovery_diagnostic(
                    deps, error=error, grant=grant, outcome="exhausted"
                )
                return self._recovery_payload(error, grant)
            self._recovery_diagnostic(
                deps,
                error=error,
                grant=grant,
                action="retry_same_read",
                outcome="consumed",
            )

        try:
            value, call_index = self.execute_tool(deps, tool_name, operation)
        except (
            EmbeddingUnavailable,
            RetrievalUnavailable,
            _TransientReadUnavailable,
        ):
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
            self._recovery_diagnostic(deps, error=error, grant=grant)
            return self._recovery_payload(error, grant)
        else:
            if retrying:
                deps.pending_read_failures.pop(fingerprint, None)
            return value, call_index

    def skipped_payload(
        self,
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

    def run_management_read(
        self,
        ctx: RunContext[AgentDeps],
        *,
        tool_name: str,
        arguments: dict,
        operation,
    ) -> dict:
        """Return a composable read observation or a safe recovery envelope."""

        def checked_operation():
            outcome = operation()
            if (
                outcome.status == "failed"
                and outcome.error_code == "management_failed"
            ):
                raise _TransientReadUnavailable()
            return outcome

        read_result = self._read_failure(
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
