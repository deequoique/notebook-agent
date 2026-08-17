# Implementation Plan

## Gate 0 - Isolation and contract

- [x] Fetch and inspect `upstream/main`; confirm no Web collection/folder API.
- [x] Confirm connector `tags` are YouTube metadata and must remain unchanged.
- [x] Create `codex/why-saved-collections` worktree from Web MVP HEAD to avoid
  the Showcase and chapter-style WIP in `web-mvp-final`.
- [x] Record the `why_saved` hashtag grammar, 500-character limit, and non-goals.

## Gate 1 - Pure tag behavior

- [x] RED: parsing, de-duplication, validation, reason separation, formatting,
  and combined-length tests.
- [x] GREEN: implement the pure collection-tag module.

## Gate 2 - Add dialog

- [x] RED: existing tag selection, create/select validation, unclassified
  selection, textarea resize affordance, and unchanged request DTO tests.
- [x] GREEN: implement picker composition and add-dialog behavior.

## Gate 3 - Existing library/detail UI

- [x] RED: library filter chips, card tag/reason separation, and detail tag
  display plus reason-edit preservation.
- [x] GREEN: implement shared tag presentation and integrate each surface.

## Gate 4 - Verification and integration handoff

- [x] Targeted tests after each RED/GREEN cycle.
- [x] Full frontend test, typecheck, lint, build, and API stale check.
- [x] Desktop and 390x844 browser smoke in an isolated preview port.
- [x] Review for accessibility, mobile overflow, privacy, and simpler designs.
- [x] Prepare a clean commit/patch for integration into
  `codex/web-video-library-mvp`; do not merge the PR automatically.

## Gate 5 - Compact URL tags follow-up

- [x] RED: compact one-row URL input and per-link removable tag behavior.
- [x] GREEN: split URL draft state from confirmed URLs while preserving the
  existing `{urls, why_saved}` submission contract.
- [x] Verify multiline paste, link wrapping/growth, desktop/mobile overflow,
  full frontend checks, and the refreshed isolated preview.

## Gate 6 - Account-menu click-away follow-up

- [x] RED: opening the native details menu and clicking a control outside it
  leaves the old implementation incorrectly expanded.
- [x] GREEN: close only when document pointerdown falls outside the menu ref.
- [x] Run the shell regression test, full frontend checks, rebuild 5175, and
  verify outside/inside clicks in the browser before committing.

## Gate 7 - Processing-area hierarchy follow-up

- [x] RED: prove readable videos and work items render in separate regions,
  with explanatory copy and an approximate progress value.
- [x] GREEN: partition lifecycle states and render a restrained lower queue
  section without changing API or polling contracts.
- [x] Verify desktop/mobile layout, progress semantics, full frontend checks,
  and the refreshed isolated preview before committing.

## Gate 8 - Remove queue explanation follow-up

- [x] RED: prove the selected queue explanation is absent while grouping and
  progress semantics remain available.
- [x] GREEN: remove the copy node and its dedicated style, retaining a 1rem
  structural gap between the queue heading and cards.
- [x] Re-run the full frontend checks, rebuild 5175, and confirm the copy is
  absent with no horizontal overflow.

## Gate 9 - Local search suggestions follow-up

- [x] RED: prove a typed loaded-library keyword renders no matching suggestion
  list in the previous implementation.
- [x] GREEN: collect at most six local, de-duplicated title, author, collection,
  and save-reason matches without issuing a request per keystroke.
- [x] Lock the two browser-discovered pointer regressions: suggestion mouse-down
  must survive input blur, and outside dismissal must still allow a click on the
  focused input to reopen unchanged suggestions.
- [x] Run all frontend checks and desktop plus 391x844 browser smoke before the
  local-only commit.

## Gate 10 - Long detail-title balance follow-up

- [x] RED: render the reported long English title and prove the previous hero
  did not opt into compact title typography.
- [x] GREEN: keep the full title, classify visually long Latin/CJK strings, use
  a compact responsive scale, and reserve half of the desktop hero for media.
- [x] Verify the reported detail, a normal short-title detail, the 391x844
  layout, full frontend checks, and the refreshed 5175 preview.

## Gate 11 - Add-dialog note label follow-up

- [x] RED: update the dialog contract to `备注（可选）` and prove the previous
  label no longer satisfies it.
- [x] GREEN: replace only the visible label while preserving the `why-saved`
  field name, request shape, validation, and storage behavior.
- [x] Run all frontend checks, rebuild 5175, and confirm the open dialog copy in
  the authenticated browser fixture.

## Gate 12 - Personalized demo descriptions follow-up

- [x] Trace the repeated sentence to the ignored authenticated fixture rather
  than the production ingestion path.
- [x] Verify the five source URLs through `yt-dlp` and confirm production already
  maps connector metadata description into `ContentItem.description`.
- [x] RED/GREEN an ignored runtime check requiring five specific, unique,
  placeholder-free descriptions; keep demo overrides outside version control.
- [x] Restart only the owned 5175 fixture and verify all five detail APIs,
  desktop detail pages, and the 391x844 creative-confidence page.

## Gate 13 - Region count follow-up

- [x] RED: prove the primary region cannot be queried by a distinct count label
  and still displays the API-wide total instead of its rendered card count.
- [x] GREEN: derive the primary and queue labels from `readableItems.length`
  and `workItems.length` after lifecycle grouping.
- [x] Run all frontend checks and verify default, collection-filtered, and
  narrow browser states against the actual cards in each region.

## Validation Commands

```powershell
corepack pnpm vitest run src/library/collections.test.ts
corepack pnpm test
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm build
corepack pnpm check:api
git diff --check
```
