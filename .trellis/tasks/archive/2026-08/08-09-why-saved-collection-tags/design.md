# Technical Design

## Evidence and Decision

`upstream/main@a5d244e` has no Web collection/folder contract. Its `tags`
column is populated from YouTube connector metadata, while `why_saved` is
already tenant-scoped, searchable, editable, submitted by Web/Agent/MCP, and
bounded to 500 characters in the upstream management service. Therefore the
smallest compatible design encodes one folder-like selection as a canonical
hashtag token inside `why_saved`.

## Data Grammar

- Canonical token: `#<name>`.
- Name: 1-20 Unicode letters/numbers plus `_` or `-`.
- Token boundary: beginning/whitespace before `#`, whitespace/end after name.
- Parsers return de-duplicated names in first-seen order.
- Presentation splits recognized tokens from the remaining reason copy.
- Submission joins trimmed reason and the selected canonical token with one
  space, then rejects values over 500 characters.

The parser and formatter live in a small pure TypeScript module so every UI
surface uses one definition. They do not access browser storage or network.

## UI Flow

`LibraryPage` derives collection suggestions from the currently returned item
page and passes them to `AddVideosDialog`. It also renders the discovered names
as a filter row. Selecting a filter sets the existing library `search` query to
the canonical hashtag, so the server continues to enforce tenant scope and
searches its own `why_saved` column.

`AddVideosDialog` owns the selected collection and new-name draft as local form
state. It formats the final `why_saved` immediately before the existing
`submitBatch` call. The request DTO is unchanged.

`VideoCard` and `VideoDetailView` parse `why_saved` during render. Tags use one
shared presentational component; ordinary reason text remains readable and
editable. Detail editing preserves already recognized collection tokens when
the user updates only the reason.

## Safety and Compatibility

- No production mock data or new dependency.
- No changes to cookies, CSRF, auth, public IDs, or query-cache teardown.
- No client-side tenant field.
- No reinterpretation of connector metadata tags.
- Existing backend validation remains authoritative; frontend validation only
  provides earlier feedback.

## Rollback

Removing the collection parser/components returns the UI to raw `why_saved`.
Persisted hashtag tokens remain valid ordinary text and require no data repair.
