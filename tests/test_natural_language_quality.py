from __future__ import annotations

import pytest

from app.config import Settings
from evals.natural_language.mcp_runtime import DiagnosticCollector
from evals.natural_language.quality import (
    AnswerBoundary,
    EvidenceHit,
    FixtureRef,
    GoldEvidenceError,
    GoldSample,
    HumanEvalDataset,
    aggregate_quality_scores,
    classify_evidence_failures,
    human_eval_completion,
    score_gold_sample,
    validate_human_eval_dataset,
)
from evals.natural_language.runner import EvalConfig, LiveEvaluator
from evals.natural_language.schema import load_catalog
from evals.natural_language.human_review import (
    HumanReviewDataset,
    aggregate_human_reviews,
    load_human_review_dataset,
)


def _sample(**overrides) -> GoldSample:
    value = {
        "sample_id": "he-001",
        "case_id": "retrieval.search",
        "turn_index": 1,
        "kind": "multi_segment",
        "query": "问题",
        "fixture_ref": {"fixture_alias": "baseline"},
        "gold_item_id": "baseline",
        "gold_segment_ids": ["seg-a", "seg-b"],
        "gold_timestamp_range": [10, 20],
        "timestamp_tolerance_sec": 3,
        "evidence_groups": [["seg-a"], ["seg-b"]],
        "reference_points": ["事实 A", "事实 B"],
        "reference_answer": "参考答案",
        "answer_boundary": {
            "must_include": ["事实 A"],
            "acceptable_paraphrases": ["同义表达"],
            "must_not_claim": ["未提供的事实"],
        },
        "no_evidence": False,
    }
    value.update(overrides)
    return GoldSample.model_validate(value)


def test_gold_sample_requires_stable_evidence_contract():
    draft = _sample(gold_item_id=None)
    assert not draft.is_gold_complete
    negative = _sample(
        kind="no_evidence",
        no_evidence=True,
        gold_item_id=None,
        gold_segment_ids=[],
        gold_timestamp_range=None,
        evidence_groups=[],
    )
    assert negative.no_evidence is True


def test_no_evidence_sample_requires_explicit_refusal_boundary():
    draft = _sample(
        kind="no_evidence",
        no_evidence=True,
        gold_item_id=None,
        gold_segment_ids=[],
        gold_timestamp_range=None,
        evidence_groups=[],
        answer_boundary=AnswerBoundary(must_not_claim=[]),
    )
    assert not draft.is_gold_complete


def test_fixture_ref_requires_alias_or_platform_identity():
    with pytest.raises(ValueError, match="fixture_ref"):
        FixtureRef.model_validate({})
    assert FixtureRef(fixture_alias="baseline")
    assert FixtureRef(platform="youtube", platform_id="abc")


def test_quality_metrics_score_rank_citation_completeness_and_timestamp():
    sample = _sample()
    score = score_gold_sample(
        sample,
        retrieved=[
            {"item_id": "baseline", "segment_id": "wrong", "start_sec": 1},
            {"item_id": "baseline", "segment_id": "seg-a", "start_sec": 11},
            {"item_id": "baseline", "segment_id": "seg-b", "start_sec": 19},
        ],
        citations=[
            {"item_id": "baseline", "segment_id": "seg-a", "start_sec": 11},
            {"item_id": "other", "segment_id": "seg-b", "start_sec": 12},
        ],
    )
    assert score.recall_at_1 == 0
    assert score.recall_at_3 == 1
    assert score.mrr == pytest.approx(0.5)
    assert score.citation_precision == pytest.approx(0.5)
    assert score.citation_completeness == pytest.approx(0.5)
    assert score.timestamp_hit_rate == 1


def test_no_evidence_is_scored_separately_from_retrieval_quality():
    sample = _sample(
        sample_id="he-negative",
        kind="no_evidence",
        no_evidence=True,
        gold_item_id=None,
        gold_segment_ids=[],
        gold_timestamp_range=None,
        evidence_groups=[],
    )
    score = score_gold_sample(
        sample,
        retrieved=[{"item_id": "baseline", "segment_id": "seg-a"}],
        citations=[],
    )
    assert score.recall_at_1 is None
    assert score.mrr is None
    assert score.retrieval_false_positive == 1
    assert score.citation_false_positive == 0


