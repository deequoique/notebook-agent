"""Retrieval and evidence-expansion tool registration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from pydantic import Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from app.agent.autonomy import ErrorEnvelope, RecoveryGrant
from app.agent.limits import SEARCH_RESULT_LIMIT
from app.agent.runtime_state import (
    AgentDeps,
    NORMAL_RETRIEVAL_CALLS_LIMIT,
    NORMAL_SEARCH_CALLS_LIMIT,
    RetrievalKind,
    _RecoveryPayload,
    _citation_matches_scope,
)
from app.agent.agent_tools.policy import ToolPolicy
from app.agent.types import RetrievalToolPayload
from app.ingest.submission import normalize_item_reference


def _item_details_matches_scope(
    result: dict,
    scope: tuple[tuple[str, str], ...],
) -> bool:
    try:
        reference = normalize_item_reference(str(result.get("url", "")))
    except (ValueError, TypeError):
        return False
    return (
        result.get("platform") == reference.platform
        and (reference.platform, reference.platform_id) in set(scope)
    )


def register_retrieval_tools(agent: Agent, policy: ToolPolicy) -> None:
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
            if ctx.deps.recovery_ledger is not None
            else None
        )
        if fingerprint is not None:
            if fingerprint == ctx.deps.last_empty_search_fingerprint:
                # Repeating an empty query is still the same observation, not
                # a reformulation and not a backend call.
                return {
                    "status": "ok",
                    "evidence": [],
                    "reason": None,
                }
            if ctx.deps.last_empty_search_fingerprint is not None:
                recovery_policy = ctx.deps.recovery_policy
                ledger = ctx.deps.recovery_ledger
                grant = (
                    recovery_policy.grant_for_empty_search(
                        search_budget_remaining=(
                            NORMAL_SEARCH_CALLS_LIMIT - ctx.deps.search_calls
                        ),
                        retrieval_budget_remaining=(
                            NORMAL_RETRIEVAL_CALLS_LIMIT - ctx.deps.retrieval_calls
                        ),
                    )
                    if recovery_policy is not None
                    else RecoveryGrant((), 0)
                )
                if (
                    not grant.permits("reformulate_search")
                    or ledger is None
                    or not ledger.record_recovery(
                        "reformulate_search",
                        category="transient_read",
                    )
                ):
                    ctx.deps.read_recovery_exhausted = True
                    if recovery_policy is not None:
                        error = ErrorEnvelope.from_category(
                            "read_unavailable",
                            operation="read",
                            code="retry_not_allowed",
                            partial_evidence=bool(ctx.deps.citations),
                        )
                        policy._recovery_diagnostic(
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
                policy._recovery_diagnostic(
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
            any(
                tool_name == "search_segments"
                for tool_name in ctx.deps.pending_read_failures.values()
            )
            and not pending_same
        )
        if not (retry_already_used or pending_other):
            if skipped := policy.skipped_payload(
                ctx,
                "search_segments",
                RetrievalKind.SEARCH,
            ):
                return skipped

        if item_id is None:
            operation = lambda: ctx.deps.services.search_segments(
                normalized_query, limit=limit
            )
        else:
            operation = lambda: ctx.deps.services.search_segments(
                normalized_query, limit=limit, item_id=item_id
            )
        read_result = policy._read_failure(
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
        ctx.deps.tool_event(
            "search_segments", "succeeded", call_index, len(citations)
        )
        if diagnostics := ctx.deps.diagnostics:
            diagnostics.retrieval_detail(
                tool_name="search_segments",
                call_index=call_index,
                query=query,
                limit=limit,
            )
            for citation in citations:
                diagnostics.retrieval_detail(
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
        if ctx.deps.recovery_ledger is not None:
            if citations:
                ctx.deps.last_empty_search_fingerprint = None
            elif fingerprint is not None:
                ctx.deps.last_empty_search_fingerprint = fingerprint
        return {
            "status": "ok",
            "evidence": [value.model_dump() for value in citations],
            "reason": None,
        }

    @agent.tool(prepare=policy.prepare_search)
    def search_segments(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = SEARCH_RESULT_LIMIT,
        item_id: Annotated[int | None, Field(gt=0)] = None,
    ) -> dict:
        """Search globally or within a trusted current-run item reference."""

        return _run_search_segments(ctx, query, limit, item_id)

    @agent.tool(prepare=policy.prepare_expansion)
    def get_neighbors(
        ctx: RunContext[AgentDeps], segment_id: int, radius: int = 1
    ) -> dict:
        """Read nearby segments around a search result."""

        if skipped := policy.skipped_payload(
            ctx,
            "get_neighbors",
            RetrievalKind.EXPANSION,
        ):
            return skipped
        operation = lambda: policy.optional_knowledge(
            lambda: ctx.deps.services.get_neighbors(segment_id, radius=radius)
        )
        read_result = policy._read_failure(
            ctx,
            tool_name="get_neighbors",
            arguments={"segment_id": int(segment_id), "radius": int(radius)},
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        citations, call_index = read_result
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
        ctx.deps.tool_event(
            "get_neighbors", "succeeded", call_index, len(citations)
        )
        if diagnostics := ctx.deps.diagnostics:
            for citation in citations:
                diagnostics.retrieval_detail(
                    tool_name="get_neighbors",
                    call_index=call_index,
                    segment_id=citation.segment_id,
                    radius=radius,
                    item_id=citation.item_id,
                    title=citation.title,
                    url=citation.url,
                    excerpt=citation.excerpt,
                    start=citation.start_sec,
                )
        return {
            "status": "ok",
            "evidence": [value.model_dump() for value in citations],
            "reason": None,
        }

    @agent.tool(prepare=policy.prepare_expansion)
    def get_item(ctx: RunContext[AgentDeps], item_id: int) -> dict:
        """Read metadata for a knowledge item returned by search."""

        if skipped := policy.skipped_payload(
            ctx,
            "get_item",
            RetrievalKind.EXPANSION,
        ):
            return skipped
        operation = lambda: policy.optional_knowledge(
            lambda: ctx.deps.services.get_item(item_id)
        )
        read_result = policy._read_failure(
            ctx,
            tool_name="get_item",
            arguments={"item_id": int(item_id)},
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        details, call_index = read_result
        result = asdict(details) if details is not None else {}
        if ctx.deps.reference_scope and result and not _item_details_matches_scope(
            result, ctx.deps.reference_scope
        ):
            result = {}
        ctx.deps.tool_event(
            "get_item", "succeeded", call_index, 1 if result else 0
        )
        if diagnostics := ctx.deps.diagnostics:
            diagnostics.retrieval_detail(
                tool_name="get_item",
                call_index=call_index,
                item_id=item_id,
                title=result.get("title"),
                author=result.get("author"),
                description=result.get("description"),
                url=result.get("url"),
            )
        return {
            "status": "ok",
            "evidence": [result] if result else [],
            "reason": None,
        }

    @agent.tool(prepare=policy.prepare_expansion)
    def open_at(ctx: RunContext[AgentDeps], segment_id: int) -> dict:
        """Resolve a segment to a clickable timestamp or article anchor."""

        if skipped := policy.skipped_payload(
            ctx,
            "open_at",
            RetrievalKind.EXPANSION,
        ):
            return skipped
        operation = lambda: policy.optional_knowledge(
            lambda: ctx.deps.services.open_at(segment_id)
        )
        read_result = policy._read_failure(
            ctx,
            tool_name="open_at",
            arguments={"segment_id": int(segment_id)},
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        citation, call_index = read_result
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
            if diagnostics := ctx.deps.diagnostics:
                diagnostics.retrieval_detail(
                    tool_name="open_at",
                    call_index=call_index,
                    segment_id=segment_id,
                    item_id=citation.item_id,
                    title=citation.title,
                    url=citation.url,
                    excerpt=citation.excerpt,
                    start=citation.start_sec,
                )
        return {
            "status": "ok",
            "evidence": [citation.model_dump()] if citation is not None else [],
            "reason": None,
        }
