import type {
  BatchSubmitInput,
  BatchSubmitResponse,
  AcceptedResponse,
  Capabilities,
  ChallengeStatus,
  ConsumeLinkTokenInput,
  EmailChallengeInput,
  EmailVerifyInput,
  LinkTokenInput,
  LinkTokenResponse,
  LinkedResponse,
  LibraryItem,
  LibraryPageResponse,
  LoginChallenge,
  LegacyLoginChannel,
  SessionInfo,
  TranscriptPage,
  ConversationHistoryPage,
  ConversationResponse,
  ConversationTurns,
  BrowserDeviceList,
  PairingApproval,
} from "./contracts";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function cookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const entry = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : null;
}

export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (init.method && !["GET", "HEAD", "OPTIONS"].includes(init.method.toUpperCase())) {
    const csrf = cookie("__Host-kb_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const fallback = { code: "request_failed", message: "请求无法完成" };
    const payload = await response.json().catch(() => fallback) as Partial<typeof fallback>;
    const error = new ApiError(
      response.status,
      payload.code ?? fallback.code,
      payload.message ?? fallback.message,
    );
    // Login verification failures are 401s too, but they are recoverable
    // form errors.  Only an invalidated browser session tears down the
    // private query client and redirects to the login route.
    if (response.status === 401 && payload.code === "session_invalid") {
      unauthorizedHandler?.();
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function createLoginChallenge(channel: LegacyLoginChannel): Promise<LoginChallenge> {
  return requestJson("/api/v1/auth/challenges", {
    method: "POST",
    body: JSON.stringify({ target_channel: channel }),
  });
}

function browserAuthorization(browserSecret: string): HeadersInit {
  return { Authorization: `Bearer ${browserSecret}` };
}

export function getChallengeStatus(
  challengeId: string,
  browserSecret: string,
): Promise<ChallengeStatus> {
  return requestJson("/api/v1/auth/challenges/status", {
    method: "POST",
    headers: browserAuthorization(browserSecret),
    body: JSON.stringify({ public_id: challengeId }),
  });
}

export function exchangeChallenge(
  challengeId: string,
  browserSecret: string,
): Promise<SessionInfo> {
  return requestJson("/api/v1/auth/sessions", {
    method: "POST",
    headers: browserAuthorization(browserSecret),
    body: JSON.stringify({ public_id: challengeId }),
  });
}

export function requestEmailChallenge(
  email: string,
): Promise<AcceptedResponse> {
  const input: EmailChallengeInput = { email };
  return requestJson("/api/v1/auth/challenges", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function verifyEmailChallenge(
  email: string,
  code: string,
): Promise<SessionInfo> {
  const input: EmailVerifyInput = { email, code };
  return requestJson("/api/v1/auth/verify", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getSession(): Promise<SessionInfo> {
  return requestJson("/api/v1/auth/session");
}

export function logout(): Promise<void> {
  return requestJson("/api/v1/auth/session", { method: "DELETE" });
}

export function createTelegramLinkToken(): Promise<LinkTokenResponse> {
  const input: LinkTokenInput = { target_channel: "telegram" };
  return requestJson("/api/v1/link-tokens", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function consumeLinkToken(token: string): Promise<LinkedResponse> {
  const input: ConsumeLinkTokenInput = { token };
  return requestJson("/api/v1/link-tokens/consume", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function approveBrowserCompanionPairing(
  pairingId: string,
): Promise<PairingApproval> {
  return requestJson(
    `/api/v1/browser-companion/pairings/${encodeURIComponent(pairingId)}:approve`,
    { method: "POST" },
  );
}

export function listBrowserCompanionDevices(): Promise<BrowserDeviceList> {
  return requestJson("/api/v1/browser-companion/devices");
}

export function revokeBrowserCompanionDevice(deviceId: string): Promise<void> {
  return requestJson(
    `/api/v1/browser-companion/devices/${encodeURIComponent(deviceId)}`,
    { method: "DELETE" },
  );
}

export function getCapabilities(): Promise<Capabilities> {
  return requestJson("/api/v1/capabilities");
}

export function listConversations(cursor?: string | null): Promise<ConversationHistoryPage> {
  const params = new URLSearchParams({ limit: "30" });
  if (cursor) params.set("cursor", cursor);
  return requestJson(`/api/v1/conversations?${params.toString()}`);
}

export function getConversationTurns(threadId: string): Promise<ConversationTurns> {
  return requestJson(`/api/v1/conversations/${encodeURIComponent(threadId)}/turns`);
}

export function deleteConversation(threadId: string): Promise<void> {
  return requestJson(`/api/v1/conversations/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
}

export function sendConversationMessage(
  conversationId: string,
  input: { message_id: string; text: string },
): Promise<ConversationResponse> {
  return requestJson(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function resetConversation(conversationId: string): Promise<ConversationResponse> {
  return requestJson(`/api/v1/conversations/${encodeURIComponent(conversationId)}/reset`, {
    method: "POST",
  });
}

export interface LibraryQuery {
  search?: string;
  collection?: string;
  lifecycle?: string;
  include_archived?: boolean;
  sort?: "saved_desc" | "saved_asc" | "title_asc";
  page?: number;
  page_size?: number;
}

export function listLibraryItems(query: LibraryQuery): Promise<LibraryPageResponse> {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "" && value !== false) params.set(key, String(value));
  });
  const suffix = params.size ? `?${params.toString()}` : "";
  return requestJson(`/api/v1/library/items${suffix}`);
}

export function getLibraryItem(publicId: string): Promise<LibraryItem> {
  return requestJson(`/api/v1/library/items/${encodeURIComponent(publicId)}`);
}

export function getTranscript(
  publicId: string,
  cursor?: string | null,
): Promise<TranscriptPage> {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor) params.set("cursor", cursor);
  return requestJson(
    `/api/v1/library/items/${encodeURIComponent(publicId)}/transcript?${params.toString()}`,
  );
}

export function updateWhySaved(publicId: string, whySaved: string | null): Promise<LibraryItem> {
  return requestJson(`/api/v1/library/items/${encodeURIComponent(publicId)}`, {
    method: "PATCH",
    body: JSON.stringify({ why_saved: whySaved }),
  });
}

export function archiveItem(publicId: string): Promise<LibraryItem> {
  return requestJson(`/api/v1/library/items/${encodeURIComponent(publicId)}:archive`, { method: "POST" });
}

export function restoreItem(publicId: string): Promise<LibraryItem> {
  return requestJson(`/api/v1/library/items/${encodeURIComponent(publicId)}:restore`, { method: "POST" });
}

export function retryItem(publicId: string): Promise<LibraryItem> {
  return requestJson(`/api/v1/library/items/${encodeURIComponent(publicId)}:retry`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
}

export function submitVideoBatch(input: BatchSubmitInput): Promise<BatchSubmitResponse> {
  return requestJson("/api/v1/library/items:batch", {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(input),
  });
}
