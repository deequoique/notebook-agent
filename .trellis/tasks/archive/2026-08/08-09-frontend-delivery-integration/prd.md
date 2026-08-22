# Frontend delivery integration and separation

## Goal

Integrate completed Web UI work, audit the combined frontend, and make the existing web package deployable independently while preserving same-origin authentication.

## Requirements

- Integrate the completed video-library refinements, public Showcase work, and
  the other active Web task only after each source worktree reports a committed,
  verified handoff.
- Review the combined UI for behavior regressions, inaccessible interactions,
  stale placeholders, internal implementation vocabulary, responsive overflow,
  and duplicated state or styles. Fix only evidence-backed issues.
- Keep `web/` as one private standalone React application package. Do not turn
  the product into a published npm component library and do not split history
  into a new repository in this increment.
- Support two backward-compatible production layouts:
  1. bundled mode, where `web-server` serves `/api/v1/*` and `web/dist`;
  2. split-service mode, where a static frontend host serves the SPA and proxies
     `/api/v1/*` to an API-only `web-server` process.
- Both layouts must expose one browser-visible HTTPS origin. Cross-origin Web
  API access is out of scope because the current `__Host-` cookie, CSRF,
  `Origin`, and `Sec-Fetch-Site: same-origin` contracts intentionally reject it.
- Add an explicit fail-closed setting that lets the Python Web server start
  without requiring `web/dist`, while keeping bundled static serving as the
  default for existing deployments.
- Document the package boundary, build artifact, reverse-proxy contract,
  cache rules, health checks, rollback, and which process consumes each Web
  setting. Do not deploy or modify external production resources.
- Keep all work and PR branches in the user's fork. Any PR must target the
  upstream source repository for teammate review and must not be auto-merged.

## Acceptance Criteria

- [ ] Every source branch has a recorded commit, clean/owned residual WIP, and
  focused verification before integration.
- [ ] The combined branch passes frontend tests, typecheck, lint, OpenAPI stale
  check, production build, relevant Python API/config tests, and diff checks.
- [ ] Desktop and 390x844 browser smoke cover Showcase, login, library, add
  dialog, and video detail without horizontal overflow or console errors.
- [ ] Bundled mode still mounts the SPA and rejects unknown `/api/*` as JSON.
- [ ] API-only mode starts without `web/dist` and still exposes the same
  `/api/v1` contract, security headers, origin checks, cookies, and CSRF rules.
- [ ] Deployment documentation states that split services remain one public
  origin and includes concrete proxy routing and verification commands.
- [ ] The integration branch is pushed to the user's fork and a non-merged PR
  is opened for upstream review, with remaining deployment/credential gates
  stated explicitly.

## Notes

- Official Vercel documentation confirms that a monorepo directory can be a
  separate project and that external rewrites can proxy an API without changing
  the browser URL. The repository must not commit an unknown backend hostname;
  deployment-specific routing is configured only after that origin exists.
