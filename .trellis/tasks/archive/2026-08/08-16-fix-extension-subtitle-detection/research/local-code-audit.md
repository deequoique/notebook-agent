# Local subtitle-capture code audit

Date: 2026-08-16 (Asia/Singapore)

## Scope inspected

- `extension/src/page-capture.ts`
- `extension/src/page-capture.test.ts`
- `extension/src/protocol.ts` and its tests
- `extension/src/worker.ts`
- `extension/manifest.json` and package audit
- existing browser-companion PRD/design and local Kaltura failure research
- `.trellis/spec/backend/browser-companion-capture.md`

## Findings

1. The working tree already contains an uncommitted Kaltura repair that adds all-frame capture, native TextTrack/resource discovery, one exact Kaltura player host permission and capture scoring. This task must preserve and extend those user-owned changes rather than reset them.
2. The extension test suite currently has only three tests: one protocol content-hash test, one Kaltura result-selection test and one signed VTT success test. No YouTube adapter or `captureActivePage` test exists.
3. YouTube reads only `window.ytInitialPlayerResponse`; it does not prove the response belongs to the current URL and has no current-player/config fallback. This is fragile under YouTube SPA navigation.
4. Kaltura returns `caption.status=unavailable` after silently ignoring every failed candidate fetch. Therefore a real no-caption page and a page with an unreadable/changed caption endpoint are observationally identical to the user.
5. Kaltura discovery is primarily post-load observation (`textTracks`, `<track>`, performance entries, serialized HTML). A caption descriptor held only in current player state can be missed.
6. The injected functions are page-world code and must remain self-contained when passed through `chrome.scripting.executeScript`; refactoring must be tested against serialization/injection, not only direct function calls.
7. The current package has no extension-local lockfile and dependencies are not installed in the working tree. The first baseline `pnpm test` attempt used system Node 20/Corepack and failed before tests with `ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING`. The bundled newer Node/pnpm runtime then attempted dependency resolution; sandboxed DNS prevented it. This is a test-environment/tooling issue, not evidence that tests pass or fail.

## Existing security contract to retain

- Consume signed caption assets in the browser frame only.
- Return normalized cues and secret-free canonical metadata only.
- Never return Kaltura KS/signature URLs, cookies, auth headers or page exception text.
- Keep exact host permissions and `capture.v1` stable unless fixture evidence requires a narrowly scoped change.

## Test gap summary

The current passing claim from the previous task (3 extension tests) cannot establish page compatibility. The new task needs fixture matrices for routing/coordinator, both platform adapters, every supported timed-text format, failure classification and secret stripping, followed by a real unpacked-extension smoke because direct unit invocation does not exercise Chrome's function serialization or Frame permission behavior.

## Live host finding (2026-08-16)

- A user-authorized real media tab uses `https://ntulearnv1.ntu.edu.sg/*`, not either of the two NTU hosts currently enumerated by the extension and backend.
- The page exposes one top-level video, an English subtitle TextTrack and a metadata TextTrack. The subtitle track had not yet populated cues at inspection time; the final adapter still needs the page-world wait/resource fallbacks.
- Because `ntulearnv1.ntu.edu.sg` is absent from manifest host permissions and `captureActivePage` routing, the current extension rejects the page before the Kaltura adapter can run.
- The repair should add only this exact, live-proven origin to manifest/audit/runtime/backend host sets. A wildcard NTU permission is unnecessary and contrary to the task's least-privilege requirement.
