# Deploy Notebook Agent to production server

## Goal

Deploy Notebook Agent at `https://notebookai.deequoique.tech` on the dedicated
OVHcloud VPS `ubuntu@51.79.159.110`, while preserving every unrelated file,
service, and data set on the host.

## Background and confirmed facts

- The target is a clean Ubuntu 26.04 LTS OVHcloud VPS with 2 vCPU, about
  3.7 GiB memory, and approximately 36 GiB free disk.
- Docker 29, Docker Compose 2.40, Caddy 2.6, Python 3.14, Git, Node 24,
  Corepack, systemd, and passwordless sudo are installed.
- The host initially exposed only SSH. Notebook Agent receives dedicated
  service/deploy identities and isolated paths under `/opt`, `/etc`, and
  `/var/lib`; unrelated host content must remain untouched.
- `notebookai.deequoique.tech` has an active A record pointing to
  `51.79.159.110`.
- The production UI keeps the existing `粤ICP备2026101890号-1` filing link,
  although the Singapore-hosted OVHcloud origin does not rely on mainland
  China ICP admission.
- The user chose to deploy only an exact clean commit from `main` after the
  preceding application work was committed and integrated.
- `.github/workflows/web-auth-contract.yml` already runs deterministic backend
  tests, Alembic graph validation, frontend contract validation, lint,
  typecheck, frontend tests, and a production build on every `main` push. No
  deployment workflow exists yet.
- Production will reuse the current remote Neon PostgreSQL database. The local
  configuration has a pooled runtime `DATABASE_URL` and matching direct
  `MIGRATION_DATABASE_URL`.

## Requirements

- Phase one includes the React SPA, Python Web API, public Streamable HTTP MCP,
  local Redis, local MinIO, one Celery worker consuming `ingest,maintenance`,
  exactly one Celery Beat, the loopback Channel Gateway, and a patched LangBot
  4.10.6 core/plugin runtime for Telegram.
- Enable Telegram through the required Notebook Agent bridge plugin. Do not
  install or enable a WeChat/OpenClaw adapter in this rollout.
- Keep Channel Gateway, LangBot WebUI/API, and plugin runtime private to
  loopback. Operators access the LangBot management UI only through an SSH
  tunnel; Caddy must not expose a LangBot route or subdomain.
- Use the current email-enabled combined ASGI runtime so one loopback process
  serves the SPA, `/api/v1/*`, and `/mcp` while keeping Web cookies and MCP
  Bearer authentication isolated.
- Web login uses Gmail SMTP with `EMAIL_PROVIDER=smtp`,
  `SMTP_HOST=smtp.gmail.com`, port 587, STARTTLS, and the existing Gmail
  SMTP-compatible credential.
- Preserve the current open email-registration behavior: any syntactically
  valid address that receives and verifies its code gets a new isolated
  AppUser/tenant on first login. Do not add a production allowlist in phase one.
- Create a dedicated evaluator AppUser/tenant and a labeled `full` MCP grant
  expiring after 30 days. Support the evaluator's URL-only input through the
  HTTPS path capability `/mcp/c/<token>`; keep query-token authentication
  disabled and discard the dedicated Caddy site's access logs.
- Reuse the current remote Neon database credentials without logging their
  values. Run migrations only through the direct URL; runtime traffic uses the
  pooled URL.
- Generate independent production Redis, MinIO, Web Auth, and deployment-owned
  secrets on the server, including a dedicated Channel Gateway shared secret
  and LangBot login keys. Keep all application/provider secrets solely in
  restricted server/plugin configuration, not GitHub Actions.
- Deploy into a dedicated `/opt/notebook-agent` release layout with isolated
  configuration, data, logs, service identities, and systemd unit names.
- Bind the combined application, Redis, and MinIO to loopback or a private
  container network. Bind Gateway to `127.0.0.1:8765` and patched LangBot to
  `127.0.0.1:5300`. Expose only the selected HTTPS origin through Caddy.
- Add a separately validated `notebookai.deequoique.tech` Caddy site, back up
  the configuration before editing, and reload Caddy only after validation
  succeeds.
- Do not stop, restart, reconfigure, or delete unrelated services, containers,
  processes, files, firewall rules, certificates, or user data.
- Keep mutation features disabled until migrations, PostgreSQL, Redis, MinIO,
  worker, Beat, Web health, and MCP readiness all pass.
- Use managed services with bounded restart behavior, privacy-safe logs, and a
  rollback path that touches only Notebook Agent resources.
- Provision the first release interactively. Subsequent exact `main` commits
  deploy through GitHub Actions only after the existing CI succeeds and a
  human approves the protected `Production` Environment.
- Use a dedicated SSH deploy credential and server-side allowlisted deploy
  command, serialize deployments, build a new release before switching the
  `current` symlink, and make the deployed SHA visible in the GitHub run.
- Remove the retired repository-root Vercel health deployment, its serverless
  entrypoint, configuration, tests, and current operator documentation. Keep
  Neon as the external PostgreSQL provider for the OVHcloud runtime.

## Acceptance Criteria

- [ ] An exact clean `main` commit is recorded and deployed from an isolated
      release directory.
- [ ] Caddy exposes only the intended Notebook Agent site, and all unrelated
      files and services remain unchanged after deployment.
- [ ] Neon PostgreSQL, local Redis, and local MinIO are healthy; local
      dependency ports are not publicly reachable.
- [ ] Alembic reports exactly one repository head, the remote database is at
      that head, and `alembic check` succeeds.
- [ ] `https://notebookai.deequoique.tech/`, `/login`, `/library`, a direct SPA
      refresh, and `/api/v1/health` pass HTTPS smoke tests.
- [ ] Gmail challenge delivery and Web login succeed with Secure/HttpOnly
      session behavior and no browser credential in Web Storage.
- [ ] The page displays `粤ICP备2026101890号-1` with a link to the MIIT filing
      portal.
- [ ] A dedicated 30-day `full` grant can initialize MCP, list all ten tools
      when readiness is green, call a representative read tool, and submit a
      bounded ingestion request without exposing a fixed global user.
- [ ] Celery inspection reports a worker listening to both `ingest` and
      `maintenance`, and exactly one Beat process exists.
- [ ] Gateway health succeeds on loopback, LangBot loads the patched package,
      the required bridge plugin reaches `initialized` before adapters start,
      and only a configured Telegram adapter is enabled.
- [ ] LangBot WebUI/API is unreachable on the public interface and usable only
      through an SSH tunnel to `127.0.0.1:5300`.
- [ ] A post-deployment snapshot confirms unrelated listening ports, services,
      processes, and Caddy routes were preserved.
- [ ] Rollback restores the previous Notebook Agent release and any touched
      shared configuration without removing remote database data or unrelated
      server resources.
- [ ] GitHub Actions cannot deploy a failing CI revision, requires production
      approval, serializes deployments, and cannot administer unrelated server
      services or read application secrets.
- [ ] The live repository contains no Vercel runtime/configuration and no
      Vercel deployment is triggered by `main`.

## Out of scope

- Migrating, upgrading, reorganizing, or managing unrelated server content.
- Replacing Caddy with Nginx.
- WeChat/OpenClaw and other non-Telegram channel adapters.
- Public Redis or MinIO access.
- Changing unrelated applications, proxy settings, or firewall configuration
  except after separate approval if an unavoidable conflict is discovered.
