"""Validation for natural-text answers produced by the bounded turn Agent.

The turn Agent may write ordinary prose, but citations remain a server-owned
boundary.  This module parses only current-run ``[S<positive integer>]``
markers and never renders URLs or source blocks from model text.  Callers pass
the current-run Citation allow-list and may provide an exact-reference scope
predicate for each selected citation.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class NaturalAnswerValidationError(ValueError):
    """Raised when model prose violates the trusted answer contract."""


_VALID_MARKER = re.compile(r"\[S([1-9][0-9]*)\]")
# Capture citation-looking bracket forms, including malformed casing/spaces,
# so ``[Sfoo]`` and ``[S0]`` fail closed rather than silently becoming prose.
_CITATION_LIKE_MARKER = re.compile(r"\[\s*[Ss][^\]]*\]")
_URL_PATTERN = re.compile(r"(?:https?://|ftp://|www\.)", re.IGNORECASE)
_SOURCE_BLOCK_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:sources?|references?|来源|参考来源)\s*(?:[:：]|$)"
)


@dataclass(frozen=True, slots=True)
class ValidatedNaturalAnswer:
    """The model text plus only trusted citations selected from an allow-list."""

    text: str
    citation_ids: tuple[int, ...] = ()
    citations: tuple[Any, ...] = ()

    @property
    def grounded(self) -> bool:
        return bool(self.citation_ids)


def parse_inline_citation_ids(text: str) -> tuple[int, ...]:
    """Parse exact positive ``[S123]`` markers, preserving first-use order."""

    if not isinstance(text, str):
        raise NaturalAnswerValidationError("answer text must be text")
    malformed = []
    for match in _CITATION_LIKE_MARKER.finditer(text):
        if _VALID_MARKER.fullmatch(match.group(0)) is None:
            malformed.append(match.group(0))
    if malformed:
        raise NaturalAnswerValidationError("answer contains a malformed citation marker")

    ids: list[int] = []
    seen: set[int] = set()
    for match in _VALID_MARKER.finditer(text):
        segment_id = int(match.group(1))
        if segment_id not in seen:
            ids.append(segment_id)
            seen.add(segment_id)
    return tuple(ids)


def _reject_model_sources(text: str) -> None:
    if _URL_PATTERN.search(text) is not None:
        raise NaturalAnswerValidationError("answer must not contain model-authored URLs")
    if _SOURCE_BLOCK_PATTERN.search(text) is not None:
        raise NaturalAnswerValidationError("answer must not contain a source block")


def validate_natural_answer(
    text: str,
    *,
    citations: Mapping[int, Any] | None = None,
    knowledge_search_succeeded: bool = False,
    explicit_reference_scope: tuple[tuple[str, str], ...] = (),
    citation_matches_scope: Callable[[Any, tuple[tuple[str, str], ...]], bool] | None = None,
    max_source_items: int = 5,
    max_segments: int = 8,
    required_item_ids: frozenset[int] = frozenset(),
) -> ValidatedNaturalAnswer:
    """Validate one bounded Agent answer against trusted current-run facts.

    ``knowledge_search_succeeded=False`` is the no-tool/read-only path: clean
    natural prose is accepted but any marker, URL, or source block is rejected.
    A successful knowledge search requires at least one marker and validates
    every marker against the supplied current-run allow-list.  IDs from prior
    turns are absent from that map and therefore fail closed. ``max_segments``
    caps the distinct current-run markers selected by one answer. When
    ``required_item_ids`` is supplied, every evidence-bearing exact-scope item
    must appear in the selected markers.
    """

    if not isinstance(text, str):
        raise NaturalAnswerValidationError("answer text must be text")
    normalized = text.strip()
    if not normalized:
        raise NaturalAnswerValidationError("answer text must not be empty")
    _reject_model_sources(normalized)
    marker_ids = parse_inline_citation_ids(normalized)

    if not knowledge_search_succeeded:
        if marker_ids:
            raise NaturalAnswerValidationError(
                "no-tool answers must not contain Citation markers"
            )
        return ValidatedNaturalAnswer(normalized)

    if not marker_ids:
        raise NaturalAnswerValidationError(
            "a successful knowledge search requires a current-run Citation marker"
        )
    allow_list = citations or {}
    selected: list[Any] = []
    missing = [segment_id for segment_id in marker_ids if segment_id not in allow_list]
    if missing:
        raise NaturalAnswerValidationError("answer cites evidence outside the current run")
    for segment_id in marker_ids:
        citation = allow_list[segment_id]
        if explicit_reference_scope:
            if citation_matches_scope is None or not citation_matches_scope(
                citation, explicit_reference_scope
            ):
                raise NaturalAnswerValidationError("answer citation is outside the current scope")
        selected.append(citation)
    item_ids = {
        getattr(citation, "item_id", None) for citation in selected
    }
    if None in item_ids or len(item_ids) > max_source_items:
        raise NaturalAnswerValidationError("answer cites too many source items")
    if len(marker_ids) > max_segments:
        raise NaturalAnswerValidationError("answer cites too many source segments")
    if required_item_ids and not required_item_ids.issubset(item_ids):
        raise NaturalAnswerValidationError(
            "answer omits an evidence-bearing exact-scope item"
        )
    return ValidatedNaturalAnswer(
        normalized,
        citation_ids=marker_ids,
        citations=tuple(selected),
    )


# Short aliases for adapters and tests that use “inline” terminology.
AnswerValidationError = NaturalAnswerValidationError
parse_citation_markers = parse_inline_citation_ids
parse_markers = parse_inline_citation_ids
validate_inline_answer = validate_natural_answer
validate_natural_text = validate_natural_answer


__all__ = [
    "AnswerValidationError",
    "NaturalAnswerValidationError",
    "ValidatedNaturalAnswer",
    "parse_citation_markers",
    "parse_inline_citation_ids",
    "parse_markers",
    "validate_inline_answer",
    "validate_natural_answer",
    "validate_natural_text",
]
