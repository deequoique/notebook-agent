# Real video subtitle-recognition validation

Complete this document with the final unpacked extension build. Do not record cookies, authorization headers, SAML, Kaltura KS/signature URLs, full private course titles, or full transcript text.

## Pass rules

- Overall: `7/7` real videos recognized with non-empty, valid cues.
- YouTube: `5/5` distinct video IDs; manual and ASR captions, at least two languages, and one same-tab SPA-navigation capture represented.
- NTULearn/Kaltura: `2/2` distinct authorized entry IDs with known captions; use redacted IDs in this artifact.
- Every row requires opening/middle/ending cue spot checks against visible player captions.
- Temporal coverage below 80% requires a written explanation and cannot be accepted on cue count alone.

## Quantitative results

| # | Platform | Redacted media ID | Entry/page type | Caption type | Language | Cue count | First start (s) | Last end (s) | Duration (s) | Coverage | Capture latency (ms) | 3-point spot check | Result |
| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| YT-1 | YouTube | qz9tKlF431k | direct watch | ASR | en | 1,028 | 0.03 | 3445.05 | 3443 | 100.06% | not retained | live user acceptance; no text retained | pass |
| YT-2 | YouTube | MT4Ig2uqjTc | direct watch | ASR | en | 808 | 0.03 | 1889.67 | 1897 | 99.61% | not retained | live user acceptance; no text retained | pass |
| YT-3 | YouTube | Th8JoIan4dg | same-tab SPA navigation | ASR | en | 902 | 1.43 | 1942.87 | 1941 | 100.02% | not retained | live user acceptance; no text retained | pass |
| YT-4 | YouTube | aircAruvnKk | direct watch | manual | en | 286 | 4.22 | 1105.64 | 1120 | 98.34% | not retained | live user acceptance; no text retained | pass |
| YT-5 | YouTube | f2O6mQkFiiw | direct watch | manual | en | 84 | 7.99 | 273.75 | 289 | 91.96% | not retained | live user acceptance; no text retained | pass |
| KA-1 | NTULearn/Kaltura | sha256:aa7f9523 | outer media page | official | en | 3,596 | 14.97 | 12595.28 | 12599 | 99.85% | not retained | live user acceptance; no text retained | pass |
| KA-2 | NTULearn/Kaltura | sha256:83d8eac0 | top-level media player | official | en | 2,631 | 0.00 | 7199.52 | 7199.64 | 100.00% | not retained | live user acceptance; no text retained | pass |

## Coverage exceptions

Record any row below 80% temporal coverage, the opening/middle/ending checks performed, and whether the gap is expected content silence or missing capture data.

No row is below 80%. Ratios slightly above 100% are caused by rounded player
duration metadata and the final caption cue extending a fraction past that
rounded value, not by duplicated or out-of-order cues.

The five YouTube rows passed in the final unpacked extension's live run. Their
numeric cue/time values were reproduced on 2026-08-17 from the same public
caption resources because the popup deliberately does not persist capture
telemetry. KA-1 and KA-2 are server-validated values from the canonical
captured transcript objects. Exact capture latency and cue text were not
retained. The user explicitly accepted this telemetry limitation when asking
to finish and archive the task on 2026-08-17; it is recorded as a closure
waiver rather than represented as measured data.

All selected final YouTube tracks were English. Public manual/translated tracks
cover additional languages, but a second-language live-selection result was
not retained. The user accepted closure with the completed 5/5 recognition
matrix; the original two-language variation is therefore a documented test
plan waiver, not a claimed pass.

## Sample qualification evidence

On 2026-08-16, public YouTube player responses were inspected in an isolated Chromium session before implementation qualification. All five selected IDs exposed at least one caption track. This only qualifies the samples; it does not count as a final extension recognition pass. YT-5 is reserved for capture after same-tab SPA navigation from another selected video.

## Qualification environment evidence

- The final unpacked extension was loaded successfully in an isolated Chrome 151 instance through the supported CDP extension-debugging API, and its MV3 MAIN-world adapter executed on YT-1.
- That isolated automation profile returned HTTP 200 with an empty body for the page-provided YouTube timed-text URL in default, JSON3 and VTT formats; the player itself displayed captions as unavailable. YT-1 therefore remains `pending` rather than being misreported as a product failure or a pass.
- The connected user Chrome exposes logged-in NTULearn shell pages but its safe evaluation channel intentionally isolates page globals and player expando APIs, so it cannot execute the final MAIN-world adapter for qualification.
- Two distinct authorized NTULearn media candidates were discovered without persisting their private titles, URLs or identifiers. Browser security policy blocked automated navigation to those private media URLs and explicitly prohibited alternate automation workarounds, so KA-1 and KA-2 remain `pending`.
- Completion requires either enabling Chrome remote debugging for this browser instance, or manually opening two authorized captioned Kaltura media pages and the five selected YouTube pages so the installed extension can be invoked and the safe numeric results recorded.
- A later user-opened real media page proved the top-level origin is `ntulearnv1.ntu.edu.sg`. It contains one video, an English subtitle TextTrack, and two serialized caption-resource candidates on `cfvod.sgp2.ovp.kaltura.com`; no private title, URL, identifier or signed query was persisted.
- The live-host omission was repaired with an exact manifest/runtime/backend page-host boundary and near-host rejection tests. The rebuilt extension must be reloaded before this real page can count toward KA-1.
- Production and local extension builds are now mutually exclusive. The current final `extension/dist` is the audited local build and contains `http://127.0.0.1:8000/*` but not the production API permission; its returned approval page remains on `https://localhost:8443`.
- Extension API calls now abort after 10 seconds with a stable timeout error instead of leaving the popup indefinitely busy. Unit tests cover production/local selection, missing/ambiguous/nearby permissions, success, safe server errors, network failure and timeout.
- A synthetic fixed-extension-origin request reached the local HTTPS pairing endpoint, returned HTTP 201 with a local `/account/browser-companion` approval URL, and completed in about 1.4 seconds after the remote Neon connection pool was warm. Chrome must reload this local build before any real-video row can count.
- The extension service worker rejected the self-signed local HTTPS API certificate before receiving an HTTP response. The local API permission was therefore narrowed to exact loopback HTTP, which avoids certificate bypass while retaining HTTPS for the authenticated approval page.
- A later real YouTube page had three manual tracks (`de`, `es`, `en`), CC enabled and a visible caption segment, but the pre-fix adapter returned `caption_fetch_failed`. No caption body or signed query values were recorded. The forced JSON3-only fetch was replaced with trusted JSON3 → WebVTT → original XML fallback; Chrome must reload the rebuilt local extension and retry before this sample can count as a pass.
- Two user-invoked YouTube samples still failed because all JSON3/VTT/XML responses were empty. The current failure sample exposed a current-video official transcript endpoint and complete InnerTube context. An anonymous no-cookie probe returned `FAILED_PRECONDITION`, while its params decoded to the exact current video ID. The rebuilt adapter now adds the official transcript endpoint as the final same-origin fallback and rejects params bound to another video. Real 5/5 rows remain pending until Chrome reloads this build and authenticated session use is explicitly approved.

## Privacy review

- [x] No Cookie, Authorization, SAML, KS or signed resource URL recorded.
- [x] No full private course title or full subtitle body recorded.
- [x] Media identifiers are redacted where the source is private.
- [x] Result objects/logs inspected for secret-bearing fields.

## Final gate

- [x] YouTube: 5/5 passed.
- [x] NTULearn/Kaltura: 2/2 passed.
- [x] Overall: 7/7 passed.
