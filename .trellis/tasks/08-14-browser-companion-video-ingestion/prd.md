# Browser companion video ingestion

## Goal

Add a Chrome/Chromium browser companion that can save the video currently open on YouTube or NTULearn/Kaltura into Notebook Agent. The browser should acquire captions in the user's existing signed-in and local-network context, so YouTube ingestion is no longer dependent on the production server's IP reputation and private NTULearn media can be accessed without exporting the user's institutional session.

## User Value

- A single “保存当前视频到 Notebook Agent” action works across supported YouTube and NTULearn/Kaltura video pages.
- YouTube remains usable when server-side acquisition receives `youtube_rate_limited` because caption acquisition can happen from the user's browser/IP.
- NTULearn videos can be ingested through the user's already authenticated browser session without asking for or storing NTU credentials.

## Confirmed Facts

- The current backend already models normalized transcript cues and ingestion hand-off through `Cue`, `ItemMeta`, `TextResult`, `NeedsExtension`, and `NeedsASR` (`app/connectors/base.py`).
- The ingestion pipeline already maps extension-required and ASR-required outcomes to `needs_extension` and `needs_asr`, then owns validation, chunking, object storage, embeddings, and lifecycle (`app/ingest/tasks.py`).
- Production Web authentication is deliberately same-origin: raw session/CSRF credentials live in `__Host-` cookies, unsafe requests require exact Origin plus double-submit CSRF validation, and browser cookies must not authenticate other transports (`app/api/app.py`, `.trellis/spec/backend/web-browser-runtime.md`). An extension therefore needs a separately scoped, user-approved, revocable credential rather than copying Web cookies.
- The repository already has hashed, expiring, revocable Bearer-grant patterns for MCP (`app/models.py:104`, `app/mcp_grants.py`), but MCP credentials are intentionally isolated from browser routes. The extension may reuse the security pattern, not the MCP grant or scope itself.
- Existing ingestion limits default to 5,000,000 raw transcript bytes, 50,000 cues, 1,000,000 transcript characters, 5,000 segments, and 2,000,000 embedding characters (`app/config.py:225`). Worker tests prove oversized transcript content is rejected before storage or embedding (`tests/test_tasks.py:294`).
- Current product surfaces are YouTube-specific in URL normalization, connector composition, platform enumeration, transcript parsing, timestamp citations, API capabilities/CSP, and the add/detail UI (`app/ingest/submission.py:408`, `app/ingest/tasks.py:182`, `app/models.py:42`, `app/web/transcript.py:15`, `app/agent/services.py:52`, `app/api/app.py:77`, `web/src/videos/VideoDetailView.tsx:124`, `web/src/library/AddVideosDialog.tsx:117`).
- NTULearn is Blackboard Learn Ultra and uses NTU Microsoft Entra SAML. NTULearnVideo is Kaltura MediaSpace. Blackboard REST authorization does not automatically grant access to private Kaltura media.
- A Kaltura page can expose page-scoped metadata, manifests, and caption assets through short-lived playback authorization. Signed media URLs must therefore be consumed in the browser and must not be queued for later server retrieval.
- YouTube's official captions download API does not provide arbitrary public-video transcripts; it requires management permission on the video. Browser capture is a non-official acquisition path and needs explicit security and policy boundaries.

## Requirements

### R1. Unified browser action

- Provide one extension action for the current supported video page.
- Detect supported YouTube watch pages and NTULearn/Kaltura video contexts and present a clear unsupported-page state elsewhere.
- Show progress and actionable results for success, missing captions, authentication/authorization failure, rate limiting, invalid content, and upload failure.

### R2. Browser-context acquisition

- Acquire caption tracks and essential video metadata in the browser context that already has the user's YouTube or NTU/Kaltura access.
- Upload bounded, normalized transcript content/cues and metadata to Notebook Agent; do not submit a short-lived caption or playback URL for the backend to fetch later.
- Preserve enough timing information for transcript display and timestamp citations on both platforms.
- Validate source page, media identity, transcript format, cue count, duration, language, and payload size before accepting content.
- MVP captures only browser-readable caption tracks. If no usable caption track exists, create or retain the item in `needs_asr` and explain that audio transcription is not yet available through the extension.

### R3. Account connection and tenant isolation

- Pair the extension with a Notebook Agent account through an explicit, revocable flow.
- Pairing approval must occur in the authenticated Notebook Agent Web origin; the extension receives its own least-privilege capture credential and must not read or duplicate the Web session cookie.
- Opening an approval link while signed out must return to that exact, validated browser-companion approval after login. Public showcase/login entry points must download the extension (or lead to its store listing when published), not enter authenticated connection management.
- Web approval and device connection are distinct states: approval copy must not claim the device is connected before verifier exchange succeeds, and the extension must show a safe actionable reason for every pairing failure.
- Authenticate every capture submission and bind it to the correct Notebook Agent tenant/user.
- Do not upload or persist Google/NTU passwords, SAML responses, browser cookies, Kaltura KS tokens, YouTube session tokens, or other long-lived third-party session material.
- Avoid broad page access beyond the minimum host permissions and user gesture needed for supported capture.
- A wildcard Chrome-extension Origin is permitted only for a loopback-bound development Web server; production continues to require exact extension IDs.

