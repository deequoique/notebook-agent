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
.venv/bin/python -m evals.natural_language --preflight
.venv/bin/python -m evals.natural_language --prepare-fixtures
.venv/bin/python -m evals.natural_language --case retrieval.search --repeat 1
.venv/bin/python -m evals.natural_language --category context
.venv/bin/python -m evals.natural_language --smoke
.venv/bin/python -m evals.natural_language --all --repeat 3 --threshold 0.67
```

Reports under `.eval-results/natural-language/` preserve every repeat and separate model-selected Agent tools from deterministic/direct MCP activity. They omit prompts, answers, identities, tokens, tool arguments/results and storage keys. Forbidden safety-critical tools are zero tolerance. A failed-item retry fixture is never fabricated; its case is visibly skipped when unavailable.

To add a case, edit `catalog.yaml`, use a unique ID and declare each turn's route plus required/allowed/forbidden tools and acceptable statuses. Templates may only use trusted fixture values or scalar captures from earlier typed MCP results.
