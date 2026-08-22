# Type Safety

> How Python API schemas become browser TypeScript types.

---

## Overview

TypeScript runs in strict mode. FastAPI Pydantic response and request models are the canonical browser contract. `openapi-typescript` generates the tracked `schema.d.ts`; frontend code imports small aliases from `contracts.ts`.

---

## Type Organization

- `app/api/*_schemas.py`: canonical request and response DTOs.
- `scripts/export_web_openapi.py`: deterministic, side-effect-free OpenAPI export.
- `web/src/api/openapi.json`: tracked generated schema.
- `web/src/api/schema.d.ts`: tracked generated TypeScript declarations.
- `web/src/api/contracts.ts`: semantic aliases such as `LibraryItem`, `TranscriptPage`, and `LoginChannel`.
- Component-only prop interfaces remain next to the component.

Never duplicate lifecycle, login-channel, batch-status, or transcript response unions by hand.

Conversation SSE events use the generated `ConversationStreamEvent` contract.
The server omits event-specific null fields (`exclude_none`), so nullable
fields such as `section_id`, `status`, and `reason` are optional in generated
TypeScript as well as nullable. Use runtime lifecycle narrowing for their
event-specific requirements; do not make fixtures satisfy a broader global
required shape or bypass the contract with a cast.

The exporter must instantiate the same email-enabled canonical route
composition used in production, with inert injected services where external
providers would otherwise be constructed. A schema generated from a legacy or
partial app is stale even when its generated files are internally consistent.

---

## Validation

The server is the runtime validation boundary. Pydantic uses strict extra-field rejection for browser requests and emits fixed safe validation errors. The client does not maintain a second handwritten Zod response schema.

Client-side checks may improve form feedback, such as the 1–10 URL limit, but they do not replace server validation.

---

## Common Patterns

```ts
import type { components } from "./schema";

type Schemas = components["schemas"];
export type LibraryItem = Schemas["LibraryItemResponse"];
export type LibraryLifecycle = LibraryItem["lifecycle"];
```

- Use `import type` for erased imports.
- Narrow unknown errors by behavior rather than using `any`.
- Use generated nullable fields exactly as returned by the API.
- Treat transcript cursors and public IDs as opaque strings.
- Regenerate `openapi.json` and `schema.d.ts` together and require
  `pnpm check:api` in CI.

---

## Forbidden Patterns

- Editing `schema.d.ts` directly.
- Casting API responses to unrelated interfaces.
- Adding `any` to bypass a contract mismatch.
- Declaring a second handwritten `LibraryItem` or lifecycle union.
- Using ORM/model types in React.
- Assuming optional summary data exists when the schema says null is valid.
