import { afterEach, describe, expect, it, vi } from "vitest";

import {
  API_ORIGINS,
  API_REQUEST_TIMEOUT_MS,
  CAPTURE_REQUEST_TIMEOUT_MS,
  requestJson,
  resolveApiOrigin,
  storedOriginAction,
} from "./api-client";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("resolveApiOrigin", () => {
  it("keeps the local API on the exact loopback HTTP endpoint", () => {
    expect(API_ORIGINS.local).toBe("http://127.0.0.1:8000");
  });

  it("selects the production origin only from the exact production permission", () => {
    expect(resolveApiOrigin(["https://www.youtube.com/*", `${API_ORIGINS.production}/*`]))
      .toBe(API_ORIGINS.production);
  });

  it("selects the local origin only from the exact local permission", () => {
    expect(resolveApiOrigin([`${API_ORIGINS.local}/*`])).toBe(API_ORIGINS.local);
  });

  it.each([
    [[]],
    [[`${API_ORIGINS.production}/*`, `${API_ORIGINS.local}/*`]],
    [["https://localhost/*"]],
    [["https://notebookai.deequoique.tech.evil.example/*"]],
  ])("fails closed for missing, ambiguous, or nearby permissions", (permissions) => {
    expect(() => resolveApiOrigin(permissions)).toThrowError("extension_api_origin_invalid");
  });
});

describe("requestJson", () => {
  it("keeps ordinary requests short while allowing bounded long-form capture admission", () => {
    expect(API_REQUEST_TIMEOUT_MS).toBe(10_000);
    expect(CAPTURE_REQUEST_TIMEOUT_MS).toBe(30_000);
  });

  it("requests the selected origin and returns JSON", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(requestJson<{ status: string }>(API_ORIGINS.local, "/api/v1/health"))
      .resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/v1/health",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("preserves safe server error codes", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ code: "extension_origin_invalid" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(requestJson(API_ORIGINS.local, "/pair")).rejects.toThrowError("extension_origin_invalid");
  });

  it("maps network failures to a stable error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("private network detail"));

    await expect(requestJson(API_ORIGINS.local, "/pair")).rejects.toThrowError("network_unavailable");
  });

  it("aborts and reports a bounded timeout", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));

    const request = requestJson(API_ORIGINS.production, "/pair", {}, 25);
    const assertion = expect(request).rejects.toThrowError("request_timeout");
    await vi.advanceTimersByTimeAsync(25);
    await assertion;
  });
});

describe("storedOriginAction", () => {
  it("keeps state already bound to the selected origin", () => {
    expect(storedOriginAction(API_ORIGINS.local, API_ORIGINS.local)).toBe("current");
  });

  it("adopts legacy state only for the original production target", () => {
    expect(storedOriginAction(undefined, API_ORIGINS.production)).toBe("adopt");
  });

  it.each([
    [undefined, API_ORIGINS.local],
    [API_ORIGINS.production, API_ORIGINS.local],
    [API_ORIGINS.local, API_ORIGINS.production],
    ["https://nearby.example", API_ORIGINS.production],
  ])("resets missing local, cross-target, and unknown state", (storedOrigin, apiOrigin) => {
    expect(storedOriginAction(storedOrigin, apiOrigin)).toBe("reset");
  });
});