def test_he003_failure_classification_preserves_retrieval_and_answer_layers():
    sample = _sample()
    classifications = classify_evidence_failures(
        sample,
        retrieved=[{"item_id": "baseline", "segment_id": "wrong"}],
        citations=[],
        diagnostic_events=[
            {
                "stage": "citation_validated",
                "agent_phase": "answer",
                "error_code": "answer_unavailable",
                "failure_reason": "unknown_citation",
            }
        ],
    )
    assert classifications == ("retrieval_miss", "answer_contract_failure")


def test_evidence_selection_failure_requires_gold_candidate_without_leaking_ids():
    sample = _sample()
    classifications = classify_evidence_failures(
        sample,
        retrieved=[{"item_id": "baseline", "segment_id": "seg-a"}],
        citations=[],
    )
    assert classifications == ("evidence_selection_miss",)


def test_aggregate_keeps_positive_and_negative_denominators_separate():
    positive = score_gold_sample(_sample(), [], [])
    negative = score_gold_sample(
        _sample(
            sample_id="he-negative",
            kind="no_evidence",
            no_evidence=True,
            gold_item_id=None,
            gold_segment_ids=[],
            gold_timestamp_range=None,
            evidence_groups=[],
        ),
        [],
        [{"item_id": "baseline", "segment_id": "seg-a"}],
    )
    aggregate = aggregate_quality_scores([positive, negative])
    assert aggregate.sample_count == 2
    assert aggregate.no_evidence_count == 1
    assert aggregate.recall_at_1 == 0
    assert aggregate.citation_precision == 0
    assert aggregate.citation_false_positive_rate == 1


def test_complete_dataset_gate_is_separate_from_shape_validation():
    dataset = HumanEvalDataset(
        schema_version="1.0.0", dataset_id="human-eval-v1", language="zh-CN", samples=[]
    )
    assert validate_human_eval_dataset(dataset) is dataset
    with pytest.raises(GoldEvidenceError, match="20 to 30"):
        validate_human_eval_dataset(dataset, require_complete=True)


def test_draft_authoring_shape_accepts_top_level_fixture_alias_and_missing_gold():
    from evals.natural_language.quality import HumanEvalDataset

    dataset = HumanEvalDataset.model_validate(
        {
            "schema_version": "1.0.0",
            "dataset_id": "human-eval-v1",
            "language": "zh-CN",
            "samples": [
                {
                    "sample_id": "he-draft",
                    "case_id": "retrieval.search",
                    "turn_index": 1,
                    "kind": "direct_keyword",
                    "fixture_alias": "baseline",
                    "query": "问题",
                    "reference_answer": "参考答案",
                }
            ],
        }
    )
    sample = dataset.samples[0]
    assert sample.fixture_ref is not None
    assert sample.fixture_ref.fixture_alias == "baseline"
    assert not sample.is_gold_complete
    summary = human_eval_completion(dataset)
    assert summary.sample_count == 1
    assert summary.draft_count == 1
    assert summary.incomplete_sample_ids == ("he-draft",)


def test_evidence_hit_rejects_reversed_timestamp_interval():
    with pytest.raises(ValueError, match="ordered"):
        EvidenceHit(item_id=1, segment_id=2, start_sec=20, end_sec=10)


