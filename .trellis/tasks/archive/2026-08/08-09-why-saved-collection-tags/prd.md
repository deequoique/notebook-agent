# Why-saved Collection Tags

## Goal

Let Web users organize saved videos with lightweight folder-like collection
tags while preserving the existing `why_saved` API and storage model. The UI
must make the feature feel like a collection picker, but the persisted value
remains an ordinary validated hashtag inside `why_saved`.

## Product Requirements

- The Add Video dialog includes a collection section between URLs and the save
  reason.
- A user may select one existing collection, create and select one new
  collection, or explicitly choose `未归类`.
- Existing collection suggestions are derived from collection hashtags found
  in the authenticated user's currently loaded library items. They are never
  derived from YouTube metadata `tags`.
- A collection name is 1-20 characters after trimming and may contain Unicode
  letters, numbers, `_`, or `-`. Spaces, punctuation, control characters, and
  a second leading `#` are rejected with inline Chinese guidance.
- Saving with a collection appends one canonical `#name` token to the shared
  save reason. Saving without a collection sends only the ordinary reason or
  `null`.
- The final combined `why_saved` value must not exceed the upstream-compatible
  500-character limit.
- The ordinary save reason is a multiline textarea with native vertical resize
  affordance, a practical maximum height, and a visible character count. The
  Add Video dialog labels this optional utility field `备注（可选）` while the
  persisted `why_saved` contract remains unchanged.
- The URL entry starts as a compact one-row input. Enter or a multiline
  paste confirms URL drafts into individually distinguishable removable tags;
  the tag container wraps and grows with its actual contents instead of
  reserving a large empty textarea.
- Collection tags and ordinary save-reason copy are visually separated on
  library cards and video detail pages.
- The account popover keeps its native summary toggle but also closes when a
  pointer interaction occurs anywhere outside the account-menu boundary.
- The library keeps readable videos in the primary grid and moves queued,
  processing, failed, or action-required videos into a quieter lower work
  area separated by whitespace and a fine divider.
- The lower work area stays visually concise without explanatory body copy and
  shows a deliberately approximate progress indicator for reassurance rather
  than precise worker telemetry.
- The library displays discovered collections as quiet filter chips. Choosing
  one uses the existing server-side `why_saved` search by querying its exact
  hashtag; choosing `全部视频` clears that collection filter.
- While a user types in library search, the UI suggests up to six de-duplicated
  matches from the currently loaded titles, authors, collection tags, and save
  reasons. Suggestions are computed locally and never trigger a request per
  keystroke; choosing one reuses the existing search submission.
- Search suggestions close on Escape or an outside pointer interaction and can
  be reopened by clicking a still-focused input with an unchanged query.
- Video-detail heroes keep the complete source title, but long Latin or CJK
  titles use a compact responsive type scale and the desktop cover retains
  roughly half of the available hero width.
- Authenticated demo fixtures must use specific, mutually distinct video
  descriptions grounded in the source video's public metadata. Demo overrides
  remain ignored runtime data and must never become title-ID conditionals in
  production frontend code.
- The library's two visible counters describe the cards in their own regions:
  the primary count uses the currently rendered readable items and the queue
  count uses the currently rendered work items. Neither counter reuses the
  API-wide total across both lifecycle groups.
- All additions match the existing pale editorial UI, remain usable at
  390x844, expose programmatic labels, and never rely on color alone.

## Compatibility and Non-goals

- No database model, migration, OpenAPI schema, API route, auth behavior, or
  tenant selection changes.
- Do not overwrite or reinterpret `ContentItem.tags`; those remain connector
  metadata supplied by YouTube.
- No nested collections, collection rename/delete workflow, drag-and-drop,
  multi-select folder assignment, or cross-page collection aggregation in this
  increment.
- Existing `why_saved` values without recognized tags remain unchanged and
  render as ordinary save reasons.

## Acceptance Criteria

- [x] Unit tests prove collection parsing, stable de-duplication, name limits,
  combined 500-character validation, and reason/tag separation.
- [x] Add Video tests prove existing-tag selection, new-tag validation,
  explicit unclassified selection, resizable reason textarea, and unchanged
  `{urls, why_saved}` submission shape.
- [x] Library tests prove discovered collection chips and existing-search
  filtering without a new API request field.
- [x] Card and detail tests prove tags render separately from reason copy and
  ordinary hashtags outside the supported token grammar are not misclassified.
- [x] Targeted Vitest, TypeScript, ESLint, production build, and diff checks
  pass.
- [x] Browser smoke proves the dialog and tag controls work at desktop and
  390x844 without horizontal overflow.
- [x] URL-entry tests and browser smoke prove compact initial height, per-link
  tags, individual removal, wrapped growth, and unchanged batch submission.
- [x] Account-shell tests and browser smoke prove an outside click closes the
  account popover without changing logout behavior.
- [x] Library tests and browser smoke prove readable/work-item grouping, the
  absence of redundant explanatory copy, and the approximate progress indicator.
- [x] Search tests and browser smoke prove local title/author/collection/reason
  suggestions, no per-keystroke request, reliable suggestion clicks, outside
  dismissal, reopening, and narrow-screen containment.
- [x] Detail tests and browser smoke prove long titles remain complete, select
  compact typography, keep the cover visually dominant, and avoid desktop or
  mobile horizontal overflow without shrinking ordinary titles unnecessarily.
- [x] Source-metadata checks and browser smoke prove the five non-primary demo
  videos have distinct, detailed, placeholder-free descriptions while the
  production Connector-to-ContentItem description path remains unchanged.
- [x] Library tests and browser smoke prove each region count matches its own
  rendered cards and follows collection filtering instead of showing a fixed
  or API-wide placeholder total.
