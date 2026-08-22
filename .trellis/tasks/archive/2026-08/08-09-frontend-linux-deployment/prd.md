# Frontend Linux same-origin deployment

## Goal

Reconcile the latest upstream schema and make the standalone Web package
deployable on the team's Linux server as static assets, with the Python Web API
running as a separate loopback service behind the same HTTPS origin.

## Requirements

- Integrate the latest `upstream/main` before forming a deployment candidate.
- Preserve upstream exact-video routing and durable ingestion-completion queue
  behavior together with the Web tenant/archive/deleted boundaries.
- Resolve the duplicate Alembic revision ID without rewriting a published
  upstream migration. The final graph must have exactly one new head.
- Treat the repository-root Vercel/Neon deployment as a separate competition
  health environment. Do not repoint or delete it.
- Add a Linux deployment surface consistent with the repository's supported
  single-host topology: Nginx serves `web/dist`, `/api/v1/*` proxies to a
  loopback-only `web-server`, and the channel gateway remains private.
- Keep browser authentication same-origin. Do not add wildcard CORS, domain
  cookies, browser-stored tokens, or a cross-origin fallback.
- Keep secrets and concrete server identity outside the repository. Templates
  may contain an obvious example domain but no guessed production address.
- If no SSH host/domain/credential is discoverable, finish and verify the
  deployable configuration and record remote rollout as an external gate; do
  not claim that production was changed.

## Acceptance Criteria

- [ ] Latest upstream is integrated with no unresolved conflicts or lost Web
  security filters.
- [ ] Alembic reports one unique head and deployment revision references agree.
- [ ] A reviewed Nginx configuration provides SPA fallback, static caching,
  frontend security headers, privacy-safe logging, and `/api/v1/*` proxying.
- [ ] A hardened systemd unit starts the API-only Web service with
  `WEB_SERVE_STATIC=false` on loopback.
- [ ] Documentation gives install, build, atomic update/rollback, configuration,
  TLS, health, and first-login smoke steps for a generic Linux host.
- [ ] Focused backend/config/deployment tests and the full frontend validation
  lane pass from a frozen dependency install.
- [ ] A local production-shape smoke proves SPA routes and `/api/v1` remain
  separated without exposing the channel gateway.
- [ ] Remote deployment is either verified against an explicit authorized host
  or recorded as blocked on the exact missing host/domain/access information.
- [ ] Changes are committed and pushed only to the user's fork PR branch; the
  upstream PR remains unmerged.

## Non-goals

- Creating a second Git repository or reusable npm component library.
- Moving PostgreSQL, Redis, MinIO, LangBot, MCP, or the loopback gateway to a
  new provider in this task.
- Committing certificates, private keys, API tokens, database passwords, or a
  real host inventory.
- Automatically merging the upstream PR.
