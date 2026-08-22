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
  ConversationCitation,
  ConversationResponse,
  ConversationStreamEvent,
  ConversationTurns,
  BrowserDeviceList,
  PairingApproval,
} from "./contracts";

export type ConversationStreamEventHandler = (event: ConversationStreamEvent) => void;

export class StreamingUnavailableError extends Error {
  readonly code = "streaming_disabled";

  constructor(message = "流式回答当前未启用") {
    super(message);
    this.name = "StreamingUnavailableError";
  }
}

export class ConversationStreamError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly response?: ConversationResponse,
  ) {
    super(message);
    this.name = "ConversationStreamError";
  }
}

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

function streamErrorPayload(response: Response): Promise<{ code: string; message: string }> {
  return response.json().catch(() => ({
    code: "request_failed",
    message: "请求无法完成",
  })) as Promise<{ code: string; message: string }>;
}

const STREAM_EVENT_TYPES = new Set([
  "started",
  "activity",
  "section_started",
  "text_delta",
  "section_completed",
  "section_aborted",
  "completed",
  "error",
  "cancelled",
]);
const STREAM_ACTIVITY_VALUES = new Set([
  "preparing",
  "retrieving",
  "planning_answer",
  "composing",
  "completed",
  "failed",
  "cancelled",
]);
const STREAM_EVENT_KEYS = new Set([
  "type",
  "request_id",
  "message_id",
  "sequence",
  "activity",
  "text",
  "response",
  "error_code",
  "message",
  "section_id",
  "status",
  "citation_ids",
  "citations",
  "reason",
]);

function isConversationResponse(value: unknown): value is ConversationResponse {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const response = value as Partial<ConversationResponse>;
  return (
    typeof response.status === "string"
    && ["ok", "not_found", "failed"].includes(response.status)
    && typeof response.text === "string"
    && Array.isArray(response.citations)
    && Array.isArray(response.action_results)
    && (response.thread_id === undefined
      || response.thread_id === null
      || typeof response.thread_id === "string")
    && (response.error_code === undefined
      || response.error_code === null
      || typeof response.error_code === "string")
  );
}

function isStreamEvent(value: unknown): value is ConversationStreamEvent {
  if (typeof value !== "object" || value === null) return false;
  const event = value as Partial<ConversationStreamEvent>;
  return (
    Object.keys(value).every((key) => STREAM_EVENT_KEYS.has(key))
    && typeof event.request_id === "string"
    && event.request_id.length > 0
    && event.request_id.length <= 64
    && typeof event.message_id === "string"
    && event.message_id.length > 0
    && event.message_id.length <= 128
    && typeof event.sequence === "number"
    && Number.isInteger(event.sequence)
    && event.sequence >= 1
    && typeof event.type === "string"
    && STREAM_EVENT_TYPES.has(event.type)
    && (event.activity === undefined
      || event.activity === null
      || (typeof event.activity === "string" && STREAM_ACTIVITY_VALUES.has(event.activity)))
    && (event.text === undefined || event.text === null || typeof event.text === "string")
    && (event.message === undefined || event.message === null || typeof event.message === "string")
    && (event.section_id === undefined
      || event.section_id === null
      || (typeof event.section_id === "string" && event.section_id.length > 0 && event.section_id.length <= 64))
    && (event.status === undefined
      || event.status === null
      || event.status === "grounded"
      || event.status === "unsupported")
    && (event.citation_ids === undefined
      || (Array.isArray(event.citation_ids)
        && event.citation_ids.every((id) => typeof id === "number" && Number.isInteger(id) && id > 0)))
    && (event.citations === undefined
      || (Array.isArray(event.citations)
        && event.citations.every((citation) => isConversationCitation(citation))))
    && (event.reason === undefined
      || event.reason === null
      || ["provider_failure", "timeout", "cancelled"].includes(event.reason))
    && (event.response === undefined || event.response === null || isConversationResponse(event.response))
  );
}

function isConversationCitation(value: unknown): value is ConversationCitation {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const citation = value as Partial<ConversationCitation>;
  return typeof citation.title === "string"
    && typeof citation.excerpt === "string"
    && typeof citation.url === "string"
    && (citation.start_sec === undefined || citation.start_sec === null || typeof citation.start_sec === "number");
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object"
    && error !== null
    && (error as { name?: unknown }).name === "AbortError";
}

