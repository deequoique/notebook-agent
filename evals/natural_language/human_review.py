"""Validated local human-review records for answer-quality evaluation."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .quality import GoldEvidenceError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ScoreValue = Literal[0, 1]
Adjudication = Literal["pending", "accepted", "disputed"]
Verdict = Literal["pass", "fail"]


class HumanRubric(_StrictModel):
    answered_question: ScoreValue | None = None
    evidence_grounded: ScoreValue | None = None
    correct_video: ScoreValue | None = None
    timestamp_locatable: ScoreValue | None = None
    no_unsupported_claims: ScoreValue | None = None
    tone_and_guidance: ScoreValue | None = None


class HumanReviewScore(_StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    reviewer_id: str = Field(min_length=1, max_length=128)
    model_answer: str = Field(min_length=1, max_length=16000)
    cited_item_ids: list[str | int] = Field(default_factory=list, max_length=10)
    cited_segment_ids: list[str | int] = Field(default_factory=list, max_length=10)
    cited_timestamp_ranges: list[tuple[float, float]] = Field(default_factory=list, max_length=10)
    rubric: HumanRubric
    verdict: Verdict | None = None
    adjudication: Adjudication = "pending"
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "HumanReviewScore":
        if any(
            not (math.isfinite(start) and math.isfinite(end))
            or start < 0
            or end < start
            for start, end in self.cited_timestamp_ranges
        ):
            raise ValueError("cited timestamp ranges must be non-negative and ordered")
        if (
            self.adjudication == "accepted"
            and self.verdict is None
            and any(value is None for value in self.rubric.model_dump().values())
        ):
            raise ValueError(
                "accepted reviews require a verdict or every rubric field"
            )
        return self


class HumanReviewDataset(_StrictModel):
    schema_version: str = Field(pattern=r"[0-9]+\.[0-9]+\.[0-9]+$")
    rubric_version: str = Field(pattern=r"[0-9]+\.[0-9]+\.[0-9]+$")
    scores: list[HumanReviewScore] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_records(self) -> "HumanReviewDataset":
        keys = [(score.sample_id, score.run_id, score.reviewer_id) for score in self.scores]
        if len(keys) != len(set(keys)):
            raise ValueError("review records must be unique per sample, run, and reviewer")
        return self


def load_human_review_dataset(path: str | Path) -> HumanReviewDataset:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return HumanReviewDataset.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise GoldEvidenceError(f"human review validation failed: {exc}") from exc


def write_human_review_dataset(
    path: str | Path, dataset: HumanReviewDataset
) -> Path:
    """Write an explicitly requested local review package with answer text."""

    target = Path(path)
    target.write_text(
        yaml.safe_dump(
            dataset.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return target


@dataclass(frozen=True)
class HumanReviewAggregate:
    accepted_count: int
    pending_count: int
    disputed_count: int
    passed_count: int
    failed_count: int
    human_pass_rate: float | None
    answered_question_rate: float | None
    evidence_grounded_rate: float | None
    correct_video_rate: float | None
    timestamp_locatable_rate: float | None
    no_unsupported_claims_rate: float | None
    tone_and_guidance_rate: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _rate(values: Iterable[ScoreValue | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def aggregate_human_reviews(dataset: HumanReviewDataset) -> HumanReviewAggregate:
    """Aggregate accepted reviews; pending/disputed values never become zeros."""

    accepted = [score.rubric for score in dataset.scores if score.adjudication == "accepted"]
    verdicts = [
        score.verdict
        for score in dataset.scores
        if score.adjudication == "accepted" and score.verdict is not None
    ]
    return HumanReviewAggregate(
        accepted_count=len(accepted),
        pending_count=sum(score.adjudication == "pending" for score in dataset.scores),
        disputed_count=sum(score.adjudication == "disputed" for score in dataset.scores),
        passed_count=sum(value == "pass" for value in verdicts),
        failed_count=sum(value == "fail" for value in verdicts),
        human_pass_rate=(
            sum(value == "pass" for value in verdicts) / len(verdicts)
            if verdicts else None
        ),
        answered_question_rate=_rate(score.answered_question for score in accepted),
        evidence_grounded_rate=_rate(score.evidence_grounded for score in accepted),
        correct_video_rate=_rate(score.correct_video for score in accepted),
        timestamp_locatable_rate=_rate(score.timestamp_locatable for score in accepted),
        no_unsupported_claims_rate=_rate(score.no_unsupported_claims for score in accepted),
        tone_and_guidance_rate=_rate(score.tone_and_guidance for score in accepted),
    )
