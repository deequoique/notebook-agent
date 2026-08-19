# Benchmark closure and human-review summary

## Closure decision

The user accepted the completed Gold-evidence and human-review slice as the
deliverable for this task on 2026-08-19. Broader concurrency 5/10, failure
injection and a mutation-capable 22-case rerun are explicitly deferred; they
are not claimed as completed by this archive.

Product defects discovered by the benchmark are tracked separately. The
trusted-response/section findings and channel-save planning move to
`08-19-trusted-response-boundary`.

## Reviewed live run

- Run: `20260819T072852Z-7e8a6e54`
- Catalog: `1.0.0`
- Model: `openai:deepseek-v4-flash` through the configured OpenAI-compatible provider
- Samples: 20 fixed human-evaluation questions
- Review authority: manual checkboxes and notes in the ignored local review workbook
- Human result: 16 pass / 4 fail
- Failed samples: `he-003`, `he-011`, `he-019`, `he-020`

The evaluator intentionally left every case as `pending_review`; it did not
auto-assign the human verdict. The 16/20 result above comes only from the
completed manual review.

## Safe run metrics

- Tool policy pass rate: 100.0% (`n=20`)
- Safety violation rate: 0.0% (`n=20`)
- Turn latency p50: 31,023 ms
- Turn latency p95: 45,306 ms
- Average model calls per attempt: 4.85
- Average completed Agent tool calls per attempt: 3.00
- Agent loop-limit rate: 25.0%

## Automated Gold diagnostics (not verdicts)

- Recall@1: 41.2%
- Recall@3: 41.2%
- MRR: 42.5%
- Citation precision: 20.1%
- Citation completeness: 25.9%
- Timestamp hit rate: 52.9%
- No-evidence retrieval false-positive rate: 100.0%
- No-evidence citation false-positive rate: 100.0%

These numbers diagnose retrieval and citation behavior. They do not override
manual pass/fail and must not be presented as a final model-quality score.

## Findings handed off

- `he-003`: the gold segments were absent from retrieval top 3, followed by
  three Answer Agent failures reported only as `invalid_citation`. Retrieval
  miss and answer-contract failure must remain separate diagnoses.
- `he-011`, `he-019`, `he-020`: no-evidence answers attached unrelated
  Citation/source sections because the current `AnswerDraft` requires at least
  one selected segment and every accepted draft unconditionally renders
  sources.
- Channel capability text and save confirmation expose the same response
  ownership gap: the flat answer contract cannot distinguish model text from
  server-owned canonical/action output.

Detailed analysis is retained in
`research/human-review-trusted-section-findings-2026-08-19.md`.

## Verification artifacts

- Gold and rubric unit tests: `tests/test_natural_language_quality.py`
- Evaluator/report tests: `tests/test_natural_language_evaluator.py`
- Human dataset: `human_eval_samples.yaml`
- Human data contract: `human-eval-data-contract.md`
- Local readable workbook:
  `.eval-results/natural-language/20260819T072852Z-7e8a6e54/human_review.md`

The local workbook remains gitignored because it contains model answers and
source projections. This committed summary contains only reviewed outcomes,
safe aggregate metrics and defect classifications.
