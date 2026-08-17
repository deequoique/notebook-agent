"""Tool-free bounded answer repair, evidence rendering, and fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Literal

from pydantic_ai import (
    Agent,
    PromptedOutput,
    RunContext,
    UsageLimits,
)
from pydantic_ai.exceptions import (
    ModelHTTPError,
    ModelRetry,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from app.agent.answer_validation import NaturalAnswerValidationError
from app.agent.autonomy import ErrorEnvelope
from app.agent.runtime_state import (
    COMPRESSED_EVIDENCE_LIMIT,
    ComposerDeps,
    MAX_SOURCE_ITEMS,
    AgentDeps,
    AgentExecution,
)
from app.agent.types import AgentAnswer, AgentRequest, AnswerDraft, Citation
from app.config import Settings
from app.diagnostics import RequestDiagnostics, classify_usage_limit
from app.agent.provider import composer_model_settings


COMPOSER_INSTRUCTIONS = """
你是私有知识库的回答编辑器。只能依据服务器提供的证据写回答，不能使用模型记忆补充事实。
输出结构化 sections；每个 section 写简洁中文文本，并列出支持该 section 的 segment ID。
不要输出 URL、视频标题、[S…] 标记、章节标题或服务器未提供的事实。最多引用五个不同视频。
""".strip()


def build_composer(
    model: Model | str,
    *,
    tool_timeout: float = 15.0,
    output_retries: int = 0,
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


class AnswerPipeline:
    """Own bounded same-evidence repair and deterministic evidence fallback."""

    def __init__(
        self,
        composer: Agent[ComposerDeps, AnswerDraft],
        *,
        composer_model_settings: dict,
        settings: Settings,
    ) -> None:
        self.composer = composer
        self.composer_model_settings = composer_model_settings
        self.settings = settings

    async def repair_bounded_answer(
        self,
        request: AgentRequest,
        deps: AgentDeps,
        diagnostics: RequestDiagnostics,
    ) -> AgentExecution:
        citations = _limit_citations_by_item(list(deps.citations.values()))
        ledger = deps.recovery_ledger
        policy = deps.recovery_policy
        if ledger is None or policy is None:
            return self.evidence_fallback(request, citations)
        error = ErrorEnvelope.from_category(
            "answer_validation",
            operation="answer",
            partial_evidence=True,
        )
        grant = policy.grant(
            error,
            has_evidence=True,
            request_budget_remaining=self.settings.agent_request_limit,
            provider_budget_remaining=1,
            answer_budget_remaining=1,
            output_budget_remaining=self.settings.agent_output_token_limit,
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
            return self.evidence_fallback(request, citations)
        diagnostics.event(
            "recovery",
            error_code=error.code,
            error_category=error.category,
            recovery_action="repair_answer",
            recovery_outcome="consumed",
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
            return dict(self.composer_model_settings)

        try:
            async with asyncio.timeout(self.settings.agent_timeout_seconds):
                result = await self.composer.run(
                    request.question.strip(),
                    deps=composer_deps,
                    usage_limits=UsageLimits(
                        request_limit=1,
                        output_tokens_limit=self.settings.agent_output_token_limit,
                    ),
                    usage=RunUsage(),
                    model_settings=record_answer_attempt,
                )
        except UsageLimitExceeded as exc:
            kind, limit, used = classify_usage_limit(exc)
            diagnostics.event(
                "agent_failed",
                error_code="answer_unavailable",
                exception=exc,
                limit_kind=kind,
                limit_value=limit,
                used_value=used,
                agent_phase="answer",
            )
            return self.evidence_fallback(request, citations)
        except ModelHTTPError as exc:
            diagnostics.event(
                "agent_failed",
                error_code="answer_unavailable",
                exception=exc,
                http_status=exc.status_code,
                agent_phase="answer",
            )
            return self.evidence_fallback(request, citations)
        except Exception as exc:
            diagnostics.event(
                "agent_failed",
                error_code="answer_unavailable",
                exception=exc,
                agent_phase="answer",
            )
            return self.evidence_fallback(request, citations)

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

    @staticmethod
    def evidence_fallback(
        request: AgentRequest,
        citations: list[Citation],
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


FALLBACK_INTRO = "自动总结未完成，以下是知识库中最相关的证据："
