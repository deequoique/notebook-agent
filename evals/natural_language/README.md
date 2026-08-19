# Real-model natural-language evaluation

This opt-in evaluator calls the configured real model through the official MCP v2 stdio client and the complete Notebook Agent stack. It makes paid provider calls and intentionally retains data owned by the dedicated evaluation user. Install the project `dev` extra for PyYAML.

Required configuration includes the normal PostgreSQL/pgvector, Redis, Celery `ingest` and `maintenance` queues, MinIO, embedding and Agent provider variables, plus:

```bash
export NATURAL_LANGUAGE_EVAL_ENABLED=true
export NATURAL_LANGUAGE_EVAL_USER_ID=123
```

The runner issues one short-lived full grant and revokes only that grant in `finally`. Knowledge, conversations, ingestion rows and objects remain. It refuses fake models, production-mode execution, incomplete readiness, non-current migrations, or MCP discovery other than the exact ten full-scope tools.

```bash
.venv/bin/python -m evals.natural_language --validate-catalog
.venv/bin/python -m evals.natural_language --validate-human-samples \
  --human-samples .trellis/tasks/08-18-agent-quality-benchmark/human_eval_samples.yaml
.venv/bin/python -m evals.natural_language --validate-human-samples \
  --require-complete-human-samples \
  --human-samples .trellis/tasks/08-18-agent-quality-benchmark/human_eval_samples.yaml
.venv/bin/python -m evals.natural_language --human-benchmark --repeat 1 \
  --human-samples .trellis/tasks/08-18-agent-quality-benchmark/human_eval_samples.yaml
.venv/bin/python -m evals.natural_language --score-human-review \
  --human-review .eval-results/natural-language/RUN_ID/human_review.yaml
.venv/bin/python -m evals.natural_language --preflight
.venv/bin/python -m evals.natural_language --prepare-fixtures
.venv/bin/python -m evals.natural_language --case retrieval.search --repeat 1
.venv/bin/python -m evals.natural_language --category context
.venv/bin/python -m evals.natural_language --smoke
.venv/bin/python -m evals.natural_language --all --repeat 3 --threshold 0.67
```

Reports under `.eval-results/natural-language/` preserve every repeat and separate model-selected Agent tools from deterministic/direct MCP activity. They omit prompts, answers, identities, tokens, tool arguments/results and storage keys. Forbidden safety-critical tools are zero tolerance. A failed-item retry fixture is never fabricated; its case is visibly skipped when unavailable.

To add a case, edit `catalog.yaml`, use a unique ID and declare each turn's route plus required/allowed/forbidden tools and acceptable statuses. Templates may only use trusted fixture values or scalar captures from earlier typed MCP results.

Human answer-quality samples are authored separately from the behavior catalog.
Fill the task's `human_eval_samples.yaml` according to
`human-eval-data-contract.md`; the validation command requires 20–30 samples,
all six question kinds, at least four multi-segment samples, and at least three
no-evidence samples only when `--require-complete-human-samples` is supplied.
Without that flag it validates the authoring draft and reports how many samples
still lack Gold evidence. It validates stable fixture/evidence identities and
answer boundaries but does not call a model or include answers in the sanitized
live report. Model responses and reviewer scores belong in the opt-in local review
export under `.eval-results/`. Review records use the validated
`HumanReviewDataset` contract: only `adjudication: accepted` records contribute
to rubric rates; `pending` and `disputed` records remain visible and are never
treated as zero scores.

The default human-sample validation accepts an authoring draft and reports how
many samples are complete. Add `--require-complete-human-samples` as the
release gate; it then requires 20–30 samples, all six question kinds, at least
four multi-segment samples, at least three no-evidence samples, and complete
gold item/segment/timestamp metadata for every scorable sample.

`--human-benchmark` runs the complete Gold set through the same paid real-model
MCP stack. Every successfully recorded case remains `pending_review`; automatic
Gold diagnostics never decide pass or fail. The command always writes both a
structured `human_review.yaml` and readable `human_review.md` beside the
sanitized report. It requires development-only retrieval diagnostics. The collector
keeps only ranked item ID, segment ID, start time, and score long enough to
calculate metrics; query text, titles, URLs, excerpts, and raw tool payloads do
not enter the sanitized report. Use repeatable `--human-case human.he-006`
filters for a bounded live check before paying for the complete set; without a
filter, every complete Gold sample runs.

The review files contain model answer text and citations and are written under
the ignored `.eval-results/` tree. Set `verdict` to `pass` or `fail`, replace
the placeholder reviewer ID, and set `adjudication` to `accepted`; the six
rubric fields remain optional supporting detail. Then use
`--score-human-review` to aggregate accepted human verdicts. Pending and
disputed records remain outside the human pass-rate denominator.
