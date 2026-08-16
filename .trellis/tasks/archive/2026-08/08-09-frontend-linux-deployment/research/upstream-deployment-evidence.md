# Upstream deployment evidence

## Verified 2026-08-09

- `upstream/main` is `6539f3b`.
- `docs/vercel-neon.md` describes a repository-root Vercel + Neon competition
  health environment. It does not deploy the full React library, LangBot,
  workers, Redis, or MinIO.
- `docs/deployment.md` describes the complete runtime as a Linux single host or
  equivalent shared-network deployment using Docker Compose, systemd, and a
  same-origin TLS reverse proxy.
- The full Web split contract is already explicit: Nginx/static `/*`, loopback
  Python `/api/v1/*`, and `WEB_SERVE_STATIC=false`.
- The repository has no CI/CD deployment workflow, server inventory, Nginx
  file, systemd Web unit, SSH host, domain, or deploy secret/variable.
- The local user environment has no `.ssh/config` and no project-local private
  deployment environment file in the integration worktree.

## Consequence

The provider-neutral Linux topology is evidenced and can be implemented now.
An actual remote rollout cannot be truthfully executed until the team supplies
the real domain/host and an authorized access path.
