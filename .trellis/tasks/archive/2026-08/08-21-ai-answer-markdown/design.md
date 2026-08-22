# Design: AI answers with safe Markdown

## Boundaries

The shared Agent answer remains one persisted `assistant_text` string plus the
existing server-owned `citations`. Prompt changes apply to all channels. Only
the authenticated Web client gains Markdown rendering; other channels continue
printing the same text without a new parse mode or channel-specific generation.

No response DTO, database column, citation model, history format, or retrieval
contract changes. Existing plain-text rows remain valid Markdown input.

## Answer contract

Both `COMPOSER_INSTRUCTIONS` and `BOUNDED_AUTONOMY_INSTRUCTIONS` will encourage
Markdown only when it improves scanning. The allow-list is paragraphs, short
headings, ordered/unordered lists, emphasis, blockquotes, and inline code.
Simple answers stay simple rather than being forced into headings or lists.

The prompt continues to prohibit raw URLs, Markdown links, images, raw HTML,
source/reference sections, and unsupported facts. Exact `[S<segment_id>]`
markers remain plain text in the answer and must not be altered, linked, put in
code, or replaced. Composer citation selection remains the structured
`AnswerSection.citation_ids` allow-list; bounded-autonomy selection remains the
existing exact-marker validator.

## Web rendering

Add a focused `MarkdownAnswer` presentation component under `web/src/chat/`.
`ChatPage` passes it the answer after the existing deterministic appended-source
strip. The component uses `react-markdown` without `rehype-raw` and without GFM
plugins. It overrides links and images to inert text or otherwise prevents them
from becoming active external content, providing defense in depth if historical
or unexpected text contains Markdown syntax.

The component owns only presentation. It receives a string, does not inspect
citations, does not fetch, and does not use `dangerouslySetInnerHTML`.
Structured citation cards remain adjacent siblings rendered from
`turn.citations`.

## Styling and accessibility

Semantic elements produced by Markdown receive styles through a scoped
`.chat-markdown` container in the existing stylesheet. Spacing is compact
inside the assistant bubble; headings preserve hierarchy without competing
with page headings; lists keep visible markers and mobile-safe indentation;
blockquotes and inline code use existing design tokens. No runtime theme or
new CSS framework is introduced.

## Compatibility and failure behavior

- Plain text renders as a paragraph, so no history migration is needed.
- The fixed server-appended `来源：` block is removed before parsing, preserving
  the previous Web de-duplication behavior.
- Structured source cards are independent of Markdown and remain clickable.
- Action/read outcomes also pass through the safe renderer; ordinary text stays
  unchanged, while punctuation that happens to be Markdown may gain benign
  formatting.
- If Composer output validation fails, existing evidence fallback behavior is
  unchanged. Web safely renders its remaining introductory text after hiding
  the appended source block.

## Rollback

Rollback restores the two prompt constants and the plain `<p>` renderer, removes
the scoped component/styles/tests, and removes the Markdown dependency. No data
or schema rollback is required.
