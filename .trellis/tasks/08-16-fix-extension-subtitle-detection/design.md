# 浏览器插件字幕识别修复设计

## Diagnosis

当前扩展的字幕识别集中在 `extension/src/page-capture.ts`，但自动化只覆盖协议 hash、Kaltura Frame 评分和单个签名 VTT 成功样例。YouTube 适配器、跨 Frame 编排、原生 TextTrack、SRT/TTML、候选失败回退、无字幕与失败区分、SPA 陈旧响应和脱敏边界没有形成回归网。

现有实现还有三个结构性问题：

1. 字幕“没有找到”和“找到了描述但 fetch/parse 失败”最终都可能落为 `unavailable`，会错误进入 `needs_asr`。
2. YouTube 只读取一个全局 `ytInitialPlayerResponse`，没有验证它是否属于当前 URL，也没有覆盖当前 player API/config 响应，SPA 导航后容易使用旧数据或没有数据。
3. Kaltura 发现依赖已出现在 DOM/performance/HTML 中的资源；未加载、禁用或由播放器状态持有的字幕轨可能完全不可见。单个候选异常又被静默吞掉，测试无法证明回退与最终错误语义。

## Boundaries

```text
service worker
  -> platform router + frame coordinator
      -> injected YouTube adapter (MAIN world, top frame)
      -> injected Kaltura adapter (MAIN world, authorized frames)
          -> caption discovery
          -> trusted resource selection
          -> timed-text parsing
      -> deterministic result/error aggregation
  -> existing capture.v1 normalization and submit
```

- Service worker owns tab selection, platform routing, frame execution and final aggregation.
- Injected adapters own only page-local player inspection and page-authorized caption reads.
- Existing `protocol.ts` remains the outbound allowlist. No player object, request descriptor or resource URL crosses into the submission payload.
- Backend capture contract does not change unless tests expose a real incompatibility.

## Adapter result contract

Each injected frame returns a discriminated, structured attempt rather than using “empty cues” for every situation:

```ts
type CaptureAttempt =
  | { status: "captured"; capture: PageCapture }
  | { status: "no_caption"; capture: PageCapture }
  | { status: "not_media_frame" }
  | { status: "failed"; code: SafeCaptureError };
```

The coordinator follows these rules:

- Any valid captured result outranks every no-caption result.
- A no-caption result is accepted only if no frame discovered a caption track that failed to load/parse.
- Safe failures are aggregated by stable code only. If a track was discovered but every fetch/parse failed, return `caption_fetch_failed` (or a more specific existing safe code), not `unavailable`.
- Exceptions from one frame are tolerated when another frame returns a valid capture.
- Returned capture objects are runtime-validated before scoring so malformed page-owned objects cannot influence submission.

If changing the internal result type would create unnecessary protocol churn, the same semantics may be represented with private coordinator metadata. The public `PageCapture`/`capture.v1` shape must remain unchanged.

## YouTube discovery

Resolve the current video ID from the URL, then inspect candidate player responses in freshness order:

1. current movie-player API response when exposed;
2. page player configuration response;
3. `ytInitialPlayerResponse` fallback.

Parse string-valued response fields safely. Reject candidates whose `videoDetails.videoId` conflicts with the current URL. Prefer a current-player track hint or valid `defaultCaptionTrackIndex`; when neither is usable, select a non-ASR track before ASR. Fetch JSON3 in the page world, normalize valid events, and classify “track existed but read failed” separately from “response contains no track.” Metadata must come from the same accepted response.

## Kaltura discovery

Within each authorized frame:

1. establish entry identity from URL/player state/DOM/resources;
2. inspect native `HTMLMediaElement.textTracks`, temporarily using `hidden` only long enough to load cues and restoring prior modes;
3. inspect DOM `<track>` sources;
4. inspect current frame resource entries and serialized player/page configuration;
5. where a stable Kaltura player API is present, inspect its text-track descriptors without mutating playback or enabling a visible track;
6. fetch trusted candidates sequentially, continuing after bounded safe failures.

