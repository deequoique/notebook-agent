# Root cause evidence — missing Origin on MV3 status GET

Date: 2026-08-14 Asia/Singapore

## Finding

The failure is not a client-version incompatibility. The local MV3 service worker sends an Origin on the JSON `POST` that creates a pairing, so fresh `0.1.2` pairings are created and can be approved. Before exchange it performs an unauthenticated read-only `GET` for pairing status. Chrome omits the Origin header on this service-worker GET. The Web boundary currently requires an allowed Origin for every `/api/v1/browser-companion/extension/` request, including that safe GET, and returns `extension_origin_invalid` before the route runs.

The popup incorrectly maps `extension_origin_invalid` to “当前插件版本未获服务器允许”, creating a false version-mismatch diagnosis.

## Correlated evidence

- On-disk local build and hosted ZIP both contain manifest version `0.1.2`, API Origin `http://127.0.0.1:8000`, and client version `0.1.2`.
- Hosted ZIP SHA-256: `33b7ff91ce5bcbc5a5fa6560f21c2612ea97f5addce8546f9aeb9d4dbc6de068`.
- Fresh pairings `f91629c9…` and `5e45ccdc…` were created by client version `0.1.2`, approved, never consumed, and produced no grant.
- The restarted Web process logged repeated rejections with `origin=<missing>` while the user attempted to finish pairing.
- A syntactically valid arbitrary extension Origin received `200`, proving the development wildcard itself was active.
- The exchange code does not compare or reject `client_version`; the value is stored as device metadata only.

## Minimal repair boundary

- Permit a missing Origin only for the safe, read-only `GET /api/v1/browser-companion/extension/pairings/{pairing_id}` status endpoint.
- Continue requiring an exact or development-wildcard-matched Chrome extension Origin for pairing creation, verifier exchange, capture submission, and grant revocation.
- Do not add `Access-Control-Allow-Origin` when the request has no Origin; the MV3 extension host permission owns that read path.
- Change popup copy so `extension_origin_invalid` says the request source was rejected, never that the version is incompatible.
- Keep PKCE, the verifier exchange, TTL, single use, and least-privilege grant unchanged.

## Separate observation

Intermittent SQLAlchemy `OperationalError` events were observed earlier. They may require a separate reliability fix, but they do not explain the deterministic create-success/status-failure sequence proven above. They must not be conflated with this root cause.

## Repair verification

- Focused browser-companion/capture suite: 17 passed.
- Full Web suite: 112 passed, followed by lint, TypeScript, production build, and OpenAPI stale check.
- After deployment, a no-Origin GET for pairing `5e45ccdc…` returned `200 {"status":"approved"}` with request ID `c50bafb5e51544d28995906d51d3e5e9`.
- The paired-device grant remains pending user-triggered exchange verification.
