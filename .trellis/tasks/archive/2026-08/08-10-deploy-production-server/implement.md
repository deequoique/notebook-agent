# Implementation plan

## 1. Repository preparation

- [ ] Wait until the YouTube subtitle fix is committed, reviewed, and merged to
      `main`; record the exact clean SHA and confirm it is available on origin.
- [ ] Add the ICP filing link to the shared public Web layout with a focused
      component test and mobile layout coverage.
- [ ] Add production deployment assets: combined ASGI systemd unit, worker,
      single Beat, migration admission, isolated Redis/MinIO configuration,
      restricted server deploy entry point, and operator documentation.
- [ ] Add a loopback Gateway unit, patched LangBot unit, pinned-wheel installer,
      required bridge plugin configuration, and private SSH-tunnel operations.
- [ ] Extend `.github/workflows/web-auth-contract.yml` or add a dependent
      production workflow with `environment: Production`, `concurrency`, exact
      SHA reporting, pinned host-key verification, and restricted SSH use.
- [ ] Ensure no production secret, IP-private inventory, raw grant token, DSN,
      or Gmail credential is committed.

## 2. Local validation before touching the server

- [ ] Run focused backend deployment, Web runtime, MCP, tasks, and notification
      tests.
- [ ] Apply the LangBot patch to the verified 4.10.6 wheel, compile changed
      files, and run the bridge/startup patch tests.
- [ ] Run `alembic heads` and require exactly one head.
- [ ] Run Web `check:api`, tests, typecheck, lint, and build.
- [ ] Run shell/systemd/config syntax checks and `git diff --check`.
- [ ] Review the diff specifically for secret exposure, duplicate Web
      listeners, public dependency ports, multiple Beat instances, broad sudo,
      and destructive commands.

## 3. Read-only server preflight

- [ ] Re-snapshot OS resources, installed packages, listening ports, running
      services/processes, Caddy config/hash, DNS, and existing target paths.
- [ ] Confirm `127.0.0.1:8800` and selected Redis/MinIO loopback ports are free.
- [ ] Confirm `127.0.0.1:8765` and `127.0.0.1:5300` are free and no existing
      LangBot installation or channel data will be replaced.
- [ ] Verify outbound GitHub, Neon, Gmail SMTP, model, embedding, and certificate
      endpoints without printing credentials.
- [ ] Stop without mutation if resource, firewall, package, port, or ownership
      conflicts could affect an unrelated service.

## 4. One-time isolated bootstrap

- [ ] Create dedicated service/deploy identities and `/opt/notebook-agent`,
      `/etc/notebook-agent`, data, release, and log paths with least privilege.
- [ ] Install only the selected Redis/MinIO runtime; bind it to loopback/private
      networking and create Notebook-Agent-specific names, volumes, and secrets.
- [ ] Generate server-only Web Auth, Redis, MinIO, and deploy credentials.
- [ ] Transfer the pooled/direct Neon, model, embedding, and Gmail settings into
      the root-owned environment file without echoing values.
- [ ] Install and validate systemd units for migration, combined ASGI, worker,
      exactly one Beat, loopback Gateway, and patched LangBot.
- [ ] Install the loopback Gateway and patched LangBot units under dedicated
      ownership. Generate an independent gateway secret, install the bridge
      plugin with mode-`0600` private configuration, and keep its bot mapping
      empty until the Telegram bot UUID is created through the private UI.
- [ ] Back up Caddy, add the isolated `notebookai.deequoique.tech` site, validate,
      gracefully reload, and immediately recheck existing routes.
- [ ] Install the forced-command SSH deploy boundary and configure the GitHub
      `Production` Environment approval requirement and secrets.

## 5. Initial release

- [ ] Materialize the exact approved `main` SHA in a new release directory,
      create its venv, install pinned Python dependencies, install frozen pnpm
      dependencies, and build `web/dist`.
- [ ] Run migration admission with the direct Neon URL; verify head/current/check
      without exposing DSNs.
- [ ] Start isolated Redis/MinIO, worker, single Beat, and combined ASGI with
      mutation flags initially disabled.
- [ ] Verify dependency health, worker pong/queues, notification heartbeat,
      loopback Web/MCP health, public HTTPS routes, security headers, SPA
      refresh, ICP link, and pre-existing Caddy routes.
- [ ] Complete one Gmail login smoke test; assert Web Storage remains empty and
      cookies/CSRF behave as designed.
- [ ] Create a dedicated evaluator user and issue a labeled 30-day `full` grant.
      Privately hand off the one-time raw token.
- [ ] Through the official MCP client, verify initialize, ten-tool discovery,
      a read call, and one bounded submission. Verify both Bearer and HTTPS
      `/mcp/c/<token>` authentication without logging the raw token. Confirm
      the worker processes it.
- [ ] Enable mutation flags only after all readiness gates pass, restart only
      Notebook Agent units, and repeat critical checks.
- [ ] Access LangBot through an SSH tunnel, create only the Telegram adapter and
      bridge-only pipeline, update `KB_BOT_CHANNELS` with its UUID, restart
      LangBot, and require the patched bridge-initialized marker before the
      Telegram adapter starts. Complete one human Telegram E2E message.

## 6. Automated release and rollback proof

- [ ] Trigger a no-op or follow-up `main` release, confirm CI must pass and the
      GitHub `production` approval gate blocks SSH until approved.
- [ ] Confirm concurrency prevents overlapping deploys and the deployed SHA is
      observable.
- [ ] Exercise rollback to the retained previous release without touching
      remote Neon data, dependency data, or unrelated services; return to the
      intended release after verification.
- [ ] Compare pre/post server snapshots and record all Notebook-Agent-owned
      units, ports, paths, credentials locations, health checks, token rotation,
      and recovery commands.

## Validation commands

```bash
python -m pytest -q tests/test_deployment_cli.py \
  tests/test_production_caddy_deployment.py tests/test_mcp_server.py \
  tests/test_web_api_runtime.py tests/test_tasks.py \
  tests/test_ingest_notifications.py
python -m alembic heads
sh -n scripts/notebook-agent
git diff --check

corepack pnpm --dir web check:api
corepack pnpm --dir web test
corepack pnpm --dir web typecheck
corepack pnpm --dir web lint
corepack pnpm --dir web build
```

## Stop/rollback gates

- Stop before shared-host mutation if a preflight differs materially from the
  recorded baseline.
- Restore the Caddy backup immediately if validation, reload, or an existing
  route check fails.
- Do not switch `current` when dependency, migration, build, Web, MCP, or worker
  validation fails.
- Never automatically downgrade Neon migrations or delete Redis/MinIO/Neon
  data during rollback.
