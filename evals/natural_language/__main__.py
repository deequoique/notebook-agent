"""CLI for explicit paid live evaluations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import Settings
from .runner import (
    EvalConfig,
    EvalPreflightError,
    EvalTeardownError,
    LiveEvaluator,
    write_preflight_failure,
    write_report,
)
from .quality import GoldEvidenceError, human_eval_completion, load_human_eval_dataset
from .human_review import aggregate_human_reviews, load_human_review_dataset
from .schema import CatalogError, load_catalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.natural_language")
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in (
        "validate-catalog",
        "validate-human-samples",
        "score-human-review",
        "human-benchmark",
        "preflight",
        "prepare-fixtures",
        "smoke",
        "all",
    ):
        mode.add_argument(f"--{flag}", action="store_true")
    mode.add_argument("--case", action="append", dest="case_ids")
    mode.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--human-samples", type=Path)
    parser.add_argument("--human-review", type=Path)
    parser.add_argument("--human-case", action="append", dest="human_case_ids")
    parser.add_argument("--export-human-review", action="store_true")
    parser.add_argument("--require-complete-human-samples", action="store_true")
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--results-dir", type=Path)
    return parser


async def _live(args, catalog, human_dataset=None) -> int:
    config = EvalConfig.from_environment()
    repeat = args.repeat if args.repeat is not None else config.repeat
    threshold = args.threshold if args.threshold is not None else config.threshold
    if not 1 <= repeat <= 20 or (threshold is not None and not 0 < threshold <= 1):
        raise EvalPreflightError("repeat or threshold is out of bounds")
    config = EvalConfig(config.enabled, config.user_id, args.results_dir or config.results_dir, repeat, threshold, config.ingest_timeout_seconds)
    settings = Settings()
    # Human benchmark runs collect answers for manual review by default.
    # The sanitized report remains answer-free.
    export_human_review = human_dataset is not None
    evaluator = (
        LiveEvaluator(
            catalog,
            settings,
            config,
            human_dataset=human_dataset,
            export_human_review=export_human_review,
        )
        if human_dataset is not None
        else LiveEvaluator(catalog, settings, config)
    )
    results = None
    try:
        await evaluator.__aenter__()
    except EvalTeardownError:
        target = write_preflight_failure(
            config, catalog, settings, error_code="teardown_failed",
            failure_stage="infrastructure",
        )
        print(f"sanitized teardown report: {target}", file=sys.stderr)
        raise
    except Exception:
        target = write_preflight_failure(config, catalog, settings, error_code="preflight_unavailable")
        print(f"sanitized preflight report: {target}", file=sys.stderr)
        raise EvalPreflightError("live evaluation preflight failed") from None
    try:
        if args.preflight:
            print("preflight ok: full stack and full MCP profile ready")
            return 0
        try:
            fixtures = await evaluator.prepare()
        except Exception:
            target = write_preflight_failure(config, catalog, settings, error_code="fixture_unavailable", failure_stage="fixture")
            print(f"sanitized fixture report: {target}", file=sys.stderr)
            raise EvalPreflightError("live evaluation fixture failed") from None
        if args.prepare_fixtures:
            print("fixtures ready and retained")
            return 0
        cases = (
            _select_human(args, evaluator.human_cases())
            if human_dataset is not None
            else _select(args, catalog.cases)
        )
        if not cases:
            raise EvalPreflightError("no catalog cases matched")
        results = await evaluator.run(cases, repeat=repeat, threshold=threshold)
        failed = sum(value.status == "fail" for value in results)
    finally:
        try:
            await evaluator.__aexit__(None, None, None)
        except EvalTeardownError:
            target = write_preflight_failure(
                config,
                catalog,
                settings,
                error_code="teardown_failed",
                failure_stage="infrastructure",
            )
            print(f"sanitized teardown report: {target}", file=sys.stderr)
            raise
    # A success/model-result report is authoritative only after the temporary
    # grant is revoked and the MCP subprocess + stderr tempfile are closed.
    assert results is not None
    target = write_report(evaluator, results)
    if export_human_review:
        review_target = evaluator.write_human_review_export(
            target / "human_review.yaml"
        )
        readable_target = evaluator.write_human_review_markdown(
            target / "human_review.md"
        )
        print(f"local human review package: {review_target}")
        print(f"readable human review workbook: {readable_target}")
    pending = sum(value.status == "pending_review" for value in results)
    print(
        f"{sum(value.status == 'pass' for value in results)} pass / "
        f"{failed} fail / "
        f"{sum(value.status == 'skip' for value in results)} skip / "
        f"{pending} pending review"
    )
    print(f"sanitized report: {target}")
    return 1 if failed else 0


def _select(args, cases):
    if args.all:
        return list(cases)
    if args.smoke:
        return [case for case in cases if case.smoke]
    if args.case_ids:
        requested = set(args.case_ids)
        found = [case for case in cases if case.id in requested]
        if requested - {case.id for case in found}:
            raise EvalPreflightError("unknown case id")
        return found
    if args.categories:
        return [case for case in cases if case.category in set(args.categories)]
    return []


def _select_human(args, cases):
    requested_ids = getattr(args, "human_case_ids", None)
    if not requested_ids:
        return list(cases)
    requested = set(requested_ids)
    found = [case for case in cases if case.id in requested]
    if requested - {case.id for case in found}:
        raise EvalPreflightError("unknown human case id")
    return found


def main() -> None:
    args = _parser().parse_args()
    try:
        catalog = load_catalog(args.catalog)
        if args.validate_catalog:
            print(f"catalog {catalog.version}: {len(catalog.cases)} cases valid")
            return
        if args.validate_human_samples:
            if args.human_samples is None:
                raise GoldEvidenceError(
                    "--validate-human-samples requires --human-samples PATH"
                )
            dataset = load_human_eval_dataset(
                args.human_samples,
                require_complete=args.require_complete_human_samples,
            )
            completion = human_eval_completion(dataset)
            print(
                f"human samples {dataset.dataset_id} {dataset.schema_version}: "
                f"{len(dataset.samples)} samples valid, "
                f"{completion.complete_count} complete, {completion.draft_count} draft"
            )
            return
        if args.score_human_review:
            if args.human_review is None:
                raise GoldEvidenceError(
                    "--score-human-review requires --human-review PATH"
                )
            aggregate = aggregate_human_reviews(
                load_human_review_dataset(args.human_review)
            )
            print(json.dumps(aggregate.as_dict(), ensure_ascii=False, indent=2))
            return
        human_dataset = None
        if args.human_benchmark:
            if args.human_samples is None:
                raise GoldEvidenceError(
                    "--human-benchmark requires --human-samples PATH"
                )
            human_dataset = load_human_eval_dataset(
                args.human_samples,
                require_complete=True,
            )
        elif args.export_human_review:
            raise GoldEvidenceError(
                "--export-human-review requires --human-benchmark"
            )
        raise SystemExit(asyncio.run(_live(args, catalog, human_dataset)))
    except (
        CatalogError,
        GoldEvidenceError,
        EvalPreflightError,
        EvalTeardownError,
        RuntimeError,
    ) as exc:
        print(f"natural-language eval unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print(
            "natural-language eval unavailable: bounded infrastructure failure",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
