"""Framework-neutral request and response contracts for the knowledge Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from typing_extensions import TypedDict

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.agent.answer_validation import NaturalAnswerValidationError, validate_natural_answer
from app.agent.context import TurnContext
from app.channels.types import TenantContext


class Citation(BaseModel):
    """A source that was actually returned by a tenant-scoped tool."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    segment_id: int
    title: str
    excerpt: str
    url: str
    start_sec: float | None = None
    _retrieval_score: float | None = PrivateAttr(default=None)

    def __eq__(self, other: object) -> bool:
        """Keep private retrieval diagnostics out of the public source contract."""

        if not isinstance(other, Citation):
            return NotImplemented
        return self.model_dump() == other.model_dump()


class AgentAnswer(BaseModel):
    """Stable answer contract shared by CLI and channel adapters."""

    status: Literal["ok", "not_found", "failed"]
    text: str
    citations: list[Citation] = Field(default_factory=list)
    action_results: list[dict] = Field(default_factory=list)
    thread_id: str | None = None
    error_code: str | None = None


class RetrievalToolPayload(TypedDict):
    """The only retrieval-tool result shape exposed to the planning Agent.

    ``skipped`` is deliberately distinct from an empty successful search: a
    provider may emit a batch despite ``parallel_tool_calls=False``, but only
    the first retrieval in that model step is allowed to reach backend
    services.
    """

    status: Literal["ok", "skipped"]
    evidence: list[dict]
    reason: Literal["same_model_step", "budget_exhausted"] | None


class GroundedSection(BaseModel):
    """One explicit grounded or unsupported Composer section.

    Unsupported sections intentionally carry no model-authored text. The
    response boundary renders a fixed server-owned notice for that status.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["grounded", "unsupported"] = "grounded"
    text: str | None = Field(default=None, min_length=1)
    citation_ids: list[int] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "GroundedSection":
        if self.status == "grounded":
            if self.text is None or not self.text.strip():
                raise ValueError("grounded section requires text")
            if not self.citation_ids:
                raise ValueError("grounded section requires citations")
            return self
        if self.text is not None:
            raise ValueError("unsupported section must not contain model text")
        if self.citation_ids:
            raise ValueError("unsupported section must not contain citations")
        return self


class GroundedDraft(BaseModel):
    """A grounded answer decision returned by the private answer Agent."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded"]
    sections: list[GroundedSection] = Field(min_length=1, max_length=8)


class AnswerDraft(BaseModel):
    """Grounded Composer draft; never persisted verbatim."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded"]
    sections: list[GroundedSection] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_disposition(self) -> "AnswerDraft":
        if not self.sections:
            raise ValueError("grounded answer requires sections")
        if len(self.sections) > 8:
            raise ValueError("grounded answer has too many sections")
        return self

    @property
    def decision(self) -> GroundedDraft:
        return GroundedDraft(kind="grounded", sections=self.sections or [])


class PlannedSection(BaseModel):
    """Server-validated section plan used before provider text streaming.

    ``task`` is a short topic/answer assignment, not model-authored answer
    text.  It gives each section stream enough context to distinguish adjacent
    questions in a mixed request while staying bounded and source-safe.
    """

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=240)
    status: Literal["grounded", "unsupported"]
    citation_ids: list[int] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_citations(self) -> "PlannedSection":
        try:
            task = validate_natural_answer(self.task).text
        except NaturalAnswerValidationError as exc:
            raise ValueError("planned section task must be short safe topic text") from exc
        self.task = task
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.citation_ids
        ):
            raise ValueError("planned citation ids must be positive integers")
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError("planned citation ids must be unique")
        if self.status == "grounded" and not self.citation_ids:
            raise ValueError("grounded planned section requires citations")
        if self.status == "unsupported" and self.citation_ids:
            raise ValueError("unsupported planned section must not contain citations")
        return self


class AnswerStreamPlan(BaseModel):
    """Citation-only plan; it deliberately contains no model-authored text."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded"]
    sections: list[PlannedSection] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_plan(self) -> "AnswerStreamPlan":
        section_ids = [section.section_id for section in self.sections]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("planned section ids must be unique")
        citation_ids = [
            citation_id
            for section in self.sections
            if section.status == "grounded"
            for citation_id in section.citation_ids
        ]
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("planned citation ids must be unique globally")
        if not citation_ids:
            raise ValueError("stream plan requires at least one grounded citation")
        return self
# Historical import name retained for integrations; the section itself is
# unchanged and the top-level duplicate selection field is gone.
AnswerSection = GroundedSection


@dataclass(frozen=True)
class AgentRequest:
    """A trusted request assembled by application code, never by the model."""

    question: str
    tenant: TenantContext
    thread_db_id: int
    thread_public_id: str
    message_id: str
    request_id: str
    history: tuple[dict, ...] = ()
    # Server-owned correlation for high-risk confirmations.  This is the
    # message id of the newest completed turn before the current message;
    # model/tool arguments never carry it.
    latest_turn_message_id: str | None = None
    # A bounded, immutable projection of trusted prior-turn focus.  Appended
    # with a default to preserve positional compatibility for integrations
    # that construct AgentRequest directly.
    context: TurnContext = field(default_factory=TurnContext)

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("question must not be empty")


# Management contracts live in their focused module to keep retrieval types
# small.  Re-exporting them here preserves the package-level contract for
# integrations that historically imported all Agent payloads from ``types``.
from app.agent.management import (  # noqa: E402  (intentional compatibility export)
    BatchItemOperationResult,
    ItemFilters,
    ItemOperationResult,
    KnowledgeItemManagementService,
    SavedItem,
    SavedItemPage,
    decode_cursor,
    encode_cursor,
)
