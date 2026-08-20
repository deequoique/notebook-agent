# Bilibili Connector Contract

## 1. Scope / Trigger

Apply this contract when changing Bilibili URL admission, yt-dlp metadata or
subtitle ingestion, SRT storage/reading, Bilibili citations, or public Web
capabilities. Browser-companion page capture remains a separate follow-up and
must not be implied by server URL support.

## 2. URL Contract

- Accept only credential-free HTTPS `bilibili.com`/`www.bilibili.com` ordinary
  video paths with a strict 12-character BV ID or numeric av ID.
- Strip tracking queries and fragments from the canonical URL. Reject fragments,
  explicit ports, short links, non-video paths, malformed IDs, and `p` values
  other than the first part. Base multipart URLs intentionally select part 1
  through yt-dlp `--no-playlist`; do not silently expand a collection.
- URL preflight is local and must never make a provider request.

## 3. Connector Contract

- Use the declared yt-dlp runtime with `--ignore-config`, `--no-playlist`,
  `--skip-download`, a socket timeout, and a process wall-clock timeout.
- Never supply, persist, log, or enqueue Bilibili cookies, `SESSDATA`, WBI
  signatures, media URLs, subtitle URLs, response bodies, or raw stderr.
- Map public metadata to `ItemMeta`; accept cover images only from `hdslb.com`
  subdomains, upgrade them to HTTPS, and remove query/fragment data.
- Ignore the `danmaku` XML track. Prefer official SRT before `ai-*` automatic
  SRT. Consume only yt-dlp-projected inline `data`; a URL-only track requires
  browser capture rather than adding a broad server fetch allowlist.
- Parse bounded UTF-8 SRT into finite, ordered cues. Store it as raw format
  `srt`, object suffix `.srt`, and content type `application/x-subrip`.
- No visible subtitle returns `NeedsASR`. An explicit yt-dlp login-only warning
  returns `NeedsExtension`. Because upstream/provider behavior can omit that
  discriminator, real canaries must record the observed state without claiming
  that every login-only video is distinguishable from a no-caption video.
- Stable transient classifications are `bilibili_rate_limited`,
  `bilibili_fetch_timeout`, and `bilibili_fetch_failed`; worker retry surfaces
  continue to redact them according to the shared ingestion contract.

## 4. Required Tests

- URL matrix: BV, av, tracking queries, host confusion, credentials, ports,
  fragments, short links, malformed IDs, and multipart `p` values.
- Metadata mapping, cover allowlist, timeout, rate-limit/risk-control
  classification, stderr redaction, and absence of cookie arguments.
- Official vs automatic SRT selection, danmaku exclusion, login-only/no-caption
  states, size limits, malformed/unordered SRT, object key/content type, Web
  transcript projection, and Bilibili timestamp citations.
- API capability/OpenAPI, Web add-dialog copy, platform label, TypeScript,
  lint, frontend tests, full pytest, and read-only real metadata canaries.
