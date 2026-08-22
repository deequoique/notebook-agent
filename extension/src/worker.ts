import { captureActivePage } from "./page-capture.js";
import {
  CAPTURE_REQUEST_TIMEOUT_MS,
  requestJson,
  resolveApiOrigin,
  storedOriginAction,
} from "./api-client.js";
import { captureRequest, CLIENT_VERSION, sha256Hex } from "./protocol.js";

const API_ORIGIN = resolveApiOrigin(chrome.runtime.getManifest().host_permissions ?? []);
const STORAGE_KEYS = ["apiOrigin", "accessToken", "deviceId", "pairingId", "pairingVerifier", "pendingCaptureRef", "pendingCaptureKey"];

type Command = { type: "STATUS" | "START_PAIRING" | "FINISH_PAIRING" | "CAPTURE" | "DISCONNECT" };

function base64Url(bytes: Uint8Array): string {
  let raw = "";
  bytes.forEach((byte) => { raw += String.fromCharCode(byte); });
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function status() {
  const state = await storageState();
  return { paired: typeof state.accessToken === "string", pairing: typeof state.pairingId === "string" };
}

async function storageState(): Promise<Record<string, unknown>> {
  const state = await chrome.storage.local.get(STORAGE_KEYS);
  const action = storedOriginAction(state.apiOrigin, API_ORIGIN);
  if (action === "current") return state;
  if (action === "adopt") {
    await chrome.storage.local.set({ apiOrigin: API_ORIGIN });
    return { ...state, apiOrigin: API_ORIGIN };
  }
  await chrome.storage.local.remove(STORAGE_KEYS);
  await chrome.storage.local.set({ apiOrigin: API_ORIGIN });
  return { apiOrigin: API_ORIGIN };
}

async function startPairing() {
  await storageState();
  const verifierBytes = crypto.getRandomValues(new Uint8Array(48));
  const verifier = base64Url(verifierBytes);
  const challenge = base64Url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const pairing = await requestJson<{ pairing_id: string; approval_url: string }>(API_ORIGIN, "/api/v1/browser-companion/extension/pairings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ challenge, client_label: "Chrome / Chromium", client_version: CLIENT_VERSION }),
  });
  await chrome.storage.local.set({ apiOrigin: API_ORIGIN, pairingId: pairing.pairing_id, pairingVerifier: verifier });
  await chrome.tabs.create({ url: pairing.approval_url });
  return { awaitingApproval: true };
}

async function finishPairing() {
  const state = await storageState();
  if (typeof state.pairingId !== "string" || typeof state.pairingVerifier !== "string") throw new Error("pairing_missing");
  const pairingStatus = await requestJson<{ status: string }>(API_ORIGIN, `/api/v1/browser-companion/extension/pairings/${encodeURIComponent(state.pairingId)}`);
  if (pairingStatus.status !== "approved") throw new Error(`pairing_${pairingStatus.status}`);
  const grant = await requestJson<{ access_token: string; device_id: string }>(API_ORIGIN, `/api/v1/browser-companion/extension/pairings/${encodeURIComponent(state.pairingId)}:exchange`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verifier: state.pairingVerifier }),
  });
  await chrome.storage.local.set({ accessToken: grant.access_token, deviceId: grant.device_id });
  await chrome.storage.local.remove(["pairingId", "pairingVerifier"]);
  return { paired: true };
}

async function capture() {
  const state = await storageState();
  if (typeof state.accessToken !== "string") throw new Error("pairing_required");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) throw new Error("active_tab_missing");
  const page = await captureActivePage(tab.id, tab.url);
  const payload = await captureRequest(page);
  const captureRef = await sha256Hex(`${page.platform}:${page.platform_id}:${payload.content_hash}`);
  const idempotency = state.pendingCaptureRef === captureRef && typeof state.pendingCaptureKey === "string"
    ? state.pendingCaptureKey
    : crypto.randomUUID();
  await chrome.storage.local.set({ pendingCaptureRef: captureRef, pendingCaptureKey: idempotency });
  try {
    const result = await requestJson<{ lifecycle: string; status: string; item_public_id: string }>(API_ORIGIN, "/api/v1/browser-companion/extension/captures", {
      method: "POST",
      headers: { "Authorization": `Bearer ${state.accessToken}`, "Content-Type": "application/json", "Idempotency-Key": idempotency },
      body: JSON.stringify(payload),
    }, CAPTURE_REQUEST_TIMEOUT_MS);
    await chrome.storage.local.remove(["pendingCaptureRef", "pendingCaptureKey"]);
    return result;
  } catch (error) {
    if (error instanceof Error && ["queue_unavailable", "capture_upload_failed", "capture_conflict"].includes(error.message)) {
      await chrome.storage.local.remove(["pendingCaptureRef", "pendingCaptureKey"]);
    }
    throw error;
  }
}

async function disconnect() {
  const state = await storageState();
  if (typeof state.accessToken === "string") {
    try {
      await requestJson<void>(API_ORIGIN, "/api/v1/browser-companion/extension/grant", {
        method: "DELETE", headers: { "Authorization": `Bearer ${state.accessToken}` },
      });
    } catch { /* Local credential deletion still stops this installation. */ }
  }
  await chrome.storage.local.remove(STORAGE_KEYS);
  return { paired: false };
}

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  const command = message as Command;
  const operation = command.type === "STATUS" ? status()
    : command.type === "START_PAIRING" ? startPairing()
      : command.type === "FINISH_PAIRING" ? finishPairing()
        : command.type === "CAPTURE" ? capture()
          : command.type === "DISCONNECT" ? disconnect()
            : Promise.reject(new Error("unknown_command"));
  void operation.then((result) => respond({ ok: true, result })).catch((error: unknown) => respond({ ok: false, error: error instanceof Error ? error.message : "request_failed" }));
  return true;
});
