# Technical design

## Evidence-backed topology

The upstream repository documents two different environments:

1. the repository-root Vercel + Neon competition health deployment; and
2. the supported full Linux single-host runtime using Docker Compose,
   systemd, LangBot, workers, and a local TLS reverse proxy.

The second topology is the only repository-backed match for the user's report
of a domestic server. No concrete SSH host, domain, deployment workflow, or
repository secret is currently discoverable, so the implementation must stay
provider-neutral until an authorized target is supplied.

## Runtime boundary

```text
Browser https://<domain>
  |-- /*          -> Nginx -> immutable files in web/dist
  `-- /api/v1/*   -> Nginx -> 127.0.0.1:8000 Web API

127.0.0.1:8765    -> channel gateway, never proxied publicly
```

The Python service runs with `WEB_SERVE_STATIC=false`. Nginx preserves Host and
forwarded scheme, and the application trusts only the local proxy address.
Browser cookies remain Secure, host-only, SameSite=Strict `__Host-` cookies.

## Static delivery

- `/assets/*`: long cache lifetime; filenames are content-hashed by Vite.
- `/index.html` and SPA fallback: no-store so a rollback or new release becomes
  visible without waiting for a stale HTML shell.
- `/api/v1/*`: no caching and no SPA fallback.
- Static responses reproduce the CSP, anti-framing, MIME-sniffing, referrer,
  permissions, and HSTS boundary otherwise supplied by FastAPI bundled mode.
- Access logging is disabled by the template until the operator supplies a
  query-free, header-free sanitized format.

## Process supervision

`notebook-agent-web.service` runs only the API process. It uses the same private
environment file as the rest of the application, forces production logging and
API-only static mode, binds loopback, and uses systemd hardening consistent with
the existing gateway example.

Nginx is responsible for TLS and static files. Certificate acquisition remains
operator-owned because the actual domain and provider are unknown.

## Schema convergence

Upstream has published `f6a7b8c9d0e1` for ingestion completion events. The Web
branch previously used that same ID for a no-op branch merge. Keep the upstream
migration unchanged, replace the Web merge file with a new revision whose
parents are the Web migration branch and upstream `f6`, and update every current
head reference. Never rewrite `d3`, `e5`, or upstream `f6`.

## Rollout and rollback

1. Disable new save/retry admissions and stop Web writes as documented.
2. Back up PostgreSQL and object storage.
3. Upgrade code and the unique migration head.
4. Install frozen Python/frontend dependencies and build `web/dist`.
5. Validate Nginx/systemd configuration, then restart the API service and
   reload Nginx.
6. Smoke health, capabilities, SPA refresh, login, library, and detail routes.

Rollback switches Nginx back to the previous static release and restarts the
previous API commit. Database downgrade remains separately gated and is never
performed automatically against production data.