def test_human_review_aggregate_excludes_pending_and_disputed_records():
    accepted = {
        "sample_id": "he-001",
        "run_id": "run-1",
        "reviewer_id": "r1",
        "model_answer": "答案",
        "rubric": {
            "answered_question": 1,
            "evidence_grounded": 1,
            "correct_video": 1,
            "timestamp_locatable": 0,
            "no_unsupported_claims": 1,
            "tone_and_guidance": 1,
        },
        "adjudication": "accepted",
    }
    pending = {
        **accepted,
        "sample_id": "he-002",
        "reviewer_id": "r2",
        "rubric": {"answered_question": None},
        "adjudication": "pending",
    }
    disputed = {
        **accepted,
        "sample_id": "he-003",
        "reviewer_id": "r3",
        "adjudication": "disputed",
    }
    aggregate = aggregate_human_reviews(
        HumanReviewDataset.model_validate(
            {"schema_version": "1.0.0", "rubric_version": "1.0.0", "scores": [accepted, pending, disputed]}
        )
    )
    assert aggregate.accepted_count == 1
    assert aggregate.pending_count == 1
    assert aggregate.disputed_count == 1
    assert aggregate.passed_count == 0
    assert aggregate.human_pass_rate is None
    assert aggregate.answered_question_rate == 1
    assert aggregate.timestamp_locatable_rate == 0


def test_human_dataset_loader_is_strict_only_when_release_gate_is_requested(tmp_path):
    path = tmp_path / "human.yaml"
    path.write_text(
        "schema_version: '1.0.0'\n"
        "dataset_id: human-eval-v1\n"
        "language: zh-CN\n"
        "samples: []\n",
        encoding="utf-8",
    )
    from evals.natural_language.quality import load_human_eval_dataset

    assert load_human_eval_dataset(path).samples == []
    with pytest.raises(GoldEvidenceError, match="20 to 30"):
        load_human_eval_dataset(path, require_complete=True)


def test_human_cases_use_read_only_quality_expectations_with_gold_queries(tmp_path):
    dataset = HumanEvalDataset(
        schema_version="1.0.0",
        dataset_id="human-eval-v1",
        language="zh-CN",
        samples=[_sample()],
    )
    evaluator = LiveEvaluator(
        load_catalog(),
        Settings(),
        EvalConfig(False, None, tmp_path, 1, None, 30),
        human_dataset=dataset,
    )
    cases = evaluator.human_cases()
    assert [case.id for case in cases] == ["human.he-001"]
    assert cases[0].turns[0].input == "问题"
    assert cases[0].turns[0].expect.required_tools == ["search_segments"]
    assert "list_saved_items" in cases[0].turns[0].expect.allowed_tools
    assert "save_videos" in cases[0].turns[0].expect.forbidden_tools


def test_human_turn_scores_safe_retrieval_projection(tmp_path):
    sample = _sample(
        gold_item_id="153",
        gold_segment_ids=["1309", "1311"],
        evidence_groups=[["1309"], ["1311"]],
        gold_timestamp_range=[0, 20],
    )
    dataset = HumanEvalDataset(
        schema_version="1.0.0",
        dataset_id="human-eval-v1",
        language="zh-CN",
        samples=[sample],
    )
    evaluator = LiveEvaluator(
        load_catalog(),
        Settings(),
        EvalConfig(False, None, tmp_path, 1, None, 30),
        human_dataset=dataset,
    )
    collector = DiagnosticCollector()
    request_id = "a" * 32
    collector.write(
        f'{{"event":"knowledge_request","stage":"model_attempt","request_id":"{request_id}"}}\n'
        f'{{"event":"knowledge_request","stage":"tool_call","request_id":"{request_id}","tool_name":"search_segments","call_index":1,"tool_outcome":"succeeded"}}\n'
        f'{{"event":"retrieval_detail","request_id":"{request_id}","tool_name":"search_segments","call_index":1,"item_id":153,"segment_id":1309,"start":1,"excerpt":"private"}}\n'
    )
    evaluator.runtime = type("Runtime", (), {"diagnostics": collector})()
    turn = evaluator.human_cases()[0].turns[0]
    result = evaluator._assert_turn(
        1,
        turn,
        {
            "status": "ok",
            "request_id": request_id,
            "answer": "answer",
            "citations": [
                {"item_id": 153, "segment_id": 1309, "start_sec": 1},
                {"item_id": 153, "segment_id": 1311, "start_sec": 2},
            ],
        },
        10,
        sample.query,
        gold_sample=sample,
    )
    assert result.passed
    assert result.quality is not None
    assert result.quality["recall_at_1"] == 1
    assert result.quality["citation_precision"] == 1
    assert result.quality["citation_completeness"] == 1


