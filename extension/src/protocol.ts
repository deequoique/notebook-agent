export type Cue = { start_sec: number; end_sec: number; text: string };
export type Platform = "youtube" | "ntu_kaltura";
export type PageCapture = {
  platform: Platform;
  platform_id: string;
  canonical_url: string;
  page_url: string;
  metadata: {
    title: string | null;
    author: string | null;
    duration_sec: number | null;
    language: string | null;
    description: string | null;
    cover_url: string | null;
    tags: string[];
    chapters: Array<{ title: string; start_sec: number; end_sec: number | null }>;
  };
  caption: { status: "available"; source: "official_cc" | "auto_caption"; language: string; cues: Cue[] } | { status: "unavailable"; source: null; language: null; cues: [] };
};

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function contentHash(cues: Cue[]): Promise<string> {
  return sha256Hex(cues.map((cue) => `${cue.start_sec.toFixed(3)}\t${cue.end_sec.toFixed(3)}\t${cue.text.trim()}`).join("\n"));
}

export function captureRequest(capture: PageCapture) {
  const cues = capture.caption.cues;
  return contentHash(cues).then((content_hash) => ({
    protocol_version: "capture.v1" as const,
    client_version: "0.1.0",
    ...capture,
    content_hash,
  }));
}
