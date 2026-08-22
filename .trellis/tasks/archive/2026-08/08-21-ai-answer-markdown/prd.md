# AI answers with safe Markdown

## Goal

Make AI knowledge answers easier to scan by encouraging restrained Markdown
structure and rendering it safely in the authenticated Web conversation UI,
without weakening citation validation or breaking the existing structured
source cards.

## Background

- The Composer currently returns structured `AnswerDraft.sections`; the server
  renders their text and validated citation IDs into `assistant_text`.
- The bounded-autonomy path validates exact `[S<positive integer>]` markers
  against citations observed in the current run.
- Web source cards come from the separate `citations` response field, not by
  parsing `assistant_text`, so Markdown does not need to carry source URLs.
- Web currently renders the answer as a plain React `<p>` and has no Markdown
  dependency.
- Telegram, WeChat, CLI, MCP, history previews, and Web all consume the same
  persisted `assistant_text`; the repository does not apply a channel-specific
  Markdown parse mode before returning channel answers.

## Requirements

1. Encourage concise, restrained Markdown only when it improves readability:
   paragraphs, short headings, ordered/unordered lists, emphasis, blockquotes,
   and inline code.
2. Preserve exact `[S<segment_id>]` markers and the existing current-run
   citation validation and maximum-source rules.
3. Continue prohibiting model-authored URLs, source/reference sections, images,
   and raw HTML.
4. Render supported Markdown safely in Web answers without executing raw HTML
   or permitting unsafe link protocols.
5. Strip the server-appended text source block before Markdown rendering and
   continue rendering structured citation cards below the answer.
6. Preserve plain-text historical answers, answer status UI, action results,
   conversation history, and the API/database schema.
7. Keep Markdown styling readable inside the existing mobile-first chat bubble
   and consistent with the current paper-and-ink design.
8. Use the same lightweight Markdown answer contract across Web, Telegram,
   WeChat, CLI, and MCP. Channels without Markdown rendering may display the
   small amount of literal punctuation; do not add channel-specific answer
   generation branches.

## Acceptance Criteria

- [ ] Composer answers may use the supported lightweight Markdown constructs,
      while simple answers remain plain paragraphs rather than forced templates.
- [ ] The bounded-autonomy answer prompt follows the same formatting contract
      and retains exact `[S…]` markers.
- [ ] Web renders paragraphs, headings, lists, emphasis, blockquotes, and inline
      code semantically and accessibly.
- [ ] Raw HTML is displayed inertly or omitted and cannot create executable DOM;
      model-authored links/images are not rendered as active external content.
- [ ] Existing plain-text history remains readable without migration.
- [ ] The server-appended text source list remains hidden on Web, while title,
      excerpt, timestamp, and link in each structured citation card still work.
- [ ] Citation extraction/selection, no-evidence behavior, action responses, API
      schemas, and persistence contracts remain unchanged.
- [ ] Focused backend and frontend regression tests plus frontend lint,
      type-check, build, and API-staleness checks pass.

## Out of Scope

- Converting `[S…]` markers into clickable inline citation bubbles.
- Letting the model author URLs, Markdown links, images, or source lists.
- Tables, task lists, fenced code blocks, syntax highlighting, math, diagrams,
  or arbitrary GitHub-Flavored Markdown extensions.
- Database or public API schema changes.
- Channel-specific Markdown parse modes or transformations outside Web.