def test_human_turn_scores_missing_search_as_empty_retrieval(tmp_path):
    sample = _sample()
    dataset = HumanEvalDataset(
        schema_version="1.0.0",
        dataset_id="human-eval-v1",
        language="zh-CN",
        samples=[sample],
    )
    evaluator = LiveEvaluator(
        load_catalog(),
        Settings(),
        EvalConfig(False, None, tmp_path, 1, None, 30),
        human_dataset=dataset,
    )
    collector = DiagnosticCollector()
    request_id = "b" * 32
    collector.write(
        f'{{"event":"knowledge_request","stage":"model_attempt","request_id":"{request_id}"}}\n'
    )
    evaluator.runtime = type("Runtime", (), {"diagnostics": collector})()

    result = evaluator._assert_turn(
        1,
        evaluator.human_cases()[0].turns[0],
        {"status": "ok", "request_id": request_id, "citations": []},
        10,
        sample.query,
        gold_sample=sample,
    )

    assert result.quality is not None
    assert result.quality["recall_at_1"] == 0
    assert result.quality["citation_precision"] == 0
    assert "gold retrieval projection unavailable" not in result.failures
    assert result.passed
    assert "gold evidence missing from retrieval top 3" in result.automated_observations


def test_explicit_human_review_export_keeps_answers_outside_report(tmp_path):
    sample = _sample()
    dataset = HumanEvalDataset(
        schema_version="1.0.0",
        dataset_id="human-eval-v1",
        language="zh-CN",
        samples=[sample],
    )
    evaluator = LiveEvaluator(
        load_catalog(),
        Settings(),
        EvalConfig(False, None, tmp_path, 1, None, 30),
        human_dataset=dataset,
        export_human_review=True,
    )
    collector = DiagnosticCollector()
    request_id = "c" * 32
    collector.write(
        f'{{"event":"knowledge_request","stage":"model_attempt","request_id":"{request_id}"}}\n'
    )
    evaluator.runtime = type("Runtime", (), {"diagnostics": collector})()
    evaluator._assert_turn(
        1,
        evaluator.human_cases()[0].turns[0],
        {
            "status": "ok",
            "request_id": request_id,
            "answer": "仅保存在人工评测包中的答案",
            "citations": [
                {
                    "item_id": 153,
                    "segment_id": 1309,
                    "start_sec": 1.5,
                    "end_sec": 2.5,
                }
            ],
        },
        10,
        sample.query,
        gold_sample=sample,
    )

    target = evaluator.write_human_review_export(tmp_path / "review.yaml")
    readable = evaluator.write_human_review_markdown(tmp_path / "review.md")
    exported = load_human_review_dataset(target)

    assert exported.scores[0].model_answer == "仅保存在人工评测包中的答案"
    assert exported.scores[0].cited_timestamp_ranges == [(1.5, 2.5)]
    assert exported.scores[0].adjudication == "pending"
    workbook = readable.read_text(encoding="utf-8")
    assert "### Human reference" in workbook
    assert "### Agent answer" in workbook
    assert "仅保存在人工评测包中的答案" in workbook


def test_human_verdict_can_be_accepted_without_forcing_rubric_scores():
    score = {
        "sample_id": "he-004",
        "run_id": "run-1",
        "reviewer_id": "reviewer-1",
        "model_answer": "人工认为可通过的答案",
        "rubric": {},
        "verdict": "pass",
        "adjudication": "accepted",
    }
    aggregate = aggregate_human_reviews(HumanReviewDataset.model_validate({
        "schema_version": "1.0.0",
        "rubric_version": "1.0.0",
        "scores": [score],
    }))
    assert aggregate.passed_count == 1
    assert aggregate.failed_count == 0
    assert aggregate.human_pass_rate == 1.0
