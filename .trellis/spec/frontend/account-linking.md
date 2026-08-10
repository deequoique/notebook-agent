# Telegram Account-Linking Contract

> Executable frontend contract for linking a Web email session to a Telegram identity.

## 1. Scope / Trigger

Apply this contract to `/account/link`, its account-menu entry, and any frontend code that creates or
consumes a channel link token. The current UI is Telegram-only. WeChat/OpenClaw is not rendered until a
separate product and runtime decision adds it.

The link token is a short-lived credential. It may be visible while the user completes the flow, but it
must not outlive the feature observer or cross the browser/server trust boundary in an unrelated field.

## 2. Signatures

Canonical browser API wrappers in `web/src/api/client.ts`:

```ts
function createTelegramLinkToken(): Promise<LinkTokenResponse>;
function consumeLinkToken(token: string): Promise<LinkedResponse>;
```

Canonical HTTP payloads remain generated from FastAPI:

```text
POST /api/v1/link-tokens
request  {"target_channel":"telegram"}
response {"token":"<opaque>"}

POST /api/v1/link-tokens/consume
request  {"token":"<opaque>"}
response {"linked":true}
```

Both requests use `requestJson()`, same-origin credentials, and `X-CSRF-Token`. Browser code never adds
tenant, user, bot UUID, Telegram sender ID, or channel identity fields.

## 3. Contracts

- `/account/link` is nested below `ProtectedLayout`; unauthenticated direct access returns to `/login`.
- Web-created tokens always target `telegram`. The UI displays `/link <token>` for a private Bot chat.
- Both binding directions display `@notebook_agent_bot` and link to exactly
  `https://t.me/notebook_agent_bot`. This public Bot URL never contains the link token, command, tenant,
  sender identity, or another sensitive query parameter.
- Telegram-created Web tokens are trimmed and checked only for non-empty/maximum length in the browser;
  the server owns token format, target, expiry, replay, and tenant merge validation.
- Token response data and mutation variables use TanStack Mutation `gcTime: 0`. Replacing or unmounting
  the observer removes them immediately instead of retaining the default MutationCache GC window.
- Tokens never enter query strings, React Router state, Web Storage, persistent query caches, logs,
  analytics, or error reports.
- Successful consume clears the old QueryClient, rotates to a fresh private client, and replace-navigates
  to `/login`. Router state may contain only `{accountLinkSuccess: true}` for a one-time notice.
- `link_merge_busy` preserves the draft so the same unconsumed token can be retried.
- Bot token and `KB_BOT_CHANNELS` are private LangBot runtime configuration and never frontend inputs.

## 4. Validation & Error Matrix

| Condition | Required frontend behavior |
| --- | --- |
| empty pasted token | Local labelled error; no request |
| token longer than generated contract maximum | Local labelled error; no request |
| `link_token_used` / `link_token_expired` | Ask the user to obtain a new `/link web` token |
| `link_channel_mismatch` | Explain that the token targets another channel |
| `link_merge_busy` | Keep the draft and allow later retry with the same token |
| `link_account_disabled` / `link_source_unbound` | Safe actionable copy without internal identity data |
| `link_merge_conflict` / `link_token_invalid` | Safe retry/new-token guidance |
| unknown error code or non-`ApiError` | Fixed generic message; never render raw provider/server detail |
| `session_invalid` | Global private-cache teardown and login redirect |
| clipboard unavailable/denied | Keep command visible, focus it, and announce manual-copy fallback |
| successful consume | Rotate private cache before showing login success notice |

## 5. Good / Base / Bad Cases

- Good: a logged-in email user generates a token, sends `/link <token>` to the configured Telegram Bot,
  and both identities reach the same private library.
- Good: a Telegram user sends `/link web`, Web consumes the token, the old session/cache is destroyed,
  and email re-login enters the merged tenant.
- Base: the user leaves `/account/link` without completing. The observer unmounts and both generated and
  pasted token mutation entries are garbage-collected immediately.
- Bad: keep the default five-minute MutationCache lifetime, put the token into route state, or navigate
  after merge without rotating the QueryClient. Each can retain a credential or previous-tenant data.

## 6. Tests Required

Frontend tests must assert:

- the create wrapper sends exactly `{target_channel: "telegram"}` with same-origin credentials and CSRF;
- the consume wrapper sends only the opaque token;
- generated and pasted token mutations disappear from MutationCache after unmount;
- pending create/consume controls block parallel duplicate submissions;
- every stable link error code has safe copy and an unknown detail is not rendered;
- `link_merge_busy` keeps the same draft for retry;
- successful consume clears/rotates the QueryClient and a late old-client mutation cannot populate the
  replacement client;
- `/account/link` rejects unauthenticated direct access and renders for an authenticated session;
- the account menu and page expose Telegram, not WeChat.
- both direction cards expose the exact public Bot URL with safe external-link semantics, and generating a
  link token never changes the Bot link or adds that token to its `href`.

Run the full frontend gates: `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm build`, and
`pnpm check:api`. Real Telegram Bot acceptance remains a redacted deployment smoke; automated fakes do
not prove private Bot/LangBot configuration.

## 7. Wrong vs Correct

### Wrong

```ts
const mutation = useMutation({ mutationFn: consumeLinkToken });
navigate("/login", { state: { token, accountLinkSuccess: true } });
```

This retains the token in MutationCache and route state, and it does not tear down previous-tenant data.

### Correct

```ts
const mutation = useMutation({
  mutationFn: consumeLinkToken,
  gcTime: 0,
  onSuccess: () => {
    oldClient.clear();
    rotateClient();
    navigate("/login", {
      replace: true,
      state: { accountLinkSuccess: true },
    });
  },
});
```

The actual implementation centralizes the cache transition through `endPrivateSession()`; the snippet
shows the required ordering and allowed route-state shape.
