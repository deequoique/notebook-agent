export const API_ORIGINS = {
  production: "https://notebookai.deequoique.tech",
  local: "http://127.0.0.1:8000",
} as const;

export const API_REQUEST_TIMEOUT_MS = 10_000;
export const CAPTURE_REQUEST_TIMEOUT_MS = 30_000;

const API_HOST_PERMISSIONS = Object.values(API_ORIGINS).map((origin) => `${origin}/*`);

export function resolveApiOrigin(hostPermissions: readonly string[]): string {
  const matches = API_HOST_PERMISSIONS.filter((permission) => hostPermissions.includes(permission));
  if (matches.length !== 1) throw new Error("extension_api_origin_invalid");
  return matches[0]!.slice(0, -2);
}

export function storedOriginAction(
  storedOrigin: unknown,
  apiOrigin: string,
): "current" | "adopt" | "reset" {
  if (storedOrigin === apiOrigin) return "current";
  if (storedOrigin === undefined && apiOrigin === API_ORIGINS.production) return "adopt";
  return "reset";
}

export async function requestJson<T>(
  apiOrigin: string,
  path: string,
  init: RequestInit = {},
  timeoutMs = API_REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    let response: Response;
    try {
      response = await fetch(`${apiOrigin}${path}`, { ...init, signal: controller.signal });
    } catch {
      throw new Error(timedOut ? "request_timeout" : "network_unavailable");
    }

    let body: ({ code?: string } & T) | Record<string, never>;
    try {
      body = await response.json() as { code?: string } & T;
    } catch {
      if (timedOut) throw new Error("request_timeout");
      body = {};
    }
    if (!response.ok) throw new Error(body.code ?? "request_failed");
    return body as T;
  } finally {
    clearTimeout(timer);
  }
}
