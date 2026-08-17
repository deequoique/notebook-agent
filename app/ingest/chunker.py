"""Deterministic timestamp chunking with semantic and overlapping fallbacks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from app.connectors.base import Cue


@dataclass(frozen=True)
class Chunk:
    start_sec: float
    end_sec: float
    text: str
    boundary_kind: str


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return dot / norm if norm else 0.0


def semantic_boundary_indices(embeddings: Sequence[Sequence[float]]) -> list[int]:
    """Return cue indices after strict local minima in adjacent similarity."""
    if len(embeddings) < 4:
        return []
    similarities = [_cosine(a, b) for a, b in zip(embeddings, embeddings[1:])]
    return [i + 1 for i in range(1, len(similarities) - 1) if similarities[i] < similarities[i - 1] and similarities[i] < similarities[i + 1]]


def _make(cues: Sequence[Cue], kind: str) -> Chunk:
    return Chunk(cues[0].start, cues[-1].end, " ".join(c.text.strip() for c in cues if c.text.strip()), kind)


def _units(cues: Sequence[Cue], lang: str) -> int:
    text = " ".join(cue.text for cue in cues)
    if lang.lower().startswith("zh"):
        return len(re.findall(r"[\u3400-\u9fff]", text))
    return len(re.findall(r"\b[\w']+\b", text))


def _split_at(cues: Sequence[Cue], boundaries: dict[int, str]) -> list[Chunk]:
    result: list[Chunk] = []
    begin = 0
    for index in sorted(boundaries):
        if begin < index:
            result.append(_make(cues[begin:index], boundaries[index]))
            begin = index
    if begin < len(cues):
        result.append(_make(cues[begin:], result[-1].boundary_kind if result else "hard_cut"))
    return result


def _hard_cut(
    cues: Sequence[Cue], *, lang: str, overlap: float = 0.15
) -> list[Chunk]:
    """Cut near the language-specific 60s density target, never past 120s."""
    result: list[Chunk] = []
    start = 0
    target_units = 280 if lang.lower().startswith("zh") else 170
    while start < len(cues):
        end = start + 1
        while end < len(cues):
            candidate = cues[start : end + 1]
            duration = candidate[-1].end - candidate[0].start
            if duration > 120:
                break
            end += 1
            if duration >= 60 or _units(candidate, lang) >= target_units:
                break
        result.append(_make(cues[start:end], "hard_cut"))
        if end >= len(cues):
            break
        chunk_duration = cues[end - 1].end - cues[start].start
        overlap_start = cues[end - 1].end - chunk_duration * overlap
        next_start = end - 1
        while next_start > start + 1 and cues[next_start - 1].end > overlap_start:
            next_start -= 1
        # A gap larger than the 120s ceiling can make ``end == start + 1``.
        # In that case ``end - 1`` points back to the same cue, so overlap
        # must yield to the stronger invariant that every iteration advances.
        start = max(start + 1, next_start)
    return result


def _valid_signal_chunks(cues: Sequence[Cue], boundaries: dict[int, str]) -> list[Chunk] | None:
    if not boundaries:
        return None
    pieces = _split_at(cues, boundaries)
    return pieces if all(piece.end_sec - piece.start_sec <= 120 for piece in pieces) else None


def chunk(
    cues: list[Cue], *, lang: str, chapters: list[dict] | None = None,
    semantic_embedder: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[Chunk]:
    if not cues:
        return []
    if chapters:
        result: list[Chunk] = []
        for chapter in chapters:
            selected = [c for c in cues if c.start >= float(chapter["start_time"]) and c.start < float(chapter.get("end_time", cues[-1].end))]
            if not selected:
                continue
            if selected[-1].end - selected[0].start <= 180:
                result.append(_make(selected, "chapter"))
            else:
                result.extend(chunk(selected, lang=lang, semantic_embedder=semantic_embedder))
        if result:
            return result

    duration = cues[-1].end - cues[0].start
    required = duration / 180
    boundaries: dict[int, str] = {}
    for i in range(1, len(cues)):
        if cues[i].start - cues[i - 1].end >= 2.0:
            boundaries[i] = "gap"
    if len(boundaries) < required:
        for i, cue in enumerate(cues[:-1], 1):
            if re.search(r"[.?!。？！]\s*$", cue.text):
                boundaries.setdefault(i, "punct")
    if len(boundaries) >= required:
        pieces = _valid_signal_chunks(cues, boundaries)
        if pieces is not None:
            return pieces

    if semantic_embedder is not None:
        embeddings = semantic_embedder([cue.text for cue in cues])
        for index in semantic_boundary_indices(embeddings):
            boundaries.setdefault(index, "semantic")
        pieces = _valid_signal_chunks(cues, boundaries)
        if pieces is not None:
            return pieces
    return _hard_cut(cues, lang=lang)