function parseSseRecord(record: string): unknown | null {
  const data: string[] = [];
  for (const line of record.replace(/\r/g, "").split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  return JSON.parse(data.join("\n")) as unknown;
}

/**
 * Consume the browser SSE contract with strict correlation/sequence checks.
 * Repeated sequence numbers are idempotently ignored; a missing or future
 * sequence fails closed so a partial answer is never silently misassembled.
 */
export async function streamConversationMessage(
  conversationId: string,
  input: { message_id: string; text: string },
  onEvent?: ConversationStreamEventHandler,
  signal?: AbortSignal,
): Promise<ConversationResponse> {
  const headers = new Headers({
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  });
  const csrf = cookie("__Host-kb_csrf");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  let response: Response;
  try {
    response = await fetch(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(input),
        credentials: "same-origin",
        signal,
      },
    );
  } catch (error) {
    if (isAbortError(error)) {
      throw new ConversationStreamError("cancelled", "请求已取消");
    }
    throw error;
  }

  if (!response.ok) {
    const payload = await streamErrorPayload(response);
    if (
      payload.code === "streaming_disabled"
      || response.status === 404
      || response.status === 406
      || response.status === 415
    ) {
      throw new StreamingUnavailableError(payload.message);
    }
    if (response.status === 401 && payload.code === "session_invalid") {
      unauthorizedHandler?.();
    }
    throw new ApiError(response.status, payload.code, payload.message);
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("text/event-stream")) {
    // An older compatible server may answer this negotiation path with the
    // ordinary projection. It is safe to accept because no second submission
    // is made and the JSON route remains the canonical fallback.
    const projection = await response.json() as unknown;
    if (!isConversationResponse(projection)) {
      throw new ConversationStreamError("stream_protocol_error", "流式响应格式无效");
    }
    return projection;
  }
  if (!response.body) throw new ConversationStreamError("stream_unavailable", "流式连接不可用");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let requestId: string | null = null;
  let lastSequence = 0;
  let completed: ConversationResponse | null = null;
  let terminal = false;
  let started = false;
  let openSectionId: string | null = null;
  let sectionLifecycleSeen = false;
  let sectionAborted = false;
  const sectionIds = new Set<string>();
  const citationIds = new Set<number>();
  const sectionStatuses = new Map<string, "grounded" | "unsupported">();
  let shouldCancelReader = true;
  let readerDone = false;

  const acceptRecord = (record: string): void => {
    // A terminal event closes the public protocol.  A proxy or server may
    // have already buffered a trailing record in the same network chunk; it
    // must not turn a successfully completed answer into a protocol error or
    // reach the UI a second time.
    if (terminal) return;
    const raw = parseSseRecord(record);
    if (raw === null) return;
    if (!isStreamEvent(raw)) throw new ConversationStreamError("stream_protocol_error", "流式响应格式无效");
    if (requestId === null) requestId = raw.request_id;
    if (raw.request_id !== requestId) throw new ConversationStreamError("stream_protocol_error", "流式响应标识不一致");
    if (raw.message_id !== input.message_id) throw new ConversationStreamError("stream_protocol_error", "流式消息标识不一致");
    if (raw.sequence <= lastSequence) return;
    if (raw.sequence !== lastSequence + 1) throw new ConversationStreamError("stream_protocol_error", "流式响应顺序无效");
    if (!started) {
      if (raw.type !== "started" || raw.sequence !== 1) {
        throw new ConversationStreamError("stream_protocol_error", "流式响应缺少开始事件");
      }
      started = true;
    } else if (raw.type === "started") {
      throw new ConversationStreamError("stream_protocol_error", "流式响应重复开始");
    }
    if (raw.type === "text_delta" && typeof raw.text !== "string") {
      throw new ConversationStreamError("stream_protocol_error", "流式文本增量无效");
    }
    const sectionId = raw.section_id ?? null;
    const ids = raw.citation_ids ?? [];
    const sources = raw.citations ?? [];
    if (raw.type === "section_started") {
      if (!sectionId || !raw.status || openSectionId !== null || sectionIds.has(sectionId)) {
        throw new ConversationStreamError("stream_protocol_error", "流式分段开始事件无效");
      }
      if (raw.status === "grounded" && ids.length === 0) {
        throw new ConversationStreamError("stream_protocol_error", "流式分段缺少来源");
      }
      if (raw.status === "unsupported" && ids.length > 0) {
        throw new ConversationStreamError("stream_protocol_error", "证据不足分段不得携带来源");
      }
      if (new Set(ids).size !== ids.length || sources.length !== ids.length) {
        throw new ConversationStreamError("stream_protocol_error", "流式分段来源无效");
      }
      if (ids.some((id) => citationIds.has(id))) {
        throw new ConversationStreamError("stream_protocol_error", "流式分段重复来源");
      }
      sectionLifecycleSeen = true;
      openSectionId = sectionId;
      sectionIds.add(sectionId);
      sectionStatuses.set(sectionId, raw.status);
      ids.forEach((id) => citationIds.add(id));
    } else if (raw.type === "text_delta") {
      if (sectionLifecycleSeen) {
        if (!sectionId || sectionId !== openSectionId) {
          throw new ConversationStreamError("stream_protocol_error", "流式文本分段标识无效");
        }
      } else if (sectionId !== null) {
        throw new ConversationStreamError("stream_protocol_error", "流式文本缺少分段开始事件");
      }
    } else if (raw.type === "section_completed") {
      if (
        !sectionLifecycleSeen
        || !sectionId
        || sectionId !== openSectionId
        || raw.status === undefined
        || raw.status === null
        || raw.status !== sectionStatuses.get(sectionId)
      ) {
        throw new ConversationStreamError("stream_protocol_error", "流式分段完成事件无效");
      }
      openSectionId = null;
    } else if (raw.type === "section_aborted") {
      if (
        !sectionLifecycleSeen
        || !sectionId
        || sectionId !== openSectionId
        || !raw.reason
      ) {
        throw new ConversationStreamError("stream_protocol_error", "流式分段中断事件无效");
      }
      openSectionId = null;
      sectionAborted = true;
    } else if (raw.type === "completed" || raw.type === "error" || raw.type === "cancelled") {
      if (openSectionId !== null) {
        throw new ConversationStreamError("stream_protocol_error", "流式响应仍有未完成分段");
      }
      if (raw.type === "completed" && sectionAborted) {
        throw new ConversationStreamError("stream_protocol_error", "中断的流式响应不得成功完成");
      }
    }
    lastSequence = raw.sequence;
    if (raw.type === "completed") {
      const completedResponse = raw.response;
      if (!isConversationResponse(completedResponse)) {
        throw new ConversationStreamError("stream_protocol_error", "流式响应缺少最终答案");
      }
      completed = completedResponse;
      terminal = true;
    } else if (raw.type === "error" || raw.type === "cancelled") {
      terminal = true;
      onEvent?.(raw);
      throw new ConversationStreamError(
        raw.error_code ?? (raw.type === "cancelled" ? "cancelled" : "request_failed"),
        raw.message ?? (raw.type === "cancelled" ? "请求已取消" : "请求无法完成"),
        raw.response ?? undefined,
      );
    }
    onEvent?.(raw);
  };

  try {
    while (!terminal) {
      const { done, value } = await reader.read();
      if (done) {
        readerDone = true;
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary >= 0) {
        const match = buffer.match(/\r?\n\r?\n/);
        if (!match || match.index === undefined) break;
        acceptRecord(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
        if (terminal) {
          buffer = "";
          break;
        }
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    }
    buffer += decoder.decode();
    if (!terminal && buffer.trim()) acceptRecord(buffer);
  } catch (error) {
    if (isAbortError(error)) {
      throw new ConversationStreamError("cancelled", "请求已取消");
    }
    throw error;
  } finally {
    if (shouldCancelReader && !readerDone) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
  if (!completed) throw new ConversationStreamError("stream_protocol_error", "流式响应未正常结束");
  shouldCancelReader = false;
  return completed;
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
