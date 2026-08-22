# Component Guidelines

> How React components are built in the Notebook Agent Web client.

---

## Overview

Components are plain function components. Pages orchestrate queries and mutations; views receive browser-safe DTOs and callbacks. Components do not infer tenant identity, ingestion state, retry eligibility, or transcript ownership.

---

## Component Structure

1. Import generated contract aliases with `import type`.
2. Define a narrow local props interface when the generated DTO is not the complete prop shape.
3. Derive display-only values during render.
4. Keep event handlers close to the relevant form or control.
5. Return semantic HTML before adding ARIA.

`VideoDetailPage` owns network state. `VideoDetailView` owns the visual layout and is tested with explicit data and callbacks.

---

## Props Conventions

- Accept public IDs and browser-safe DTOs only.
- Inject network functions only where it materially improves isolated testing, as in `LoginPage`, `LibraryPage`, and `AddVideosDialog`.
- Do not pass `user_id`, `app_user_id`, `ChannelIdentity`, request keys, raw object keys, task IDs, or ORM objects.
- Use callbacks for user actions. A presentation component must not import a database or channel concept.
- Optional data is rendered only when meaningful. Null title uses an honest pending label; null author is omitted rather than invented.

---

## Styling Patterns

- One tracked stylesheet, `src/styles.css`, defines the current small design system.
- Use CSS custom properties for palette and common surfaces.
- The mobile layout is the default; desktop changes live in min-width media queries.
- The desktop page container is capped at `1080px`.
- Do not add Tailwind, CSS-in-JS, a component framework, or runtime theme state without a separate design decision.
- Motion must have a `prefers-reduced-motion` fallback.

---

## Accessibility

- Every form field has a programmatic label.
- Icon-only buttons have an accessible name.
- Use native `<dialog>`, `<details>`, headings, lists, links, and buttons.
- Dialogs use `showModal()` so focus and the backdrop are real browser behavior.
- Async status changes use `aria-live`, `role="alert"`, or `aria-busy` as appropriate.
- Focus indicators remain visible; keyboard and touch controls are at least 44px high where practical.
- Thumbnails are decorative when the adjacent title already names the destination.

## Safe Markdown Answers

- AI answer Markdown is rendered by a feature-local presentation component,
  not with `dangerouslySetInnerHTML`.
- Keep raw HTML and optional Markdown extensions disabled. Override model-authored
  links and images to inert text; only the separate server-owned citation DTO
  may create clickable source anchors.
- Remove the deterministic appended text-source block before Markdown parsing,
  then render structured citation cards as adjacent semantic content.
- Citation cards show the server-owned title, link, and timestamp by default;
  subtitle/excerpt text remains inside a collapsed native `<details>` block and
  is expanded only by the user's keyboard or pointer action.
- Scope answer typography beneath `.chat-markdown`. Plain historical text must
  remain readable without migration.

---

## Common Mistakes

- Showing the first-empty Agent card during loading or error states.
- Treating a YouTube description as an AI summary.
- Rendering a retry button from lifecycle alone instead of `available_actions`.
- Setting the `open` attribute before calling `dialog.showModal()`, which creates a non-modal dialog.
- Inventing placeholder metadata such as “unknown author” for newly queued items.
