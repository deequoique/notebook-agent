"""Framework-neutral request and response contracts for the knowledge Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from typing_extensions import TypedDict

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

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
    """One model-authored section backed by current-run Citation IDs."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    citation_ids: list[int] = Field(min_length=1, max_length=8)


class GroundedDraft(BaseModel):
    """A grounded answer decision returned by the private answer Agent."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded"]
    sections: list[GroundedSection] = Field(min_length=1, max_length=8)


class NoRelevantEvidenceDraft(BaseModel):
    """An explicit answer-agent decision that no candidate supports the query."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["no_relevant_evidence"]


AnswerDecision = Annotated[
    GroundedDraft | NoRelevantEvidenceDraft,
    Field(discriminator="kind"),
]

# PydanticAI's prompted-output adapter requires an object schema and cannot
# consume a RootModel whose schema is a top-level ``oneOf``.  Keep the
# discriminated AnswerDecision as the canonical Python union, while this
# object wrapper validates the same invariant on the provider wire.
class AnswerDraft(BaseModel):
    """Private structured composer output; never persisted verbatim."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded", "no_relevant_evidence"]
    sections: list[GroundedSection] | None = None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        """Advertise the same no-sections invariant enforced at runtime."""

        schema = handler(core_schema)
        section_schema = dict(schema.get("$defs", {}).get("GroundedSection", {}))
        schema["oneOf"] = [
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "grounded"},
                    "sections": {
                        "type": "array",
                        "items": section_schema,
                        "minItems": 1,
                        "maxItems": 8,
                    },
                },
                "required": ["kind", "sections"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"kind": {"const": "no_relevant_evidence"}},
                "required": ["kind"],
                "not": {"required": ["sections"]},
                "additionalProperties": False,
            },
        ]
        return schema

    @model_validator(mode="after")
    def validate_disposition(self) -> "AnswerDraft":
        if self.kind == "grounded":
            if not self.sections:
                raise ValueError("grounded answer requires sections")
            if len(self.sections) > 8:
                raise ValueError("grounded answer has too many sections")
        elif self.sections is not None:
            raise ValueError("no-evidence answer cannot contain sections")
        return self

    @property
    def decision(self) -> AnswerDecision:
        if self.kind == "no_relevant_evidence":
            return NoRelevantEvidenceDraft(kind=self.kind)
        # The validator above guarantees the non-empty section invariant.
        return GroundedDraft(kind="grounded", sections=self.sections or [])
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
