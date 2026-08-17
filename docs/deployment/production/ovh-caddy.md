# OVHcloud Caddy production deployment

This runbook deploys the combined browser and Streamable HTTP MCP runtime at
`https://notebookai.deequoique.tech` on an Ubuntu VPS. Its safety rules remain
additive so the same procedure does not replace unrelated Caddy sites or
services if the host later becomes shared.

## Runtime shape

```text
Caddy :443 -> 127.0.0.1:8800
               |-- / and /api/v1/*: SPA + email-authenticated Web API
               `-- /mcp: Bearer-authenticated Streamable HTTP MCP

Celery worker: ingest,maintenance
Celery Beat: exactly one scheduler
PostgreSQL: external pooled Neon runtime + direct Neon migration URL
Redis/MinIO: Notebook-Agent-only containers published on loopback
```

Do not start `app.cli web-server` beside the combined runtime. With
`WEB_AUTH_ENABLED=true`, `app.cli mcp-server --transport streamable-http`
already dispatches browser and MCP traffic while keeping their credentials
isolated.

The MCP SDK keeps DNS-rebinding protection enabled. In combined mode the
validated `WEB_PUBLIC_ORIGIN` host is admitted automatically; do not disable
transport security or rewrite Caddy's upstream `Host` header to loopback.

## One-time server configuration

Create a dedicated service account and release layout. Do not reuse an
existing application account or directory.

```bash
sudo useradd --system --home-dir /var/lib/notebook-agent \
  --create-home --shell /usr/sbin/nologin notebook-agent
sudo install -d -o notebook-agent -g notebook-agent -m 0755 \
  /opt/notebook-agent /opt/notebook-agent/repository \
  /opt/notebook-agent/releases /var/lib/notebook-agent
sudo install -d -o root -g notebook-agent -m 0750 /etc/notebook-agent
```

Install Docker from the distribution repository only after checking that no
existing package, bridge, firewall policy, or published port conflicts. The
production Compose file starts only Redis and MinIO; it never starts the
repository PostgreSQL service.

Create `/etc/notebook-agent/dependencies.env` as root, mode `0600`, with newly
generated values:

```dotenv
NOTEBOOK_REDIS_PASSWORD=<random-production-secret>
NOTEBOOK_REDIS_PORT=16379
MINIO_ROOT_USER=<random-production-user>
MINIO_ROOT_PASSWORD=<random-production-secret>
MINIO_BUCKET=kb-raw
NOTEBOOK_MINIO_API_PORT=19000
NOTEBOOK_MINIO_CONSOLE_PORT=19001
```

Create `/etc/notebook-agent/notebook-agent.env` as root, mode `0600`.
Long-lived processes use only the pooled Neon URL and must never inherit the
direct migration credential.

```dotenv
DATABASE_URL=<pooled-neon-url-with-sslmode-require>
REDIS_URL=redis://:<redis-password>@127.0.0.1:16379/0
MINIO_ENDPOINT_URL=http://127.0.0.1:19000
MINIO_ROOT_USER=<same-private-user>
MINIO_ROOT_PASSWORD=<same-private-secret>
MINIO_BUCKET=kb-raw

ZHIPU_API_KEY=<provider-secret>
EMBEDDING_MODEL=embedding-3
EMBEDDING_DIMENSIONS=1536
AGENT_MODEL=<provider-model>
AGENT_API_KEY=<provider-secret>
AGENT_BASE_URL=<provider-url-if-required>

WEB_AUTH_ENABLED=true
WEB_PUBLIC_ORIGIN=https://notebookai.deequoique.tech
WEB_AUTH_SECRET=<new-at-least-32-character-secret>
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=<gmail-address>
SMTP_PASSWORD=<gmail-app-password>
SMTP_FROM_EMAIL=<gmail-address>
WEB_COOKIE_SECURE=true
WEB_FORWARDED_ALLOW_IPS=127.0.0.1
WEB_TRUSTED_PROXY_HOSTS=notebookai.deequoique.tech

