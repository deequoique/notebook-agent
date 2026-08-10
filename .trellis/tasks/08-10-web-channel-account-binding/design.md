# Web 邮箱与 Telegram Bot 账户绑定：技术设计

## 1. Design Goals and Boundaries

The MVP links a verified Web email identity and a Telegram sender identity through one configured
Telegram Bot. It supports both existing directions:

1. Web creates a Telegram-targeted token; the user sends `/link <token>` to the Bot.
2. The user sends `/link web` to the Bot; Web consumes the returned token.

WeChat is deferred because the current personal OpenClaw/iLink adapter requires QR login and carries a
different operational lifecycle. The backend may retain WeChat capabilities, but the new Web page does
not expose or test them in this task.

## 2. Telegram Runtime Boundary

```text
Telegram user
  -> user-created Telegram Bot
  -> LangBot 4.10.6 Telegram adapter
  -> explicitly bound required bridge plugin
  -> loopback HMAC gateway
  -> ChannelService
  -> trusted Telegram ChannelIdentity / AppUser
```

The user creates the Bot and stores its token only in LangBot's private adapter configuration. Notebook
Agent does not receive the Bot token. The installed bridge plugin stores only its own loopback gateway
configuration and an explicit `KB_BOT_CHANNELS` mapping from the LangBot bot UUID to `telegram`.

The required bridge plugin must be initialized before the adapter starts and must call
`prevent_default()` after replying. LangBot Local Agent is not a fallback: identity, tenant resolution,
commands, conversations, and retrieval remain owned by Notebook Agent.

## 3. Existing Link Contracts

No backend schema change is required:

| Operation | Input | Output | Client behavior |
| --- | --- | --- | --- |
| `POST /api/v1/link-tokens` | `{ target_channel: "telegram" }` | `{ token }` | Display `/link <token>` for the configured Bot. |
| `POST /api/v1/link-tokens/consume` | `{ token }` | `{ linked: true }` | Clear private Web state and return to login. |

Both calls remain unsafe same-origin mutations through `requestJson()`, which attaches credentials and
the CSRF header. The browser never submits a tenant, user, bot UUID, sender ID, or channel identity ID.

The response does not include `expires_at` or public Bot metadata. The deployment's public Bot identity is
therefore an explicit frontend constant: `@notebook_agent_bot`, linked to
`https://t.me/notebook_agent_bot`. The URL never includes a link token or other sensitive parameter. The
UI uses generic single-use/short-lived copy and does not implement an expiry countdown, token-bearing deep
link, or QR code in this MVP.

## 4. Frontend Architecture

### 4.1 Route and feature boundary

- Add protected route `/account/link` beneath `ProtectedLayout`.
- Add “绑定 Telegram” to the existing account menu.
- Create `web/src/account/AccountLinkPage.tsx` and a colocated interaction test.
- Keep target channel fixed to `telegram`; do not render a misleading WeChat option.

The page owns the current generated instruction, pasted token draft, create/consume mutations, clipboard
feedback, and safe localized errors. Tokens remain only in component/mutation memory and are reset when
replaced or when the feature unmounts.

### 4.2 API and generated types

- Alias the existing link request/response schemas in `web/src/api/contracts.ts`.
- Add `createTelegramLinkToken()` and `consumeLinkToken(token)` in `web/src/api/client.ts`.
- Do not manually edit `openapi.json` or `schema.d.ts`; `check:api` confirms the existing contract remains
  current.
- Client validation trims the token and enforces only non-empty/maximum-length constraints. The server
  remains authoritative for token format, target, expiry, replay, and merge rules.

## 5. Interaction Model

### Web to Telegram

1. User explicitly requests a binding code.
2. Web calls the create endpoint with `target_channel="telegram"`.
3. UI displays the exact `/link <token>` command, a copy control, and instructions to send it in a private
   chat with `@notebook_agent_bot`, with a nearby external link that opens the Bot without putting the
   token in the URL.
4. Explicit regeneration replaces the visible code without claiming previous server-side revocation.

### Telegram to Web

1. UI links to `@notebook_agent_bot` and instructs the user to send `/link web` in that Bot's private chat.
2. User pastes the returned token into a labelled Web field.
3. Pending submission disables duplicates; safe failures retain the draft. `link_merge_busy` remains
   retryable with the same token.
4. Success clears and rotates the full private QueryClient before replace-navigation to `/login` with an
   ephemeral success flag. `LoginPage` announces success. The token is never placed in route state.

## 6. Error, Privacy, and Accessibility

- Keep `session_invalid` on the global unauthorized path.
- Map known link error codes to feature-owned actionable Chinese copy; unknown failures use the existing
  safe generic message.
- Clipboard failure leaves the command visible for manual selection and announces the fallback.
- Never place a token or Bot secret in a URL, Web Storage, console, analytics, error report, or real-value
  snapshot.
- Use semantic headings, labels, native controls, visible focus, minimum practical 44px targets, and
  `aria-live`/`role="alert"` for asynchronous feedback.
- Follow the existing mobile-first stylesheet; add no UI framework or global store.

## 7. Operational Validation and Rollback

Before browser E2E, operations configures:

- the Telegram Bot token in LangBot's private Telegram adapter settings;
- the LangBot bot UUID as `telegram` in the installed bridge plugin's private `KB_BOT_CHANNELS`;
- the required bridge ref and explicit pipeline binding;
- the same loopback `CHANNEL_GATEWAY_SECRET` on Notebook Agent and the bridge plugin.

Real acceptance runs `/start` or `/whoami`, Web -> Telegram link, Telegram -> Web link, and a shared-
library check from both entry points. Logs and screenshots used for evidence must redact tokens, sender
IDs, message text, and secrets.

Frontend rollback removes the route and menu entry without changing backend or existing identities.
Operational rollback disables the Telegram adapter while preserving the Bot token and identity rows; it
must not delete or reset tenants. WeChat, unlink/split, persistent binding status, Bot deep links, and
exact expiry metadata remain deferred.
