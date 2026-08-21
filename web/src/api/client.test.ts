import { describe, expect, it, vi } from "vitest";

import {
  ConversationStreamError,
  consumeLinkToken,
  createTelegramLinkToken,
  requestJson,
  setUnauthorizedHandler,
  streamConversationMessage,
  StreamingUnavailableError,
} from "./client";

function streamRecord(event: Record<string, unknown>): string {
  return `event: ${String(event.type)}\ndata: ${JSON.stringify(event)}\n\n`;
}

describe("same-origin API client", () => {
  it("copies the readable CSRF cookie to unsafe requests", async () => {
    document.cookie = "__Host-kb_csrf=csrf-value; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await requestJson("/api/v1/example", { method: "POST", body: "{}" });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-value");
    expect(init.credentials).toBe("same-origin");
  });

  it("clears private client state through the unauthorized hook before surfacing 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(requestJson("/api/v1/library/items")).rejects.toEqual(
      expect.objectContaining({ status: 401, code: "session_invalid" }),
    );
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("keeps recoverable email verification failures inside the login form", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "verification_failed", message: "验证码无效或已过期" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(
      requestJson("/api/v1/auth/verify", { method: "POST", body: "{}" }),
    ).rejects.toEqual(expect.objectContaining({ status: 401, code: "verification_failed" }));
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("creates a Telegram-targeted link token through the CSRF-protected API", async () => {
    document.cookie = "__Host-kb_csrf=csrf-link; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ token: "ephemeral-link-token" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(createTelegramLinkToken()).resolves.toEqual({ token: "ephemeral-link-token" });

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/link-tokens");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ target_channel: "telegram" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-link");
    expect(init.credentials).toBe("same-origin");
  });

  it("consumes a Telegram link token with the same unsafe-request contract", async () => {
    document.cookie = "__Host-kb_csrf=csrf-consume; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ linked: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(consumeLinkToken("one-time-token")).resolves.toEqual({ linked: true });

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/link-tokens/consume");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ token: "one-time-token" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-consume");
    expect(init.credentials).toBe("same-origin");
  });

  it("consumes ordered SSE events, copies CSRF, and returns the final projection", async () => {
    document.cookie = "__Host-kb_csrf=csrf-stream; Path=/; Secure";
    const response = {
      status: "ok",
      text: "最终答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response([
        streamRecord({ type: "started", request_id: "request-1", message_id: "message-1", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "activity", request_id: "request-1", message_id: "message-1", sequence: 2, activity: "retrieving" }),
        streamRecord({ type: "text_delta", request_id: "request-1", message_id: "message-1", sequence: 3, text: "最终答案" }),
        streamRecord({ type: "completed", request_id: "request-1", message_id: "message-1", sequence: 4, activity: "completed", response }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    const events: string[] = [];

    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-1", text: "问题" },
      (event) => events.push(event.type),
    )).resolves.toEqual(response);
    expect(events).toEqual(["started", "activity", "text_delta", "completed"]);
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/conversations/conversation-1/messages/stream");
    expect(new Headers(init.headers).get("Accept")).toBe("text/event-stream");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-stream");
    expect(init.credentials).toBe("same-origin");
  });

  it("ignores duplicate sequence numbers but rejects a future sequence", async () => {
    const final = {
      status: "ok",
      text: "答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-2", message_id: "message-2", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "activity", request_id: "request-2", message_id: "message-2", sequence: 1, activity: "retrieving" }),
        streamRecord({ type: "completed", request_id: "request-2", message_id: "message-2", sequence: 2, response: final }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    await expect(streamConversationMessage("conversation-1", { message_id: "message-2", text: "问题" })).resolves.toEqual(final);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-3", message_id: "message-3", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "completed", request_id: "request-3", message_id: "message-3", sequence: 3, response: final }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    await expect(streamConversationMessage("conversation-1", { message_id: "message-3", text: "问题" })).rejects.toBeInstanceOf(ConversationStreamError);
  });

  it("stops consuming records after a terminal completion in the same chunk", async () => {
    const final = {
      status: "ok",
      text: "答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    const events: string[] = [];
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-terminal", message_id: "message-terminal", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "completed", request_id: "request-terminal", message_id: "message-terminal", sequence: 2, response: final }),
        // A buffered tail must not turn the already-complete response into a
        // gap/ordering error or reach the UI as a second answer.
        streamRecord({ type: "activity", request_id: "request-terminal", message_id: "message-terminal", sequence: 3, activity: "retrieving" }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-terminal", text: "问题" },
      (event) => events.push(event.type),
    )).resolves.toEqual(final);
    expect(events).toEqual(["started", "completed"]);
  });

  it("rejects events for a different message id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        streamRecord({
          type: "started",
          request_id: "request-mismatch",
          message_id: "another-message",
          sequence: 1,
          activity: "preparing",
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    await expect(
      streamConversationMessage("conversation-1", { message_id: "message-expected", text: "问题" }),
    ).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("requires the stream to start with sequence one", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        streamRecord({
          type: "activity",
          request_id: "request-no-start",
          message_id: "message-no-start",
          sequence: 1,
          activity: "retrieving",
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    await expect(
      streamConversationMessage("conversation-1", { message_id: "message-no-start", text: "问题" }),
    ).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("rejects a text delta without text instead of passing it to the UI", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        streamRecord({
          type: "started",
          request_id: "request-invalid-delta",
          message_id: "message-invalid-delta",
          sequence: 1,
          activity: "preparing",
        })
        + streamRecord({
          type: "text_delta",
          request_id: "request-invalid-delta",
          message_id: "message-invalid-delta",
          sequence: 2,
          text: null,
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    await expect(
      streamConversationMessage("conversation-1", { message_id: "message-invalid-delta", text: "问题" }),
    ).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("surfaces explicit stream disablement for a one-time JSON fallback", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "streaming_disabled", message: "流式回答当前未启用" }), {
        status: 406,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(streamConversationMessage("conversation-1", { message_id: "message-4", text: "问题" })).rejects.toBeInstanceOf(StreamingUnavailableError);
  });
});