### R4. Backend integration

- Add a platform-neutral browser-capture submission contract that reuses existing validation, deduplication, chunking, storage, embedding, retrieval, quota, and lifecycle behavior.
- Add NTULearn/Kaltura as a first-class source platform without regressing existing YouTube, Bilibili, or WeChat data.
- Keep the existing server-side `YouTubeConnector` as an optional fallback for users without the extension.
- When server-side YouTube acquisition returns `youtube_rate_limited`, guide the user toward browser capture instead of treating the current home-network tunnel as the permanent product path.
- Make transcript rendering and timestamp citations platform-aware rather than assuming YouTube JSON3 and YouTube URLs.

### R5. Compatibility and operations

- Target Manifest V3 on supported Chrome/Chromium browsers.
- Define extension/backend protocol versioning so incompatible clients fail with an actionable upgrade message.
- Keep the extension fully opt-in per user/browser: installing and pairing enables browser capture for that user; disabling/uninstalling it or disconnecting the pairing stops browser capture without changing the existing server-side save path.
- Items already saved through the extension remain ordinary Notebook Agent items after the extension is disabled, uninstalled, or disconnected.
- Log capture outcomes without logging transcript bodies, credentials, cookies, session tokens, or signed media URLs.

## Acceptance Criteria

- [ ] From a supported YouTube page with readable captions, a paired user can invoke one browser action and obtain a searchable Notebook Agent item with correct title, source URL, transcript, and timestamp links.
- [ ] The same flow works for an authorized NTULearn/Kaltura video with readable captions using the existing browser login.
- [ ] A YouTube capture succeeds when the production server cannot acquire captions because of `youtube_rate_limited`, with no backend retry of browser-only signed URLs.
- [ ] The extension and backend never transmit or persist third-party passwords, SAML responses, cookies, Kaltura KS values, YouTube session tokens, or equivalent browser session credentials.
- [ ] Capture submissions are authenticated, tenant-isolated, deduplicated, bounded by existing or explicitly defined quotas, and rejected cleanly when malformed, oversized, expired, or protocol-incompatible.
- [ ] Existing server-side YouTube ingestion remains available and existing non-extension ingestion paths continue to pass their tests.
- [ ] Transcript display, citations, item detail, and source actions render correct platform-aware links for both YouTube and NTULearn/Kaltura.
- [ ] Unsupported pages, missing permission, expired Notebook Agent pairing, unavailable captions, and upload errors produce clear recovery guidance.
- [ ] A supported video with no browser-readable caption is not treated as a failed or empty successful ingest; it reaches `needs_asr` with an actionable deferred-ASR explanation and uploads no audio/video bytes.
- [ ] Extension permissions, packaging, installation/update instructions, disable switch, and security/privacy boundaries are documented.
- [ ] A user can freely choose the extension or the existing server-side YouTube save path; disabling/uninstalling the extension or revoking its pairing stops future browser captures without hiding or damaging previously saved items.

## Out of Scope

- Automating or bypassing NTU/Google sign-in, MFA, CAPTCHA, access controls, DRM, or paywalls.
- Uploading browser cookies or institutional/Google session material to the backend.
- Treating Blackboard REST registration as a substitute for Kaltura authorization.
- Replacing the backend's chunking, embeddings, retrieval, tenant isolation, or knowledge-item lifecycle with browser-side logic.
- Publishing to a public browser extension marketplace in the first implementation unless separately approved.
- Browser audio/video capture, media upload, resumable upload, and backend ASR execution for videos without readable captions.
- Removing the existing server-side YouTube connector or the current tunnel before browser capture has been validated and rolled out.

## Constraints and Risks

- YouTube and Kaltura page/player internals may change; acquisition adapters need fixture-based tests, diagnostics, and graceful failure.
- Browser-based YouTube caption acquisition is not an official arbitrary-transcript API and may carry platform-policy and maintenance risk.
- NTULearn content access remains limited to media the signed-in user is authorized to view; the extension must not broaden access.
- Audio capture substantially increases extension permissions, bandwidth, storage, worker load, copyright exposure, and failure modes compared with caption-only capture.

## Key Product Decisions

- MVP is caption-first: it ingests browser-readable captions on YouTube and NTULearn/Kaltura and defers audio capture/upload and ASR execution. No-caption items use the existing `needs_asr` lifecycle with clear user guidance.
- Browser capture is an optional user-installed acquisition method, not a globally default-off product feature. It coexists with the existing server-side YouTube method, and user control is provided by install/pair, disconnect, disable, and uninstall actions.
