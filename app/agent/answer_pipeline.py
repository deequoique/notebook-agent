"""Tool-free bounded answer recovery and evidence rendering."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from pydantic_ai import (
    Agent,
    PromptedOutput,
    RunContext,
    UsageLimits,
)
from pydantic_ai.exceptions import (
    ModelHTTPError,
    ModelRetry,
    UnexpectedModelBehavior,
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

from app.agent.answer_validation import (
    NaturalAnswerValidationError,
    validate_natural_answer,
)
from app.agent.provider import composer_model_settings
from app.agent.runtime_state import (
    COMPRESSED_EVIDENCE_LIMIT,
    ComposerDeps,
    MAX_SOURCE_ITEMS,
    AgentDeps,
    AgentExecution,
    _citation_matches_scope,
)
from app.agent.response import (
    GroundedResponseSection,
    ResponseEnvelope,
    UNSUPPORTED_EVIDENCE_TEXT,
    UnsupportedResponseSection,
)
from app.agent.types import (
    AgentRequest,
    AnswerDraft,
    GroundedDraft,
    Citation,
)
from app.config import Settings
from app.diagnostics import RequestDiagnostics, classify_usage_limit


_COMPOSER_JSON_EXAMPLE = (
    '{"kind":"grounded","sections":[{"status":"grounded","text":"简洁回答","citation_ids":[123]}]}'
)
_COMPOSER_GLOBAL_CONSTRAINTS = (
    "完整约束：kind 只能是 grounded；grounded 的 sections 最多 8 个；"
    "每个 section 必须显式填写 status=grounded 或 status=unsupported；"
    "grounded section 必须有非空 text 和 citation_ids；unsupported section 必须只包含 status，"
    "不得填写 text 或 citation_ids，服务器会渲染固定的证据不足说明；"
    "只选择与问题相关的视频；每个选中的视频至少引用一个 segment，"
    "更重要的视频可以引用多个 segment；"
    "所有 section citation_ids 按 section 顺序合并后就是最终选择（最多 8 个），不得重复；"
    "所有 section 引用都必须是可用候选 ID；全部引用最多来自 5 个视频；"
    "只能使用可用候选证据中的 ID；section text 不得包含 URL、来源块或 [S…] 标记；"
    "不要为了给无证据 section 填充 Citation 而引用无关片段。"
)
_COMPOSER_SCHEMA_GUIDANCE = (
    "只返回一个符合 schema 的 JSON。结构示例（123 仅为示例，必须替换为可用候选 ID）："
    f"{_COMPOSER_JSON_EXAMPLE}\n{_COMPOSER_GLOBAL_CONSTRAINTS}"
)

COMPOSER_INSTRUCTIONS = f"""
你是私有知识库的回答编辑器。只能依据服务器提供的证据写回答，不能使用模型记忆补充事实。
输出结构化 sections；有证据的 section 写简洁中文文本并列出支持该 section 的 segment ID；
无法确认的 section 只输出 {{"status":"unsupported"}}，不要自行填写解释文本。
只在有助于阅读时使用克制的 Markdown：段落、短标题、有序/无序列表、强调、引用和行内代码。
简单回答保持简单，不要强制使用标题或列表。不要输出 URL、Markdown 链接、图片、原始 HTML、
“来源/参考资料”区块、视频标题、[S…] 标记、章节标题或服务器未提供的事实。
证据编号只能放在 section 的 citation_ids 字段，服务器会追加精确 [S<segment_id>] 标记；
不要在文本中改写、链接化、放入代码或替代这些标记。
回答只选择与问题相关的视频；每个选中的视频至少引用一个 segment。
{_COMPOSER_SCHEMA_GUIDANCE}
""".strip()


_COMPOSER_FAILURE_GUIDANCE: dict[str, str] = {
    "invalid_structure": (
        "校验类别 invalid_structure：上一轮没有返回可解析的 AnswerDraft。"
        f"本次请修正结构并重试。{_COMPOSER_SCHEMA_GUIDANCE}"
    ),
    "unsafe_text": (
        "校验类别 unsafe_text：section 的 text 不得包含 URL、来源块或 [S…]"
        "标记；引用只能放在 citation_ids 中。"
    ),
    "missing_citation": (
        "校验类别 missing_citation：整份回答至少要有一个 grounded section 引用可用证据 segment；"
        "无法确认的部分必须使用 status=unsupported，不能用空 citation_ids 伪装。"
    ),
    "unknown_citation": (
        "校验类别 unknown_citation：section citation_ids 只能选择可用证据中列出的"
        "segment ID，不要猜测、编造或引用其他轮次的 ID。"
    ),
    # Kept as a safe compatibility category for older callers; new drafts
    # use the more precise unknown/duplicate/scope categories above.
    "invalid_citation": (
        "校验类别 invalid_citation：section citation_ids 只能选择可用证据中列出的"
        "segment ID，不要猜测、编造或引用其他轮次的 ID。"
    ),
    "duplicate_citation": (
        "校验类别 duplicate_citation：每个 segment 只能在一个 section 中引用一次，"
        "不要重复列出相同 ID。"
    ),
    "too_many_segments": (
        "校验类别 too_many_segments：所有 section 合计去重后最多选择 8 个"
        "segment。"
    ),
    "too_many_items": (
        "校验类别 too_many_items：所有引用合计最多来自 5 个视频。"
    ),
    "missing_scope_item": (
        "校验类别 missing_scope_item：当前消息明确指定且已有证据的每个视频"
        "都必须至少选择一个 segment。"
    ),
    "provider_failure": (
        "校验类别 provider_failure：上一轮没有得到可交付的结构化结果。"
        "请重新直接返回符合 schema 的 JSON。"
    ),
}


def _composer_failure_guidance(reason: str | None) -> str:
    """Return fixed correction text for one allow-listed prior failure reason."""

    return _COMPOSER_FAILURE_GUIDANCE.get(reason or "", "")


def _draft_failure_reason(
    ctx: RunContext[ComposerDeps],
    draft: AnswerDraft,
) -> str | None:
    """Classify a rejected draft without retaining any model-authored content."""

    cited_ids = [
        segment_id
        for section in draft.sections
        if section.status == "grounded"
        for segment_id in section.citation_ids
    ]
    selected_ids = set(cited_ids)
    try:
        for section in draft.sections:
            if section.status == "grounded":
                # Pydantic validation already requires text for grounded
                # sections; keep this boundary check defensive and explicit.
                validate_natural_answer(section.text or "")
    except NaturalAnswerValidationError:
        return "unsafe_text"

    if not selected_ids:
        return "missing_citation"
    allowed = set(ctx.deps.citations)
    if any(segment_id not in allowed for segment_id in cited_ids):
        return "unknown_citation"
    if len(cited_ids) != len(selected_ids):
        return "duplicate_citation"
    if len(selected_ids) > ctx.deps.max_segments:
        return "too_many_segments"
    item_ids = {ctx.deps.citations[segment_id].item_id for segment_id in cited_ids}
    if len(item_ids) > MAX_SOURCE_ITEMS:
        return "too_many_items"
    if not ctx.deps.required_item_ids.issubset(item_ids):
        return "missing_scope_item"
    return None


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
        evidence = _render_composer_evidence(
            ctx.deps.citations.values(),
            excerpt_chars=ctx.deps.excerpt_chars,
        )
        guidance = _composer_failure_guidance(ctx.deps.last_failure_reason)
        return f"{guidance}\n\n{evidence}" if guidance else evidence

    @composer.output_validator
    def validate_draft(
        ctx: RunContext[ComposerDeps],
        draft: AnswerDraft,
    ) -> AnswerDraft:
        failure_reason = _draft_failure_reason(ctx, draft)
        if failure_reason is None:
            return draft
        ctx.deps.last_failure_reason = failure_reason
        ctx.deps.invalid_draft_count += 1
        if ctx.deps.diagnostics is not None:
            ctx.deps.diagnostics.event(
                "citation_validated",
                error_code="answer_unavailable",
                retry_count=ctx.deps.invalid_draft_count,
                failure_reason=failure_reason,
                agent_phase="answer",
            )
        raise ModelRetry(
            "回答必须只引用可用证据，并满足视频、片段和当前问题范围限制。"
        )

    return composer


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


def _render_sections(draft: GroundedDraft, citations: list[Citation]) -> str:
    """Render server-owned markers and source projection for a grounded draft."""

    return _append_sources(_render_grounded_sections(draft, citations), citations)


def _render_grounded_sections(draft: GroundedDraft, citations: list[Citation]) -> str:
    """Render only model sections plus server-owned Citation markers."""

    allowed = {citation.segment_id for citation in citations}
    sections: list[str] = []
    for section in draft.sections:
        if section.status == "unsupported":
            sections.append(UNSUPPORTED_EVIDENCE_TEXT)
            continue
        ids = [
            segment_id
            for segment_id in section.citation_ids
            if segment_id in allowed
        ]
        markers = " ".join(f"[S{segment_id}]" for segment_id in ids)
        sections.append(f"{(section.text or '').strip()} {markers}".strip())
    return "\n\n".join(sections)


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

    # Canonical, action, failed, and no-evidence dispositions must never gain
    # a dangling source heading merely because the caller has no selected
    # Citation set.
    if not citations:
        return text.rstrip()

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
    """Own bounded same-evidence answer recovery."""

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

    async def recover_answer(
        self,
        request: AgentRequest,
        deps: AgentDeps,
        diagnostics: RequestDiagnostics,
    ) -> AgentExecution:
        citations = [
            citation
            for citation in deps.citations.values()
            if not deps.reference_scope
            or _citation_matches_scope(citation, deps.reference_scope)
        ]
        if not citations:
            if (
                deps.successful_searches
                and not deps.pending_read_failures
                and not deps.read_recovery_exhausted
            ):
                return self.no_evidence(
                    request, diagnostics, deps.actions.read_action_results
                )
            return self._answer_unavailable(request, diagnostics, attempt=0)

        required_item_ids = (
            {citation.item_id for citation in citations}
            if deps.reference_scope
            else set()
        )
        # The answer agent judges relevance across the complete bounded
        # current-run evidence set.  Eight is an output-selection limit, not a
        # retrieval-order prefilter; otherwise an important ninth segment
        # could never be selected.
        candidates = citations
        composer_deps = ComposerDeps(
            {citation.segment_id: citation for citation in candidates},
            diagnostics=diagnostics,
            required_item_ids=frozenset(required_item_ids),
            max_segments=COMPRESSED_EVIDENCE_LIMIT,
        )
        attempts = 0
        last_error_category = "answer_validation"

        for attempt_index in range(1, 4):
            # Count the attempt before entering PydanticAI.  Provider failures
            # can occur while building or dispatching a request, before the
            # model-settings callback runs; those failures still consume one
            # of the three answer-agent attempts and must be observable with a
            # stable 1..3 index.
            attempts = attempt_index
            invalid_drafts_before_attempt = composer_deps.invalid_draft_count
            diagnostics.event(
                "model_attempt", call_index=attempt_index, agent_phase="answer"
            )
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
                        model_settings=dict(self.composer_model_settings),
                    )
            except UsageLimitExceeded as exc:
                last_error_category = "provider_failure"
                composer_deps.last_failure_reason = "provider_failure"
                kind, limit, used = classify_usage_limit(exc)
                diagnostics.event(
                    "agent_failed",
                    error_code="answer_unavailable",
                    error_class=type(exc).__name__,
                    call_index=attempts,
                    limit_kind=kind,
                    limit_value=limit,
                    used_value=used,
                    error_category=last_error_category,
                    failure_reason=composer_deps.last_failure_reason,
                    agent_phase="answer",
                )
                continue
            except ModelHTTPError as exc:
                last_error_category = "provider_failure"
                composer_deps.last_failure_reason = "provider_failure"
                diagnostics.event(
                    "agent_failed",
                    error_code="answer_unavailable",
                    error_class=type(exc).__name__,
                    call_index=attempts,
                    http_status=exc.status_code,
                    error_category=last_error_category,
                    failure_reason=composer_deps.last_failure_reason,
                    agent_phase="answer",
                )
                continue
            except (ModelRetry, UnexpectedModelBehavior) as exc:
                last_error_category = "answer_validation"
                if (
                    composer_deps.invalid_draft_count
                    == invalid_drafts_before_attempt
                ):
                    composer_deps.last_failure_reason = "invalid_structure"
                diagnostics.event(
                    "agent_failed",
                    error_code="answer_unavailable",
                    error_class=type(exc).__name__,
                    call_index=attempts,
                    error_category=last_error_category,
                    failure_reason=composer_deps.last_failure_reason,
                    agent_phase="answer",
                )
                continue
            except Exception as exc:
                last_error_category = "provider_failure"
                composer_deps.last_failure_reason = "provider_failure"
                diagnostics.event(
                    "agent_failed",
                    error_code="answer_unavailable",
                    error_class=type(exc).__name__,
                    call_index=attempts,
                    error_category=last_error_category,
                    failure_reason=composer_deps.last_failure_reason,
                    agent_phase="answer",
                )
                continue

            selected_ids = tuple(
                segment_id
                for section in result.output.sections
                for segment_id in section.citation_ids
            )
            selected = [
                next(
                    citation
                    for citation in candidates
                    if citation.segment_id == segment_id
                )
                for segment_id in selected_ids
            ]
            grounded_sections = tuple(
                (
                    GroundedResponseSection(
                        "grounded", section.text or "", tuple(section.citation_ids)
                    )
                    if section.status == "grounded"
                    else UnsupportedResponseSection("unsupported")
                )
                for section in result.output.sections
            )
            diagnostics.event(
                "citation_validated",
                call_index=attempts,
                result_count=len(selected),
                retry_count=composer_deps.invalid_draft_count,
                agent_phase="answer",
            )
            envelope = ResponseEnvelope.grounded(
                sections=grounded_sections,
                citations=selected,
                action_results=deps.actions.read_action_results,
            )
            answer_text = envelope.project(thread_id=request.thread_public_id).text
            return AgentExecution(
                envelope.project(thread_id=request.thread_public_id),
                _canonical_history(request.question, answer_text),
            )

        return self._answer_unavailable(
            request,
            diagnostics,
            attempt=attempts,
            error_category=last_error_category,
            failure_reason=composer_deps.last_failure_reason,
        )

    @staticmethod
    def _answer_unavailable(
        request: AgentRequest,
        diagnostics: RequestDiagnostics,
        *,
        attempt: int,
        error_category: str = "answer_validation",
        failure_reason: str | None = None,
    ) -> AgentExecution:
        diagnostics.event(
            "agent_failed",
            error_code="answer_unavailable",
            call_index=attempt,
            error_category=error_category,
            failure_reason=failure_reason,
            agent_phase="answer",
        )
        text = "暂时无法生成可靠回答，请稍后重试。"
        envelope = ResponseEnvelope.failed(text=text, error_code="answer_unavailable")
        return AgentExecution(
            envelope.project(thread_id=request.thread_public_id),
            [],
        )

    @staticmethod
    def no_evidence(
        request: AgentRequest,
        diagnostics: RequestDiagnostics,
        action_results=(),
    ) -> AgentExecution:
        """Project the server-owned no-evidence disposition.

        Candidate rows are deliberately not passed into this envelope.  The
        public adapter therefore cannot attach source blocks to a no-evidence
        result merely because a search returned unrelated candidates.
        """

        diagnostics.event(
            "citation_validated",
            error_code="no_evidence",
            disposition="no_evidence",
            result_count=0,
            agent_phase="answer",
        )
        envelope = ResponseEnvelope.no_evidence(action_results=action_results)
        return AgentExecution(
            envelope.project(thread_id=request.thread_public_id),
            [],
        )
