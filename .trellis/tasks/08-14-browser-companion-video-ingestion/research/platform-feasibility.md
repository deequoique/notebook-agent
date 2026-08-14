# YouTube and NTULearn/Kaltura feasibility

Research verified on 2026-08-11 and consolidated for this task on 2026-08-14.

## NTULearn platform shape

- `https://ntulearn.ntu.edu.sg/` identifies as Blackboard Learn Ultra (observed version `4000.21.0-rel.20`) and uses NTU Microsoft Entra SAML authentication.
- `https://ntulearnvideo.ntu.edu.sg/` is Kaltura MediaSpace with partner ID `117`, Kaltura Player endpoints, and the same NTU Microsoft tenant for authentication.
- A public MediaSpace sample page exposed a page-issued, short-lived player KS sufficient to query Kaltura metadata and playback information. The observed metadata included title, description, duration, thumbnail, and a play-manifest URL. That sample did not have caption assets.
- Blackboard supports user-scoped three-legged OAuth, but the application must be registered and installed on the Learn instance. Blackboard's integration documentation also states that REST, LTI, and other integrations do not automatically share authorization.
- Therefore, Blackboard REST permission is neither currently available nor sufficient by itself for private Kaltura media. Browser-context capture is the practical personal-use MVP because it can consume assets the signed-in user is already authorized to view without exporting the session.

Relevant Blackboard documentation:

- https://docs.blackboard.com/docs/blackboard/rest-apis/getting-started/3lo
- https://docs.blackboard.com/docs/developer-portal/creating-new-rest-or-lti-application
- https://docs.blackboard.com/docs/blackboard/rest-apis/getting-started/rest-integrations-and-other-integrations

## YouTube and the production-IP problem

- The existing production server path can receive YouTube HTTP 429 while the same public-caption workflow succeeds from the user's Mac/home egress. The earlier task `.trellis/tasks/08-10-youtube-ip-throttling/` implemented an intentionally temporary loopback proxy plus reverse SSH tunnel.
- YouTube metadata and subtitle acquisition are separate operations. A signed subtitle URL resolved in one context may be short-lived or context-bound, so a durable browser flow must download caption bytes in the browser and submit bounded content to Notebook Agent.
- The official YouTube Data API can provide public metadata. Official `captions.download` is not an arbitrary public-video transcript API; it requires the OAuth user to have permission to edit/manage the video.
- A browser companion can avoid the production-IP path for caption acquisition because requests originate in the user's browser/local-network session. This does not guarantee indefinite compatibility: page/player interfaces and YouTube policy can change, so the adapter needs diagnostics, fixtures, and a graceful fallback.

## Recommended common acquisition contract

Both platform adapters should return the same bounded capture envelope:

- protocol/schema version and a client-generated idempotency key;
- platform plus stable platform media ID and canonical source URL;
- title, author/channel, duration, language, description, thumbnail, and optional chapter metadata;
- normalized ordered cues with finite non-negative start/end seconds and text;
- caption source/language labels and a content hash calculated over the normalized representation;
- no cookies, request headers, SAML data, KS/player tokens, signed caption URLs, signed manifests, or audio/video URLs.

The extension may use page/player state and authenticated fetches transiently, but secret or signed third-party material is discarded before the capture envelope crosses the Notebook Agent boundary.

## Feasibility conclusion

- Caption-bearing YouTube and NTULearn/Kaltura videos are feasible for an MVP through one MV3 extension and two isolated page adapters.
- This approach directly avoids the production-server IP for YouTube caption acquisition and accesses NTULearn only with the user's existing authorization.
- Videos without browser-readable captions require a different acquisition class: audio capture/download, multipart/resumable upload, storage staging, ASR workers, progress/retry, and materially broader policy/permission analysis. It should not be treated as a small extension of caption capture.

## Implementation validation note (2026-08-14)

- A public-documentation check did not produce first-party evidence that NTULearnVideo supports the proposed `startTime` deep-link query. Search intermediaries were blocked by anti-automation controls, so their failure was not treated as evidence either way.
- The pilot therefore preserves cue timing in Notebook Agent but returns the secret-free canonical Kaltura video URL for transcript and citation links. Enabling a Kaltura time parameter remains a named live canary check against an authorized NTULearnVideo page; no unverified parameter is emitted in the meantime.
