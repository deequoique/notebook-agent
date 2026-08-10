# Production deployment design

## Architecture and boundaries

```text
GitHub main + green CI + production approval
                    |
                    | dedicated restricted SSH deploy identity
                    v
        /opt/notebook-agent/releases/<git-sha>
                    |
          atomic current symlink switch
                    |
                    v
Caddy :443 -> 127.0.0.1:8800 combined ASGI
                         |-- SPA and /api/v1/* (email cookie auth)
                         `-- /mcp (Bearer grant auth)

combined ASGI / worker / Beat
        |-- pooled Neon PostgreSQL runtime URL
        |-- direct Neon URL for one-shot Alembic only
        |-- loopback Redis broker/backend
        `-- loopback MinIO object storage

Telegram -> patched LangBot 4.10.6 -> required bridge plugin
                                      -> 127.0.0.1:8765 gateway
                                      -> Notebook Agent ChannelService
```

The combined ASGI entry point is
`python -m app.cli mcp-server --transport streamable-http` with
`WEB_AUTH_ENABLED=true`. The dispatcher selects `MCP_PATH` before the browser
application, so browser cookies cannot authenticate MCP and MCP Bearer tokens
cannot authenticate Web routes. A separate `web-server` process would duplicate
the Web listener and is therefore not part of this topology.

No component binds a new public port. Caddy remains the only public HTTP/TLS
listener. Channel Gateway binds `127.0.0.1:8765`; the patched LangBot WebUI/API
binds `127.0.0.1:5300` and is accessed only through an SSH tunnel. LangBot's
stdio plugin runtime is process-private. Only Telegram is configured; WeChat
and every other adapter remain absent or disabled.

## Release and configuration ownership

- `/opt/notebook-agent/releases/<sha>` is immutable after promotion.
- `/opt/notebook-agent/current` is the only mutable release pointer.
- `/etc/notebook-agent/notebook-agent.env` is root-owned, mode `0600`, read by
  systemd before it changes to the service identity, and never sourced as
  shell code.
- `/var/lib/notebook-agent/langbot` and `/opt/notebook-agent/shared/langbot`
  are owned by a dedicated `notebook-langbot` service identity. The bridge
  plugin's mode-`0600` `.env` contains only its gateway secret, loopback URL,
  and Telegram bot UUID mapping; it never receives database or model secrets.
- Runtime logs and mutable object/data paths live outside releases.
- GitHub owns only the deploy host/user, pinned host key, and dedicated SSH
  private key. Application DSNs and provider credentials never enter GitHub.
- The first bootstrap creates service identities, dependency runtime, systemd
  units, environment configuration, Caddy site, restricted deploy command, and
  the initial release. Later deployments reuse those boundaries.

## Dependency isolation

The bootstrap uses the installed Docker runtime for Redis and MinIO, bound to
loopback and named exclusively for Notebook Agent. Only the `redis` and
`minio` services start, and their published ports bind to `127.0.0.1`; the
repository's PostgreSQL container is not started.
Names, volumes, networks, and credentials are Notebook-Agent-specific.

Starting the dependency containers and reloading Caddy are the only shared-host
runtime changes. Each is preceded by a snapshot and validation; discovery of a
firewall, port, or resource conflict stops deployment rather than changing an
unrelated workload.

## Database and migration contract

Long-running processes receive only the pooled, TLS Neon `DATABASE_URL`.
The direct `MIGRATION_DATABASE_URL` is supplied to the bounded migration unit,
which runs `alembic upgrade head`, `current`, and `check` before application
promotion. A failed migration stops promotion and preserves the previous
release pointer. Database data is never deleted during rollback.

## Authentication and secrets

- Web: exact origin `https://notebookai.deequoique.tech`, Secure `__Host-`
  cookies, Gmail SMTP on port 587 with STARTTLS, a new Web auth secret, and
  trusted proxy configuration restricted to loopback Caddy. Phase one keeps
  the implemented open verified-email signup behavior; each newly verified
  address receives an isolated AppUser/tenant.
- MCP: `MCP_PATH=/mcp`, per-request Bearer grants, and HTTPS path-token mode
  for the URL-only evaluator at `/mcp/c/<token>`. Query tokens remain rejected.
  A dedicated evaluator AppUser receives a labeled `full` grant with a 30-day
  expiry. Raw token output is captured once into a private handoff and never
  written to config or logs; the dedicated Caddy site discards access logs.
- Redis/MinIO: independent random production credentials generated on the
  server. Values are redacted from command and health output.
- Telegram: LangBot 4.10.6 is installed from the pinned wheel whose SHA-256 is
  `ee950fd6a687cb8c7cfe646d2b9a92cfbf09b3ddfbaf8f43ea0613905d3ffbff`.
  The repository patch redacts private-channel diagnostics, waits for the
  required bridge plugin, fails closed on bridge loss, and forces the WebUI/API
  listener to loopback. The Telegram Bot Token is entered through the private
  management UI and persists only in LangBot-owned data.

## GitHub deployment flow

The existing deterministic CI remains the prerequisite. A production deploy
job targets a protected GitHub Environment and uses `concurrency` so only one
release can run. After approval it sends the exact GitHub SHA to a server-side
allowlisted deploy entry point. The entry point rejects non-`main`/unknown
revisions, creates a fresh release, installs pinned dependencies, builds or
verifies `web/dist`, runs the migration admission, starts/restarts only
Notebook Agent units, checks loopback and public health, then atomically
updates `current`. The previous release is retained for rollback.

The deploy SSH key has no interactive shell, port forwarding, agent forwarding,
or arbitrary sudo. Its forced command can invoke only the audited Notebook
Agent deploy entry point.

## Caddy and compliance

Before changing Caddy, save a timestamped root-owned backup and record a hash of
the current config. Add only the `notebookai.deequoique.tech` site pointing to the
combined loopback listener. Run `caddy validate` before a graceful reload, then
verify the new site and confirm no unrelated Caddy route changed.

The SPA receives a small, accessible filing link visible on public pages:
`粤ICP备2026101890号-1` -> `https://beian.miit.gov.cn/`. No third-party script or
new frontend framework is introduced.

## Rollout and rollback

Rollout starts with mutation flags disabled. It brings up dependencies, runs
migrations, starts worker and the single Beat, starts the combined ASGI service,
then validates Web, Gmail, MCP read behavior, full mutation discovery, and a
bounded ingestion submission before enabling production mutations.

Rollback stops LangBot before Gateway, switches `current` to the prior retained
release, and restarts only Notebook Agent-owned units in dependency order.
LangBot data, Telegram configuration, Redis/MinIO data, and remote Neon data are
preserved. It restores the backed-up Caddy config only when the site addition
itself must be removed. Migration rollback is not automatic; incompatible
schema changes require an explicit forward-compatible plan before deployment.