MCP_HOST=127.0.0.1
MCP_PORT=8800
MCP_PATH=/mcp
MCP_URL_TOKEN_MODE=true
```

Create `/etc/notebook-agent/migrations.env` separately as root, mode `0600`.
Only `notebook-agent-migrate.service` may read it:

```dotenv
MIGRATION_DATABASE_URL=<direct-neon-url-with-sslmode-require>
```

Never print, commit, paste into GitHub Actions, or put these values on a command
line. Verify owner `root`, group `root`, and mode `0600` before
starting a unit.

Install only these five units, then run `systemd-analyze verify` against the
installed files:

```text
notebook-agent-dependencies.service
notebook-agent-migrate.service
notebook-agent-worker.service
notebook-agent-beat.service
notebook-agent.service
```

Enable only those units. Do not install the legacy
`notebook-agent-web.service` or `notebook-agent-web-migrate.service`; email Web,
API, and MCP traffic belongs to the combined runtime. Install the Caddy site
by first saving a timestamped copy and hash of `/etc/caddy/Caddyfile`. Add the
contents of `deploy/caddy/notebook-agent.caddy`, run `caddy validate`, and use a
graceful Caddy reload. Immediately smoke-test every pre-existing hostname and
upstream; restore the backup if any check differs.

## Initial release and admission

Clone the authorized repository into `/opt/notebook-agent/repository` as the
service account. Create an exact detached `main` release in
`/opt/notebook-agent/releases/<sha>`, install Python dependencies in its own
`.venv`, install frozen Web dependencies, and build `web/dist`.

Before switching, confirm one Alembic head. After switching `current`, start
`notebook-agent-migrate.service`; it must complete `upgrade head`, `current`,
and `check` and create `.migration-admitted`. A failure restores the previous
release and does not start the candidate application.

Start in this order:

```text
notebook-agent-dependencies.service
notebook-agent-migrate.service
notebook-agent-worker.service
notebook-agent-beat.service
notebook-agent.service
```

Confirm Redis and MinIO are healthy, the owned `kb-raw` bucket was admitted by
the one-shot `minio-init` container, the worker pongs and lists both queues,
there is exactly one Beat process, and `/api/v1/health` succeeds on loopback and
HTTPS. Complete a real Gmail login and verify browser Web Storage is empty.

Save and item-management capabilities have no environment switches. Admit the
combined runtime to user traffic only after all gates are green, then re-run
readiness checks.

## Dynamic evaluator grant

Create a dedicated AppUser and issue a labeled 30-day `full` grant. Compute the
expiry in UTC and capture the raw token only once in the approved private
handoff channel.

```bash
.venv/bin/python -m app.cli users create
.venv/bin/python -m app.cli mcp-grant issue --user-id <id> --scope full \
  --expires-at <utc-iso-8601> --label dynamic-evaluator-30d \
  --created-by production-bootstrap
```

Use either `Authorization: Bearer <token>` with
`https://notebookai.deequoique.tech/mcp`, or the URL-only evaluator capability
`https://notebookai.deequoique.tech/mcp/c/<token>`. Path-token mode requires
HTTPS and the Caddy site discards access logs; `?token=` remains forbidden.
Rotate, disable, or revoke the grant after evaluation or suspected disclosure.

## GitHub approval and restricted deployment

The `Production` GitHub Environment must require a human reviewer. Configure
these environment secrets only:

```text
PRODUCTION_SSH_HOST
PRODUCTION_SSH_USER
PRODUCTION_SSH_PRIVATE_KEY
PRODUCTION_SSH_KNOWN_HOSTS
```

The server account for this key must have an `authorized_keys` entry using
`restrict` and a forced command that invokes the root-owned
`deploy/scripts/notebook-agent-ssh-dispatch` wrapper. The wrapper validates the
exact command, clears `SSH_ORIGINAL_COMMAND`, and then uses sudo to invoke the
root-owned `deploy/scripts/notebook-agent-deploy` dispatcher. Its sudo policy
may allow only that dispatcher. Do not grant an interactive shell, arbitrary
sudo, forwarding, or access to the application environment file.

Every `main` push first runs the deterministic CI job. A green revision waits
for production approval, serializes with other production deployments, and
then requests `deploy <40-character-sha>`. The dispatcher accepts only the
current `origin/main`, retains the previous release, migrates before starting,
and restores the previous symlink if migration or health admission fails.

## Rollback

Rollback changes only `/opt/notebook-agent/current` and the four application
units. It does not run `docker compose down`, delete volumes, remove buckets,
or downgrade/delete Neon data. Restore the Caddy backup only when removing the
Notebook Agent hostname, validate it, reload gracefully, and recheck all
pre-existing routes.
