# Implementation plan: AI answers with safe Markdown

## Gate 1 — Shared answer contract

- [x] Update the Composer and bounded-autonomy instructions with one restrained
      Markdown allow-list, an explicit “use only when helpful” rule, exact
      `[S<id>]` preservation, and existing URL/source/HTML prohibitions.
- [x] Add focused Agent/validation regressions showing Markdown structure does
      not change structured Composer citation selection or bounded exact-marker
      validation, and prohibited source blocks/URLs remain rejected.

## Gate 2 — Safe Web rendering

- [x] Add the minimal Markdown rendering dependency through pnpm and keep the
      lockfile synchronized.
- [x] Add a focused `MarkdownAnswer` presentation component that does not enable
      raw HTML or GFM extensions and prevents active model-authored links/images.
- [x] Render stripped assistant text through the component while leaving status
      messages and structured citation cards on their current paths.
- [x] Add scoped, responsive Markdown typography to `web/src/styles.css` using
      existing tokens.

## Gate 3 — Verification

- [x] Cover headings, paragraphs, lists, emphasis, blockquotes, inline code,
      plain-text history, inert raw HTML/link/image input, hidden appended source
      text, and still-clickable structured citations in frontend tests.
- [x] Run focused Python Agent tests and the relevant backend test set.
- [x] Run from `web/`: `corepack pnpm test`, `typecheck`, `lint`, `build`, and
      `check:api`.
- [ ] Review output in desktop and mobile layouts when an authenticated browser
      fixture is available; verify spacing, overflow, semantics, and console.
- [x] Review the final diff for citation/data-flow preservation and ensure no API
      or database artifacts changed.

## Risky files and rollback points

- `app/agent/runtime.py`: shared prompt changes affect every channel; keep the
  syntax subset restrained and do not change citation assembly.
- `web/src/chat/ChatPage.tsx`: preserve source stripping and citation-card
  rendering order.
- `web/package.json` and `web/pnpm-lock.yaml`: dependency changes must remain a
  synchronized pair.
- `web/src/styles.css`: scope every new rule beneath `.chat-markdown`.
