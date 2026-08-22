# Web 邮箱与 Telegram Bot 账户绑定：实施计划

## Implementation Checklist

1. Prepare the user-created Telegram Bot integration.
   - Record the Bot creation/token handoff as an operator prerequisite; never store the real token in the
     repository or task artifacts.
   - Configure the LangBot Telegram adapter, explicit required bridge pipeline, and installed plugin
     `KB_BOT_CHANNELS={"<bot-uuid>":"telegram"}` mapping in private runtime configuration.
   - Verify required-plugin readiness, then perform a redacted `/start` or `/whoami` private-chat smoke.

2. Add generated-contract aliases and API wrappers.
   - Alias existing link input/response schemas in `web/src/api/contracts.ts`.
   - Add `createTelegramLinkToken()` and `consumeLinkToken()` in `web/src/api/client.ts`, reusing
     `requestJson()` for credentials, CSRF, safe errors, and `session_invalid` handling.
   - Extend `web/src/api/client.test.ts` for method, fixed Telegram target body, consume body, and CSRF.

3. Build the protected Telegram-linking feature.
   - Add `web/src/account/AccountLinkPage.tsx` and a colocated test.
   - Implement explicit generation/regeneration, visible `/link` instruction, clipboard feedback,
     `/link web` guidance, pasted-token validation, pending duplicate protection, and safe error copy.
   - Show a safe external link to `https://t.me/notebook_agent_bot` beside both binding directions; display
     `@notebook_agent_bot` as the destination and never place a binding code in the URL.
   - Keep token state ephemeral and do not render a WeChat option.

4. Integrate the route and account menu.
   - Register `/account/link` below `ProtectedLayout`.
   - Add “绑定 Telegram” to `AppShell` and update shell/route tests.
   - Prove direct unauthenticated access is rejected and authenticated navigation works.

5. Enforce post-merge private-data teardown.
   - Reuse/refactor `endPrivateSession()` so successful Web consumption clears the old QueryClient,
     rotates the client, and replace-navigates to `/login` with only an ephemeral success flag.
   - Render a one-time success notice in `LoginPage` without persisting it or carrying the token.
   - Add a late-mutation regression proving the absorbed tenant cannot rehydrate the new private client.

6. Add mobile-first styling and accessibility coverage.
   - Extend `web/src/styles.css` with existing variables and BEM-like names.
   - Test labels, visible alerts/live regions, pending disablement, copy fallback, focusable controls, and
     Telegram-specific instructions, including both Bot links and their exact safe destination.
   - Manually inspect 390x844 and desktop layouts, keyboard flow, focus, overflow, and console cleanliness.

7. Validate both directions end to end.
   - Web email login -> generate Telegram token -> Bot `/link <token>` -> shared-library check.
   - Bot `/link web` -> Web consume -> cache/session teardown -> email re-login -> shared-library check.
   - Redact all real tokens, Bot secrets, Telegram sender IDs, and message bodies from evidence.

8. Run frontend regressions and update project knowledge.
   - Run tests, typecheck, lint, production build, and OpenAPI stale check.
   - Add `/account/link` and the Telegram link/cache-rotation conventions to frontend specs if confirmed.
   - Keep WeChat/OpenClaw work as a separate later task rather than an incomplete acceptance item.

## Validation Commands

From `web/`:

```bash
pnpm test
pnpm typecheck
pnpm lint
pnpm build
pnpm check:api
```

Targeted frontend iteration:

```bash
pnpm test -- src/api/client.test.ts src/account/AccountLinkPage.test.tsx src/app/App.test.ts src/app/AppShell.test.tsx src/auth/LoginPage.test.tsx
```

Relevant bridge regressions from the repository root:

```bash
.venv/bin/pytest -q tests/test_langbot_bridge_plugin.py tests/test_http_gateway.py tests/test_multiuser_integration.py
```

## Review Gates

- Telegram Bot uses the existing LangBot/bridge trust boundary; no direct bot runtime is added.
- Real Bot token exists only in private LangBot adapter configuration.
- No request, component prop, cache, or route state contains tenant/user/bot UUID/sender identity IDs.
- No link token enters URL state, Web Storage, logs, analytics, or persistent cache.
- `link_merge_busy` retains a retryable draft; success clears private Web state immediately.
- Both link directions have automated UI coverage and a real Telegram smoke checklist.
- WeChat is absent from MVP UI and acceptance, not presented as partially supported.

## Rollback Points

- Telegram runtime issue: disable the LangBot Telegram adapter without deleting identity or tenant data.
- Frontend issue: remove the protected route and account-menu entry; backend link behavior remains intact.
- Consume/cache issue: disable Web token consumption until teardown is fixed; Web-generated codes can still
  be consumed by the Telegram Bot.

## Pre-Start Check

- User approves the revised Telegram-first planning summary and the explicit WeChat deferral.
- The user-created Bot token is treated as a later private deployment input, not required in planning.
- Task remains `planning` until approval; product-code edits and `task.py start` happen in a later turn.
