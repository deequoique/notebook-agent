"""Private Agent-to-channel streaming events.

The HTTP adapter projects these events into the public SSE contract.  Provider
messages, prompts, tool payloads and model metadata never cross this module's
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai.messages import ModelMessage

from app.agent.types import AgentAnswer, Citation


AgentStreamEventType = Literal[
    "activity",
    "section_started",
    "text_delta",
    "section_completed",
    "section_aborted",
    "completed",
]


@dataclass(frozen=True)
class AgentStreamEvent:
    """A bounded internal event emitted by one Agent execution."""

    type: AgentStreamEventType
    request_id: str
    message_id: str
    activity: Literal["retrieving", "planning_answer", "composing"] | None = None
    section_id: str | None = None
    status: Literal["grounded", "unsupported"] | None = None
    citation_ids: tuple[int, ...] = ()
    citations: tuple[Citation, ...] = ()
    text: str | None = None
    reason: Literal["provider_failure", "timeout", "cancelled"] | None = None
    answer: AgentAnswer | None = None
    new_messages: tuple[ModelMessage, ...] = field(default_factory=tuple)
    persist: bool = True


__all__ = ["AgentStreamEvent", "AgentStreamEventType"]
