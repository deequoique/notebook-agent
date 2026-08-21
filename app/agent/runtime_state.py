"""Run-scoped dependencies and execution contracts for the Agent runtime."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from pydantic_ai.messages import ModelMessage

from app.agent.actions import AgentActionRuntime
from app.agent.autonomy import RecoveryLedger, RecoveryPolicy, TodoValidationError, TurnTodoStore
from app.agent.context import TurnContext
from app.agent.services import KnowledgeServices
from app.agent.types import AgentAnswer, Citation
from app.diagnostics import RequestDiagnostics
from app.ingest.submission import normalize_item_reference

NORMAL_RETRIEVAL_CALLS_LIMIT = 5
NORMAL_SEARCH_CALLS_LIMIT = 2
NORMAL_EXPANSION_CALLS_LIMIT = 3
MAX_SOURCE_ITEMS = 5
COMPOSER_EVIDENCE_EXCERPT_CHARS = 360
COMPRESSED_EVIDENCE_LIMIT = 8


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


def _citation_matches_scope(
    citation: Citation,
    scope: tuple[tuple[str, str], ...],
) -> bool:
    """Validate a citation URL against the current-message subject set."""

    try:
        reference = normalize_item_reference(citation.url)
    except (ValueError, TypeError):
        return False
    return (reference.platform, reference.platform_id) in set(scope)


@dataclass
class AgentDeps:
    """Trusted, mutable state shared by one primary Agent run."""

    services: KnowledgeServices
    actions: AgentActionRuntime
    search_calls: int = 0
    # A search reservation is not evidence of a successful read.  This flag
    # is set only after the backend returned (including an empty result), and
    # gates the server-owned no-evidence disposition.
    successful_searches: int = 0
    retrieval_calls: int = 0
    expansion_calls: int = 0
    tool_calls: int = 0
    citations: dict[int, Citation] = field(default_factory=dict)
    last_retrieval_run_step: int | None = None
    diagnostics: RequestDiagnostics | None = None
    # Legacy exact-reference compatibility for trusted callers.  The normal
    # URL-plus-question route leaves this empty so retrieval remains tenant-wide.
    reference_scope: tuple[tuple[str, str], ...] = ()
    # URL context must not become a retrieval scope, but it still marks a
    # semantic content question so save/confirmation tools cannot turn a
    # content request into an unrelated mutation.
    semantic_url_question: bool = False
    reference_save_requested: bool = False
    # Present only on the opt-in bounded-autonomy path. It is intentionally
    # turn-local and never copied into AgentRequest/history or diagnostics.
    todo_store: TurnTodoStore | None = None
    # Trusted, bounded prior-turn focus. Item ids in this projection are
    # references only; KnowledgeServices repeats tenant/state checks.
    context: TurnContext = field(default_factory=TurnContext)
    invalid_item_scope_attempt: bool = False
    recovery_ledger: RecoveryLedger | None = None
    recovery_policy: RecoveryPolicy | None = None
    # Pending transient reads are keyed only by a server-side digest. The
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
                citation, self.reference_scope
            ):
                continue
            self.citations[citation.segment_id] = citation

    def can_scope_search_to_item(self, item_id: int | None) -> bool:
        """Validate only the shape of an optional item narrowing.

        Item IDs are model-selected query parameters, not authorization.  The
        tenant-bound KnowledgeServices query repeats ownership, deletion,
        archive, readiness, and segment/item predicates for every lookup.
        Keeping this check syntactic prevents malformed tool input without
        requiring a prior inventory observation (which would unnecessarily
        constrain tenant-wide retrieval).
        """

        if item_id is None:
            return True
        return not (
            isinstance(item_id, bool)
            or not isinstance(item_id, int)
            or item_id <= 0
        )

    def tool_event(
        self,
        name: str,
        outcome: str,
        call_index: int,
        result_count: int | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if self.diagnostics is not None:
            self.diagnostics.event(
                "tool_call",
                tool_name=name,
                tool_outcome=outcome,
                call_index=call_index,
                result_count=result_count,
                exception=exception,
                agent_phase="retrieval",
            )


@dataclass
class ComposerDeps:
    """Trusted allow-list passed to the tool-free answer composer only."""

    citations: dict[int, Citation]
    excerpt_chars: int = COMPOSER_EVIDENCE_EXCERPT_CHARS
    diagnostics: RequestDiagnostics | None = None
    invalid_draft_count: int = 0
    # Fixed allow-listed category from the previous answer attempt. It is
    # rendered as correction guidance only; no prior draft or model payload is
    # retained between attempts.
    last_failure_reason: str | None = None
    required_item_ids: frozenset[int] = frozenset()
    max_segments: int = COMPRESSED_EVIDENCE_LIMIT


@dataclass(frozen=True)
class AgentExecution:
    """Public answer plus canonical messages produced by one product run."""

    answer: AgentAnswer
    new_messages: list[ModelMessage]


__all__ = [
    "AgentDeps",
    "AgentExecution",
    "COMPOSER_EVIDENCE_EXCERPT_CHARS",
    "COMPRESSED_EVIDENCE_LIMIT",
    "ComposerDeps",
    "MAX_SOURCE_ITEMS",
    "NORMAL_EXPANSION_CALLS_LIMIT",
    "NORMAL_RETRIEVAL_CALLS_LIMIT",
    "NORMAL_SEARCH_CALLS_LIMIT",
    "ReservationResult",
    "RetrievalKind",
    "_RecoveryPayload",
    "_citation_matches_scope",
]
