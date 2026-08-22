"""Trusted internal response envelope.

The public ``AgentAnswer`` shape remains compatible with channel, MCP, and
HTTP callers. Internally, visible text is rendered only from typed sections;
model text cannot smuggle in sources, URLs, or server-owned actions.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from app.agent.types import AgentAnswer, Citation


ResponseDisposition = Literal[
    "grounded", "no_evidence", "canonical", "action", "failed"
]
_DISPOSITIONS = frozenset({"grounded", "no_evidence", "canonical", "action", "failed"})
_STATUSES = frozenset({"ok", "not_found", "failed"})

CANONICAL_TEMPLATE_KEYS = frozenset({"no_evidence", "management_read", "partial_read"})

ACTION_CODES = frozenset(
    {
        "action_failed", "action_result", "action_url_batch_mismatch", "already_exists",
        "batch_too_large", "cancelled", "confirmation_expired", "confirmation_missing",
        "confirmation_required", "create_failed", "delete_cancelled", "delete_failed",
        "delete_in_progress", "effect_failed", "effect_in_progress", "empty_batch",
        "invalid_batch", "invalid_cursor", "invalid_url", "invalid_why_saved",
        "item_not_found", "item_read", "item_updated", "items_deleted", "items_listed",
        "items_restored", "management_failed", "management_unavailable", "purge_in_progress",
        "queue_unavailable", "quota_exceeded", "retry_not_allowed", "retry_queued",
        "save_accepted", "save_cancelled", "save_confirmation_required", "save_failed",
        "save_partial", "save_unavailable", "unsupported_url",
    }
)

ERROR_CODES = frozenset(
    {
        "answer_unavailable", "embedding_unavailable", "invalid_envelope", "item_scope_required",
        "limit", "no_evidence", "not_found", "read_unavailable", "retrieval_unavailable",
        "runtime_error", "search_required", "timeout", "todo_incomplete", "management_failed",
        "management_unavailable", "confirmation_expired", "confirmation_missing", "invalid_batch",
        "invalid_cursor", "invalid_url", "invalid_why_saved", "item_not_found", "queue_unavailable",
        "save_failed", "save_unavailable", "unsupported_url", "batch_too_large", "empty_batch",
        "delete_failed", "delete_in_progress", "delete_cancelled", "items_listed", "item_read",
        "item_updated", "items_deleted", "items_restored", "retry_queued", "retry_not_allowed",
    }
)

_URL_PATTERN = re.compile(r"(?:https?://|ftp://|www\.)", re.IGNORECASE)
_CITATION_PATTERN = re.compile(r"\[\s*[Ss][^\]]*\]")
_SOURCE_BLOCK_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:sources?|references?|来源|参考来源)\s*(?:[:：]|$)"
)
NO_EVIDENCE_TEXT = "知识库中未找到足够证据。"
UNSUPPORTED_EVIDENCE_TEXT = "当前检索证据不足以确认该部分。"


def _require_safe_grounded_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("grounded section text must not be empty")
    normalized = text.strip()
    if _URL_PATTERN.search(normalized):
        raise ValueError("grounded section must not contain model-authored URLs")
    if _CITATION_PATTERN.search(normalized):
        raise ValueError("grounded section must not contain model-authored citations")
    if _SOURCE_BLOCK_PATTERN.search(normalized):
        raise ValueError("grounded section must not contain a source block")
    return normalized


def _require_text(text: str, *, label: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{label} text must not be empty")
    return text.strip()


def _require_results(results: tuple[dict, ...] | list[dict], *, label: str) -> tuple[dict, ...]:
    values = tuple(results)
    if any(not isinstance(value, dict) for value in values):
        raise ValueError(f"{label} results must be objects")
    return values


@dataclass(frozen=True, slots=True)
class GroundedResponseSection:
    kind: Literal["grounded"]
    text: str
    citation_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.kind != "grounded":
            raise ValueError("grounded section kind mismatch")
        _require_safe_grounded_text(self.text)
        if not self.citation_ids or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.citation_ids
        ):
            raise ValueError("grounded section citation_ids must be positive integers")


@dataclass(frozen=True, slots=True)
class UnsupportedResponseSection:
    """A server-owned statement that the available evidence cannot confirm."""

    kind: Literal["unsupported"]

    @property
    def text(self) -> str:
        """Return only the fixed server-owned unsupported notice."""

        return UNSUPPORTED_EVIDENCE_TEXT

    def __post_init__(self) -> None:
        if self.kind != "unsupported":
            raise ValueError("unsupported section kind mismatch")


@dataclass(frozen=True, slots=True)
class CanonicalResponseSection:
    kind: Literal["canonical"]
    template_key: str
    text: str

    def __post_init__(self) -> None:
        if self.kind != "canonical":
            raise ValueError("canonical section kind mismatch")
        if self.template_key not in CANONICAL_TEMPLATE_KEYS:
            raise ValueError("unregistered canonical template key")
        _require_text(self.text, label="canonical")


@dataclass(frozen=True, slots=True)
class ActionResponseSection:
    kind: Literal["action"]
    action_code: str
    text: str
    results: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if self.kind != "action":
            raise ValueError("action section kind mismatch")
        if self.action_code not in ACTION_CODES:
            raise ValueError("unregistered action code")
        _require_text(self.text, label="action")
        _require_results(self.results, label="action")


@dataclass(frozen=True, slots=True)
class FailedResponseSection:
    kind: Literal["failed"]
    error_code: str
    text: str

    def __post_init__(self) -> None:
        if self.kind != "failed":
            raise ValueError("failed section kind mismatch")
        if self.error_code not in ERROR_CODES:
            raise ValueError("unregistered error code")
        _require_text(self.text, label="failed")


ResponseSection = (
    GroundedResponseSection
    | UnsupportedResponseSection
    | CanonicalResponseSection
    | ActionResponseSection
    | FailedResponseSection
)


def _render_grounded_text(
    sections: tuple[GroundedResponseSection | UnsupportedResponseSection, ...],
    citations: tuple[Citation, ...],
) -> str:
    visible_sections: list[str] = []
    for section in sections:
        if isinstance(section, UnsupportedResponseSection):
            visible_sections.append(section.text.strip())
            continue
        markers = " ".join(f"[S{segment_id}]" for segment_id in section.citation_ids)
        visible_sections.append(f"{section.text} {markers}".strip())
    groups: dict[int, list[Citation]] = {}
    for citation in citations:
        groups.setdefault(citation.item_id, []).append(citation)
    lines = ["\n\n".join(visible_sections), "", "来源："]
    for group in groups.values():
        lines.append(f"- {group[0].title}")
        for citation in group:
            excerpt = " ".join(citation.excerpt.split())
            if len(excerpt) > 180:
                excerpt = f"{excerpt[:177]}…"
            lines.append(f"  - [S{citation.segment_id}] {citation.url} — {excerpt}")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    """Server-normalized response used before projecting public fields."""

    status: Literal["ok", "not_found", "failed"]
    disposition: ResponseDisposition
    sections: tuple[ResponseSection, ...] = ()
    citations: tuple[Citation, ...] = ()
    action_results: tuple[dict, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _STATUSES or self.disposition not in _DISPOSITIONS:
            raise ValueError("invalid response status or disposition")
        if any(not isinstance(section, (GroundedResponseSection, UnsupportedResponseSection, CanonicalResponseSection, ActionResponseSection, FailedResponseSection)) for section in self.sections):
            raise ValueError("response contains an unknown section type")
        _require_results(self.action_results, label="response")
        if self.error_code is not None and self.error_code not in ERROR_CODES and not (
            self.disposition == "action" and self.error_code in ACTION_CODES
        ):
            raise ValueError("unregistered response error code")
        if self.disposition != "grounded" and self.citations:
            raise ValueError("only grounded responses may carry citations")
        if self.disposition == "grounded":
            if self.status != "ok" or not self.citations:
                raise ValueError("grounded response must be successful and cited")
            if not self.sections or any(
                not isinstance(section, (GroundedResponseSection, UnsupportedResponseSection))
                for section in self.sections
            ):
                raise ValueError("grounded response requires grounded or unsupported sections")
            if not any(
                isinstance(section, GroundedResponseSection) and section.citation_ids
                for section in self.sections
            ):
                raise ValueError("grounded response requires at least one cited section")
            grounded_ids = tuple(
                segment_id
                for section in self.sections
                if isinstance(section, GroundedResponseSection)
                for segment_id in section.citation_ids
            )
            citation_ids = tuple(citation.segment_id for citation in self.citations)
            if grounded_ids != citation_ids:
                raise ValueError("grounded citations must equal section citation union")
            if len(set(grounded_ids)) != len(grounded_ids):
                raise ValueError("grounded citations must be unique")
            if len(grounded_ids) > 8:
                raise ValueError("grounded response has too many segments")
            if len({citation.item_id for citation in self.citations}) > 5:
                raise ValueError("grounded response has too many items")
        elif self.disposition == "no_evidence":
            if self.status != "not_found" or self.error_code != "no_evidence":
                raise ValueError("no-evidence response must be not_found/no_evidence")
            if len(self.sections) != 1 or not isinstance(self.sections[0], CanonicalResponseSection):
                raise ValueError("no-evidence response requires one canonical section")
            if self.sections[0].template_key != "no_evidence" or self.sections[0].text != NO_EVIDENCE_TEXT:
                raise ValueError("no-evidence response must use the registered fixed text")
        elif self.disposition == "canonical":
            if len(self.sections) != 1 or not isinstance(self.sections[0], CanonicalResponseSection):
                raise ValueError("canonical response requires one canonical section")
            if self.status != "ok" and self.error_code is None:
                raise ValueError("non-ok canonical response requires an error code")
        elif self.disposition == "action":
            if len(self.sections) != 1 or not isinstance(self.sections[0], ActionResponseSection):
                raise ValueError("action response requires one action section")
            if tuple(self.sections[0].results) != tuple(self.action_results):
                raise ValueError("action results must equal action section results")
            if self.status != "ok" and self.error_code is None:
                raise ValueError("non-ok action response requires an error code")
        elif self.disposition == "failed":
            if self.status != "failed" or len(self.sections) != 1 or not isinstance(self.sections[0], FailedResponseSection):
                raise ValueError("failed response requires one failed section")
            if self.error_code != self.sections[0].error_code or self.action_results:
                raise ValueError("failed response fields are inconsistent")

    @classmethod
    def grounded(
        cls,
        *,
        sections: list[GroundedResponseSection | UnsupportedResponseSection]
        | tuple[GroundedResponseSection | UnsupportedResponseSection, ...],
        citations: list[Citation] | tuple[Citation, ...],
        action_results: list[dict] | tuple[dict, ...] = (),
    ) -> "ResponseEnvelope":
        return cls(status="ok", disposition="grounded", sections=tuple(sections), citations=tuple(citations), action_results=tuple(action_results))

    @classmethod
    def no_evidence(cls, *, action_results: list[dict] | tuple[dict, ...] = ()) -> "ResponseEnvelope":
        return cls(
            status="not_found",
            disposition="no_evidence",
            sections=(CanonicalResponseSection("canonical", "no_evidence", NO_EVIDENCE_TEXT),),
            action_results=tuple(action_results),
            error_code="no_evidence",
        )

    @classmethod
    def canonical(
        cls,
        *,
        text: str,
        template_key: str,
        status: Literal["ok", "not_found", "failed"] = "ok",
        action_results: list[dict] | tuple[dict, ...] = (),
        error_code: str | None = None,
    ) -> "ResponseEnvelope":
        return cls(status=status, disposition="canonical", sections=(CanonicalResponseSection("canonical", template_key, text),), action_results=tuple(action_results), error_code=error_code)

    @classmethod
    def action(
        cls,
        *,
        status: Literal["ok", "not_found", "failed"],
        text: str,
        action_code: str,
        results: list[dict] | tuple[dict, ...] = (),
        error_code: str | None = None,
    ) -> "ResponseEnvelope":
        values = tuple(results)
        return cls(status=status, disposition="action", sections=(ActionResponseSection("action", action_code, text, values),), action_results=values, error_code=error_code)

    @classmethod
    def failed(cls, *, text: str, error_code: str) -> "ResponseEnvelope":
        return cls(status="failed", disposition="failed", sections=(FailedResponseSection("failed", error_code, text),), error_code=error_code)

    def project(self, *, thread_id: str | None = None) -> AgentAnswer:
        """Project only the normalized sections to the stable public contract."""

        if self.disposition == "grounded":
            text = _render_grounded_text(
                tuple(
                    section
                    for section in self.sections
                    if isinstance(section, (GroundedResponseSection, UnsupportedResponseSection))
                ),
                self.citations,
            )
        else:
            text = "\n\n".join(section.text for section in self.sections if hasattr(section, "text"))
        return AgentAnswer(status=self.status, text=text, citations=list(self.citations), action_results=list(self.action_results), thread_id=thread_id, error_code=self.error_code)


__all__ = [
    "ACTION_CODES", "CANONICAL_TEMPLATE_KEYS", "ActionResponseSection",
    "CanonicalResponseSection", "ERROR_CODES", "FailedResponseSection",
    "GroundedResponseSection", "NO_EVIDENCE_TEXT", "UNSUPPORTED_EVIDENCE_TEXT", "ResponseDisposition",
    "ResponseEnvelope", "ResponseSection",
    "UnsupportedResponseSection",
]
