# Frontend delivery integration design

## Decision

Treat `web/` as a standalone deployable application package inside the current
repository. Keep the Python API and the static frontend independently runnable,
but preserve one browser-visible origin through a reverse proxy.

This is deliberately not a published UI library. The package contains routing,
authentication, product state, OpenAPI-generated types, and application pages;
extracting it as reusable components would add a false abstraction without a
second consumer.

## Existing boundary

- `web/package.json`, `web/pnpm-lock.yaml`, Vite, Vitest, ESLint, and TypeScript
  already form an isolated frontend build surface.
- The frontend calls relative `/api/v1/*` paths and sends credentials as
  same-origin requests.
- `build_web_app(..., mount_static=False)` already proves the FastAPI composition
  can omit SPA mounting in tests, but production settings and documentation do
  not expose that mode.
- Bundled production uses `WEB_STATIC_DIR=web/dist`; separate backend images
  therefore fail startup when that artifact is absent.

## Runtime layouts

### Bundled default

```text
Browser -> HTTPS proxy -> Python web-server
                          |-- /api/v1/*
                          `-- web/dist SPA
```

`WEB_SERVE_STATIC=true` remains the default and retains current behavior.

### Split services, same public origin

```text
Browser -> https://kb.example.com
           |-- /*         -> static frontend service
           `-- /api/v1/*  -> Python API service
```

The backend uses `WEB_SERVE_STATIC=false` and does not require `web/dist`.
`WEB_ORIGIN` remains `https://kb.example.com`. The proxy forwards requests and
responses without rewriting security cookies or broadening CORS. API responses
must not be CDN-cached; fingerprinted frontend assets may be immutable.

## Configuration contract

- Add `WEB_SERVE_STATIC`, parsed as a strict boolean and defaulting to `true`.
- `build_web_app` accepts an explicit `mount_static` override for tests; when the
  override is absent it follows `settings.web_serve_static`.
- `WEB_STATIC_DIR` remains required and unchanged so toggling back to bundled
  mode is deterministic.
- No `VITE_API_URL`, CORS allowlist, domain cookie, localStorage token, or
  cross-origin fallback is introduced.

## Integration order

1. Start from the clean collection branch already based on `upstream/main`.
2. Integrate committed handoffs from the active Web task and Showcase branch.
3. Resolve shared `App.tsx`, `ShowcasePage`, `VideoDetailView`, and `styles.css`
   semantically, keeping the newest verified user behavior from each branch.
4. Add the independent API/static configuration and documentation.
5. Run focused tests, then one full frontend owner lane, backend API/config
   tests, browser smoke, review, push, and PR.

## Risks and controls

- Cross-origin deployment would silently break login and mutations: explicitly
  reject/document it rather than adding permissive CORS.
- Shared stylesheet integration can regress unrelated screens: keep source
  commits separate, inspect conflicts by component, and run every major route.
- The source worktree contains unowned `uv.lock`: never stage or rewrite it.
- Root `main` contains untracked preview artifacts: integrate only in the new
  clean worktree and do not clean root WIP.
