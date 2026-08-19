"""Gold-evidence contracts and deterministic quality scoring.

The live evaluator deliberately keeps prompts, answers, and tool payloads out of
its sanitized report.  This module therefore accepts a small, explicit
projection of retrieval results and final citations rather than reaching into
the Agent runtime or database.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class GoldEvidenceError(ValueError):
    """Raised when a gold dataset or quality projection is not usable."""


EvidenceKind = Literal[
    "direct_keyword",
    "paraphrase",
    "cross_language",
    "multi_segment",
    "context",
    "no_evidence",
]
FailureClassification = Literal[
    "retrieval_miss", "evidence_selection_miss", "answer_contract_failure"
]

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_PYDANTIC_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_PYDANTIC_SCHEMA_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixtureRef(_StrictModel):
    """Stable fixture identity, never a run-local database identity."""

    fixture_alias: str | None = Field(default=None, min_length=1, max_length=128)
    platform: str | None = Field(default=None, min_length=1, max_length=32)
    platform_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def has_stable_reference(self) -> "FixtureRef":
        if self.fixture_alias is None and (self.platform is None or self.platform_id is None):
            raise ValueError("fixture_ref requires fixture_alias or platform plus platform_id")
        if (self.platform is None) != (self.platform_id is None):
            raise ValueError("platform and platform_id must be provided together")
        return self


class AnswerBoundary(_StrictModel):
    must_include: list[str] = Field(default_factory=list, max_length=20)
    acceptable_paraphrases: list[str] = Field(default_factory=list, max_length=20)
    must_not_claim: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_phrases(self) -> "AnswerBoundary":
        values = [*self.must_include, *self.acceptable_paraphrases, *self.must_not_claim]
        if any(not value.strip() for value in values):
            raise ValueError("answer boundary phrases must not be blank")
        return self


class GoldSample(_StrictModel):
    """One manually annotated question and its trusted evidence contract."""

    sample_id: str = Field(pattern=_PYDANTIC_ID_PATTERN)
    case_id: str = Field(min_length=1, max_length=128)
    turn_index: int = Field(gt=0, le=100)
    kind: EvidenceKind
    query: str = Field(min_length=1, max_length=4000)
    fixture_ref: FixtureRef | None = None
    # Draft authoring format keeps the short alias at the sample top level.
    # It is normalized into fixture_ref before strict validation.
    fixture_alias: str | None = Field(default=None, min_length=1, max_length=128)
    gold_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    gold_segment_ids: list[str] = Field(default_factory=list, max_length=50)
    gold_timestamp_range: tuple[float, float] | None = None
    timestamp_tolerance_sec: float | None = Field(default=None, ge=0, le=300)
    evidence_groups: list[list[str]] = Field(default_factory=list, max_length=20)
    reference_points: list[str] = Field(default_factory=list, max_length=10)
    reference_answer: str = Field(min_length=1, max_length=4000)
    answer_boundary: AnswerBoundary = Field(default_factory=AnswerBoundary)
    no_evidence: bool = False
    annotation_note: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def normalize_draft_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        alias = normalized.get("fixture_alias")
        if "fixture_ref" not in normalized and alias:
            normalized["fixture_ref"] = {"fixture_alias": alias}
        # Keep extra=forbid useful after the compatibility normalization.
        normalized.pop("fixture_alias", None)
        if normalized.get("kind") == "no_evidence" and "no_evidence" not in normalized:
            normalized["no_evidence"] = True
        return normalized

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> "GoldSample":
        if any(not value.strip() for value in self.reference_points):
            raise ValueError("reference_points must not contain blank values")
        segment_ids = set(self.gold_segment_ids)
        if len(segment_ids) != len(self.gold_segment_ids):
            raise ValueError("gold_segment_ids must not contain duplicates")
        if any(not _ID_RE.fullmatch(value) for value in self.gold_segment_ids):
            raise ValueError("gold_segment_ids contain an invalid stable key")

        if self.gold_timestamp_range is not None:
            start, end = self.gold_timestamp_range
            if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end < start:
                raise ValueError("gold_timestamp_range must be finite, non-negative, and ordered")

        group_values = [segment for group in self.evidence_groups for segment in group]
        if any(not group for group in self.evidence_groups):
            raise ValueError("evidence_groups must not contain empty groups")
        if any(segment not in segment_ids for segment in group_values):
            raise ValueError("evidence_groups may only reference gold_segment_ids")

        if self.no_evidence or self.kind == "no_evidence":
            if not self.no_evidence or self.kind != "no_evidence":
                raise ValueError("no-evidence samples must set kind=no_evidence and no_evidence=true")
            if self.gold_item_id is not None or self.gold_segment_ids or self.gold_timestamp_range:
                raise ValueError("no-evidence samples cannot declare gold item, segments, or timestamps")
            if self.evidence_groups:
                raise ValueError("no-evidence samples cannot declare evidence groups")
            return self

        return self

    @property
    def is_gold_complete(self) -> bool:
        """Whether this draft has enough evidence metadata for scoring."""

        if self.kind == "no_evidence":
            return (
                self.no_evidence
                and bool(self.answer_boundary.must_not_claim)
                and bool(self.reference_points)
            )
        return bool(
            self.fixture_ref
            and self.gold_item_id
            and self.gold_segment_ids
            and self.gold_timestamp_range is not None
            and self.timestamp_tolerance_sec is not None
            and self.evidence_groups
            and self.reference_points
        )


class HumanEvalDataset(_StrictModel):
    schema_version: str = Field(pattern=_PYDANTIC_SCHEMA_PATTERN)
    dataset_id: str = Field(min_length=1, max_length=128, pattern=_PYDANTIC_ID_PATTERN)
    language: str = Field(min_length=2, max_length=32)
    samples: list[GoldSample] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_samples(self) -> "HumanEvalDataset":
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("sample_id values must be unique")
        return self


REQUIRED_KINDS = frozenset(
    {"direct_keyword", "paraphrase", "cross_language", "multi_segment", "context", "no_evidence"}
)


def validate_human_eval_dataset(
    dataset: HumanEvalDataset, *, require_complete: bool = False
) -> HumanEvalDataset:
    """Validate authoring completeness separately from YAML shape validity.

    The checked-in template is intentionally empty, so shape validation and
    the 20–30 sample release gate are separate operations.
    """

    if not require_complete:
        return dataset
    count = len(dataset.samples)
    if not 20 <= count <= 30:
        raise GoldEvidenceError("human evaluation dataset must contain 20 to 30 samples")
    kinds = {sample.kind for sample in dataset.samples}
    missing = REQUIRED_KINDS - kinds
    if missing:
        raise GoldEvidenceError(f"human evaluation dataset is missing kinds: {sorted(missing)}")
    if sum(sample.kind == "multi_segment" for sample in dataset.samples) < 4:
        raise GoldEvidenceError("human evaluation dataset requires at least four multi-segment samples")
    if sum(sample.kind == "no_evidence" for sample in dataset.samples) < 3:
        raise GoldEvidenceError("human evaluation dataset requires at least three no-evidence samples")
    incomplete = [sample.sample_id for sample in dataset.samples if not sample.is_gold_complete]
    if incomplete:
        raise GoldEvidenceError(
            "human evaluation samples are missing gold evidence: " + ", ".join(incomplete)
        )
    return dataset


@dataclass(frozen=True)
class HumanEvalCompletion:
    sample_count: int
    complete_count: int
    draft_count: int
    missing_kinds: tuple[str, ...]
    incomplete_sample_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "complete_count": self.complete_count,
            "draft_count": self.draft_count,
            "missing_kinds": list(self.missing_kinds),
            "incomplete_sample_ids": list(self.incomplete_sample_ids),
        }


def human_eval_completion(dataset: HumanEvalDataset) -> HumanEvalCompletion:
    complete = [sample for sample in dataset.samples if sample.is_gold_complete]
    missing_kinds = tuple(sorted(REQUIRED_KINDS - {sample.kind for sample in dataset.samples}))
    return HumanEvalCompletion(
        sample_count=len(dataset.samples),
        complete_count=len(complete),
        draft_count=len(dataset.samples) - len(complete),
        missing_kinds=missing_kinds,
        incomplete_sample_ids=tuple(sample.sample_id for sample in dataset.samples if not sample.is_gold_complete),
    )


def load_human_eval_dataset(
    path: str | Path, *, require_complete: bool = False
) -> HumanEvalDataset:
    """Load and validate a human gold YAML file."""

    target = Path(path)
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        dataset = HumanEvalDataset.model_validate(raw)
        return validate_human_eval_dataset(dataset, require_complete=require_complete)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        if isinstance(exc, GoldEvidenceError):
            raise
        raise GoldEvidenceError(f"human evaluation dataset validation failed: {exc}") from exc


class EvidenceHit(_StrictModel):
    """Privacy-safe retrieval/citation projection consumed by the scorer."""

    item_id: str | int
    segment_id: str | int
    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "EvidenceHit":
        values = (self.start_sec, self.end_sec)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("evidence timestamps must be finite")
        if self.start_sec is not None and self.end_sec is not None and self.end_sec < self.start_sec:
            raise ValueError("evidence timestamp interval must be ordered")
        return self


@dataclass(frozen=True)
class SampleQualityScore:
    sample_id: str
    no_evidence: bool
    recall_at_1: float | None
    recall_at_3: float | None
    mrr: float | None
    citation_precision: float | None
    citation_completeness: float | None
    timestamp_hit_rate: float | None
    retrieval_false_positive: float | None
    citation_false_positive: float | None
    failure_classifications: tuple[FailureClassification, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_evidence_failures(
    sample: GoldSample,
    retrieved: Sequence[EvidenceHit | Mapping[str, Any]],
    citations: Sequence[EvidenceHit | Mapping[str, Any]],
    *,
    diagnostic_events: Sequence[Mapping[str, Any]] = (),
) -> tuple[FailureClassification, ...]:
    """Classify retrieval, selection, and answer-contract failures safely.

    Only stable gold identities and allow-listed diagnostic labels are used;
    no out-of-scope IDs, prompts, excerpts, or provider payloads enter the
    result. A sample may have both ``retrieval_miss`` and
    ``answer_contract_failure`` (the HE-003 shape), preserving the two failure
    layers instead of collapsing them into one verdict.
    """

    retrieved_hits = _coerce_hits(retrieved)
    citation_hits = _coerce_hits(citations)
    if sample.no_evidence:
        return ("evidence_selection_miss",) if citation_hits else ()

    gold_item = _identity(sample.gold_item_id or "")
    gold_segments = {_identity(value) for value in sample.gold_segment_ids}

    def is_gold_hit(hit: EvidenceHit) -> bool:
        return _identity(hit.item_id) == gold_item and _identity(hit.segment_id) in gold_segments

    classifications: list[FailureClassification] = []
    if not any(is_gold_hit(hit) for hit in retrieved_hits):
        classifications.append("retrieval_miss")
    elif not any(is_gold_hit(hit) for hit in citation_hits):
        classifications.append("evidence_selection_miss")

    answer_failure = any(
        event.get("agent_phase") == "answer"
        and (
            event.get("failure_reason") is not None
            or event.get("error_code") == "answer_unavailable"
        )
        for event in diagnostic_events
    )
    if answer_failure:
        classifications.append("answer_contract_failure")
    return tuple(dict.fromkeys(classifications))


@dataclass(frozen=True)
class QualityAggregate:
    sample_count: int
    scored_count: int
    no_evidence_count: int
    recall_at_1: float | None
    recall_at_3: float | None
    mrr: float | None
    citation_precision: float | None
    citation_completeness: float | None
    timestamp_hit_rate: float | None
    retrieval_false_positive_rate: float | None
    citation_false_positive_rate: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_hits(values: Sequence[EvidenceHit | Mapping[str, Any]]) -> list[EvidenceHit]:
    try:
        return [value if isinstance(value, EvidenceHit) else EvidenceHit.model_validate(value) for value in values]
    except ValidationError as exc:
        raise GoldEvidenceError(f"quality projection validation failed: {exc}") from exc


def _identity(value: str | int) -> str:
    return str(value)


def _timestamp_hit(hit: EvidenceHit, sample: GoldSample) -> bool:
    if sample.gold_timestamp_range is None or hit.start_sec is None:
        return False
    hit_start = hit.start_sec
    hit_end = hit.end_sec if hit.end_sec is not None else hit.start_sec
    gold_start, gold_end = sample.gold_timestamp_range
    tolerance = sample.timestamp_tolerance_sec
    return hit_end >= gold_start - tolerance and hit_start <= gold_end + tolerance


def score_gold_sample(
    sample: GoldSample,
    retrieved: Sequence[EvidenceHit | Mapping[str, Any]],
    citations: Sequence[EvidenceHit | Mapping[str, Any]],
) -> SampleQualityScore:
    """Score one sample from ranked retrieval and final citation projections."""

    if not sample.is_gold_complete:
        raise GoldEvidenceError(
            f"sample {sample.sample_id} is a draft and cannot be scored before gold evidence is complete"
        )
    retrieved_hits = _coerce_hits(retrieved)
    citation_hits = _coerce_hits(citations)
    if sample.no_evidence:
        return SampleQualityScore(
            sample_id=sample.sample_id,
            no_evidence=True,
            recall_at_1=None,
            recall_at_3=None,
            mrr=None,
            citation_precision=None,
            citation_completeness=None,
            timestamp_hit_rate=None,
            retrieval_false_positive=float(bool(retrieved_hits)),
            citation_false_positive=float(bool(citation_hits)),
        )

    gold_item = _identity(sample.gold_item_id or "")
    gold_segments = {_identity(value) for value in sample.gold_segment_ids}

    def is_gold_hit(hit: EvidenceHit) -> bool:
        return _identity(hit.item_id) == gold_item and _identity(hit.segment_id) in gold_segments

    retrieved_ids = [is_gold_hit(hit) for hit in retrieved_hits]
    first_rank = next(
        (index + 1 for index, is_relevant in enumerate(retrieved_ids) if is_relevant),
        None,
    )
    relevant_citations = [hit for hit in citation_hits if is_gold_hit(hit)]
    relevant_cited_ids = {_identity(hit.segment_id) for hit in relevant_citations}
    covered_groups = sum(
        1
        for group in sample.evidence_groups
        if any(segment_id in relevant_cited_ids for segment_id in group)
    )
    return SampleQualityScore(
        sample_id=sample.sample_id,
        no_evidence=False,
        recall_at_1=float(any(retrieved_ids[:1])),
        recall_at_3=float(any(retrieved_ids[:3])),
        mrr=(1.0 / first_rank) if first_rank is not None else 0.0,
        citation_precision=(len(relevant_citations) / len(citation_hits)) if citation_hits else 0.0,
        citation_completeness=covered_groups / len(sample.evidence_groups),
        timestamp_hit_rate=float(
            any(is_gold_hit(hit) and _timestamp_hit(hit, sample) for hit in citation_hits)
        ),
        retrieval_false_positive=None,
        citation_false_positive=None,
    )


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def aggregate_quality_scores(scores: Iterable[SampleQualityScore]) -> QualityAggregate:
    """Return macro metrics plus separate no-evidence false-positive rates."""

    rows = list(scores)
    positive = [row for row in rows if not row.no_evidence]
    negatives = [row for row in rows if row.no_evidence]
    return QualityAggregate(
        sample_count=len(rows),
        scored_count=len(rows),
        no_evidence_count=len(negatives),
        recall_at_1=_mean(row.recall_at_1 for row in positive),
        recall_at_3=_mean(row.recall_at_3 for row in positive),
        mrr=_mean(row.mrr for row in positive),
        citation_precision=_mean(row.citation_precision for row in positive),
        citation_completeness=_mean(row.citation_completeness for row in positive),
        timestamp_hit_rate=_mean(row.timestamp_hit_rate for row in positive),
        retrieval_false_positive_rate=_mean(row.retrieval_false_positive for row in negatives),
        citation_false_positive_rate=_mean(row.citation_false_positive for row in negatives),
    )
