# Validation evidence

## Candidate and upstream

- Candidate branch: `codex/frontend-delivery-integration`.
- Upstream baseline: `upstream/main@6539f3b`.
- Deployment implementation commit: `8c6dbc306187ea0890729b90e2ef1b2d89cb4808`.
- After fetch, the candidate was ahead of upstream and behind by zero commits.
- Migration graph has one head: `a7b8c9d0e1f2`.

## Fresh automated checks

- Focused Python deployment/Web suite exited successfully.
- `python -m compileall -q app migrations tests/test_linux_frontend_deployment.py` passed.
- `alembic heads` returned only `a7b8c9d0e1f2`; history showed the expected
  `d3f4a5b6c7d8, f6a7b8c9d0e1 -> a7b8c9d0e1f2` mergepoint.
- `git diff --check` passed.
- Frozen frontend install and OpenAPI contract check passed.
- Vitest passed 13 files / 59 tests.
- TypeScript typecheck, ESLint with zero warnings, and the Vite production build
  passed; the production artifact was emitted to `web/dist`.

## Local production-shape smoke

A task-owned API-only Web process and Vite static server were started on
loopback ports 8000 and 5181 without taking over the existing 5173/5175
listeners. The SPA routes `/`, `/library`, and `/videos/user-interviews`
returned HTML; `/api/v1/health` and `/api/v1/capabilities` returned JSON; an
unknown `/api/v1/*` route returned JSON 404. The exact task-owned processes were
stopped afterward.

## External gates intentionally not claimed

- No team domain, SSH host, SSH user/key path, or authorized domestic-server
  inventory was available, so no external machine was modified.
- This Windows host has no Nginx or systemd. The runbook requires target-host
  `nginx -t` and `systemd-analyze verify` before traffic switching.
- No isolated PostgreSQL test database was available for a real migration
  upgrade/current/check rehearsal.
- Public HTTPS login, cookie/CSRF, dependency readiness, and one real mutation
  remain acceptance checks for the selected server.

## Review disposition

- Final deployment architecture review: `CLEAR` with no open P1/P2.
- Final code review: `APPROVE` with no open P1/P2.
- Existing upstream PR: `https://github.com/deequoique/notebook-agent/pull/2`.
- The PR remains review-only and must not be merged automatically.

The task remains in `review` until those external gates are completed. The
existing upstream PR must not be merged automatically.