For Kaltura `serveWebVTT` media playlists, require a finite `#EXTM3U` playlist with `#EXT-X-ENDLIST`, fetch every trusted segment before returning any cues, apply each segment's validated `X-TIMESTAMP-MAP`, and deduplicate boundary repeats. A discovered playlist outranks native/DOM cues because browser `TextTrack.cues` may expose only the buffered window. Empty but structurally valid WebVTT segments are allowed; malformed, oversized, timed-out, live, or partially unreadable playlists fail as a whole. Standalone playlist-member resources must never be submitted as complete transcripts.

Resource trust remains HTTPS plus exact NTU host or Kaltura-owned host and a caption/timed-text shape. Candidate URLs never leave the frame result. Language comes from the selected track descriptor; HTML document language is only a last-resort `und` fallback, not proof of caption language.

Live qualification identified `ntulearnv1.ntu.edu.sg` as the actual top-level media host. Treat it as a first-class, exact NTU/Kaltura page origin in the manifest, page router, `page_url` validation and backend page-URL contract. Keep the canonical reference on the pre-existing NTULearn/NTULearnVideo host set, and do not replace either explicit set with a broad `*.ntu.edu.sg` permission. The observed page owns a top-level `<video>` with an English subtitle `TextTrack`, so it must use the same Kaltura adapter rather than a separate platform type.

## Timed-text normalization

- JSON3: finite `tStartMs` and non-negative duration; concatenate text segments; ignore metadata-only events.
- WebVTT/SRT: accept optional cue identifiers/settings, comma or dot milliseconds, and multiline text.
- TTML/DFXP: accept `begin` plus `end` or `dur`, including clock/seconds/milliseconds forms.
- Strip simple markup, normalize whitespace and entity artifacts, sort cues, and reject invalid/empty cues.
- Do not merge or deduplicate semantically distinct cues in this bug fix; preserve the server-defined content hash behavior.

## Test architecture

Use deterministic DOM/player/fetch fixtures with no live credentials:

- coordinator tests mock `chrome.scripting.executeScript` and cover platform routing, `allFrames`, partial errors, empty results and scoring;
- YouTube fixtures cover initial response, player API response, string config, manual vs ASR selection, stale response and fetch failure;
- Kaltura fixtures cover shell/frame competition, native tracks, DOM/performance/player descriptors, candidate fallback and no-caption;
- format fixtures cover JSON3, VTT, SRT and TTML/DFXP boundaries;
- security fixtures include signed KS/query URLs and untrusted hosts, asserting the normalized result/error contains none of them.

Coverage thresholds should target the core capture modules, while direct matrix assertions remain the primary quality gate. Browser smoke verifies MV3 serialization/injection behavior that jsdom-style unit tests cannot prove.

## Real-video qualification gate

The final built extension must pass a seven-video canary matrix in an actual Chromium page context:

| Platform | Minimum | Required variation | Pass threshold |
| --- | ---: | --- | ---: |
| YouTube | 5 distinct video IDs | manual + ASR captions, 2+ languages, one same-tab SPA navigation | 5/5 |
| NTULearn/Kaltura | 2 distinct entry IDs | authorized captioned media; outer media/embed entry coverage where available | 2/2 |

A real-video pass requires all of the following: the returned platform ID matches the current media; caption status is available; cues are non-empty and monotonically valid; title/metadata do not come from a stale page; opening, middle and ending cues match the player's visible captions; and no secret-bearing URL or credential appears in the result or validation artifact.

For each canary, record cue count, language, first cue start, last cue end, player duration, temporal coverage ratio, capture latency and final result. A coverage ratio below 80% triggers a mandatory three-point content check and explanation. The seven canaries are a release qualification gate in addition to—not a replacement for—the deterministic fixture suite. Authenticated Kaltura validation remains user-controlled, but it is required for task completion rather than an optional/manual follow-up.

## Compatibility and rollback

- Keep `capture.v1`, popup command protocol and backend API stable.
- Keep exact permissions; add a Kaltura host only if a captured real fixture proves it is a required player-frame origin and update the permission audit in the same change.
- The 2026-08-16 live fixture proved `https://ntulearnv1.ntu.edu.sg/*` is a required top-level media origin; add that exact host and cover it in the package permission audit and backend URL tests.
- Rollback is limited to the extension capture implementation/tests and package version. Existing ingested items and backend data remain unchanged.
