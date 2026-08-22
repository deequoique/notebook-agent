from app.connectors.base import Cue
from app.ingest.chunker import chunk, semantic_boundary_indices


def _cues(texts, *, step=10, gaps=None):
    gaps = gaps or {}
    result = []
    cursor = 0.0
    for index, text in enumerate(texts):
        cursor += gaps.get(index, 0)
        result.append(Cue(cursor, cursor + step, text))
        cursor += step
    return result


def test_chapters_are_first_priority():
    cues = _cues(["a", "b", "c", "d"])
    chapters = [{"start_time": 0, "end_time": 20}, {"start_time": 20, "end_time": 40}]
    parts = chunk(cues, lang="en", chapters=chapters)
    assert [part.boundary_kind for part in parts] == ["chapter", "chapter"]


def test_gap_is_used_before_punctuation():
    cues = _cues(["first.", "second", "third"], gaps={1: 3})
    parts = chunk(cues, lang="en")
    assert len(parts) == 2
    assert all(part.boundary_kind == "gap" for part in parts)


def test_punctuation_fallback():
    parts = chunk(_cues(["one.", "two", "three"]), lang="en")
    assert len(parts) == 2
    assert all(part.boundary_kind == "punct" for part in parts)


def test_unpunctuated_track_does_not_claim_punctuation():
    parts = chunk(_cues(["word"] * 20), lang="en")
    assert {part.boundary_kind for part in parts} == {"hard_cut"}


def test_semantic_local_minimum_is_deterministic():
    embeddings = [[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9], [0, 1]]
    assert semantic_boundary_indices(embeddings) == [2]
    cues = _cues(["甲", "乙", "主题转换", "丙", "丁"], step=40)
    parts = chunk(cues, lang="zh", semantic_embedder=lambda _: embeddings)
    assert any(part.boundary_kind == "semantic" for part in parts)


def test_hard_cut_has_overlap():
    cues = _cues([str(i) for i in range(30)], step=10)
    parts = chunk(cues, lang="zh")
    assert len(parts) >= 3
    assert parts[1].start_sec < parts[0].end_sec
    overlap = parts[0].end_sec - parts[1].start_sec
    assert overlap / (parts[0].end_sec - parts[0].start_sec) >= 0.15


def test_hard_cut_advances_across_a_gap_larger_than_the_duration_ceiling():
    cues = [
        Cue(0, 1, "first"),
        Cue(500, 501, "second"),
        Cue(502, 503, "third"),
    ]

    parts = chunk(cues, lang="en")

    assert [part.text for part in parts] == ["first", "second third"]
    assert all(part.end_sec >= part.start_sec for part in parts)


def test_language_density_targets_drive_hard_cut():
    english = chunk(_cues(["word " * 50] * 8, step=5), lang="en")
    chinese = chunk(_cues(["字" * 100] * 8, step=5), lang="zh")
    assert english[0].end_sec < 60
    assert chinese[0].end_sec < 60
