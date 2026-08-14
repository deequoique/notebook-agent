import type { PageCapture } from "./protocol.js";

type ScriptResult = { result?: PageCapture };

export async function captureActivePage(tabId: number, url: string): Promise<PageCapture> {
  const host = new URL(url).hostname.toLowerCase();
  const injection = host === "www.youtube.com" || host === "youtube.com" || host === "youtu.be"
    ? captureYouTubePage
    : host === "ntulearn.ntu.edu.sg" || host === "ntulearnvideo.ntu.edu.sg"
      ? captureKalturaPage
      : null;
  if (!injection) throw new Error("unsupported_page");
  const results = await chrome.scripting.executeScript({ target: { tabId, allFrames: host.endsWith("ntu.edu.sg") }, world: "MAIN", func: injection });
  const captures = results.flatMap((value) => (value as ScriptResult).result ? [(value as ScriptResult).result!] : []);
  const capture = captures.find((value) => value.caption.status === "available") ?? captures[0];
  if (!capture) throw new Error("capture_unavailable");
  return capture;
}

async function captureYouTubePage(): Promise<PageCapture> {
  type PlayerResponse = Record<string, unknown>;
  const page = window as typeof window & { ytInitialPlayerResponse?: PlayerResponse };
  const response = page.ytInitialPlayerResponse;
  const videoDetails = (response?.videoDetails ?? {}) as Record<string, unknown>;
  const renderer = ((response?.captions as Record<string, unknown> | undefined)?.playerCaptionsTracklistRenderer ?? {}) as Record<string, unknown>;
  const tracks = Array.isArray(renderer.captionTracks) ? renderer.captionTracks as Array<Record<string, unknown>> : [];
  const params = new URLSearchParams(location.search);
  const id = (params.get("v") ?? location.pathname.split("/").filter(Boolean).at(-1) ?? "").trim();
  if (!/^[A-Za-z0-9_-]{11}$/.test(id)) throw new Error("unsupported_page");
  const selected = tracks.find((track) => track.kind !== "asr") ?? tracks[0];
  let cues: Array<{ start_sec: number; end_sec: number; text: string }> = [];
  if (selected && typeof selected.baseUrl === "string") {
    const captionUrl = new URL(selected.baseUrl);
    captionUrl.searchParams.set("fmt", "json3");
    const body = await fetch(captionUrl, { credentials: "include" }).then((result) => {
      if (!result.ok) throw new Error("caption_fetch_failed");
      return result.json() as Promise<{ events?: Array<Record<string, unknown>> }>;
    });
    cues = (body.events ?? []).flatMap((event) => {
      const start = Number(event.tStartMs) / 1000;
      const end = start + Number(event.dDurationMs ?? 0) / 1000;
      const segs = Array.isArray(event.segs) ? event.segs as Array<Record<string, unknown>> : [];
      const text = segs.map((segment) => typeof segment.utf8 === "string" ? segment.utf8 : "").join("").replace(/\s+/g, " ").trim();
      return Number.isFinite(start) && Number.isFinite(end) && text ? [{ start_sec: start, end_sec: Math.max(start, end), text }] : [];
    });
  }
  const language = typeof selected?.languageCode === "string" ? selected.languageCode : "und";
  const title = typeof videoDetails.title === "string" ? videoDetails.title.slice(0, 1000) : document.title.replace(/\s*-\s*YouTube\s*$/, "").slice(0, 1000);
  const author = typeof videoDetails.author === "string" ? videoDetails.author.slice(0, 1000) : null;
  const duration = Number(videoDetails.lengthSeconds);
  return {
    platform: "youtube",
    platform_id: id,
    canonical_url: `https://www.youtube.com/watch?v=${id}`,
    page_url: `https://www.youtube.com/watch?v=${id}`,
    metadata: { title, author, duration_sec: Number.isFinite(duration) ? Math.max(0, Math.floor(duration)) : null, language, description: null, cover_url: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`, tags: [], chapters: [] },
    caption: cues.length
      ? { status: "available", source: selected?.kind === "asr" ? "auto_caption" : "official_cc", language, cues }
      : { status: "unavailable", source: null, language: null, cues: [] },
  };
}

async function captureKalturaPage(): Promise<PageCapture> {
  const clean = (value: string) => value.replace(/\s+/g, " ").trim();
  const pageText = document.documentElement.innerHTML;
  const candidates = [location.href, ...performance.getEntriesByType("resource").map((entry) => entry.name), pageText];
  const entryId = candidates.map((value) => value.match(/\b\d+_[A-Za-z0-9]+\b/)?.[0]).find(Boolean);
  if (!entryId) throw new Error("kaltura_entry_missing");

  const trackUrls = new Set<string>();
  document.querySelectorAll("track[src]").forEach((track) => {
    const source = (track as HTMLTrackElement).src;
    if (source) trackUrls.add(source);
  });
  for (const entry of performance.getEntriesByType("resource")) {
    if (/caption|subtitle|\.vtt(?:\?|$)|\.srt(?:\?|$)/i.test(entry.name)) trackUrls.add(entry.name);
  }
  for (const match of pageText.matchAll(/https?:\\?\/\\?\/[^"'<>\s]+/gi)) {
    const candidate = match[0].replace(/\\\//g, "/").replace(/&amp;/g, "&");
    if (!/caption|subtitle|\.vtt(?:\?|$)|\.srt(?:\?|$)|\.dfxp(?:\?|$)/i.test(candidate)) continue;
    try {
      const parsed = new URL(candidate);
      if (parsed.protocol === "https:" && (parsed.hostname.endsWith(".kaltura.com") || parsed.hostname.endsWith(".ntu.edu.sg"))) trackUrls.add(parsed.toString());
    } catch { /* Ignore malformed page-owned strings. */ }
  }
  const parseTimedText = (body: string) => {
    const cues: Array<{ start_sec: number; end_sec: number; text: string }> = [];
    const timing = /(\d{1,2}:)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}:)?(\d{2}):(\d{2})[,.](\d{3})/;
    const seconds = (hour: string | undefined, minute: string, second: string, millis: string) => Number(hour?.slice(0, -1) ?? 0) * 3600 + Number(minute) * 60 + Number(second) + Number(millis) / 1000;
    for (const block of body.replace(/\r/g, "").split(/\n\n+/)) {
      const lines = block.split("\n");
      const index = lines.findIndex((line) => timing.test(line));
      if (index < 0) continue;
      const match = lines[index]?.match(timing);
      if (!match) continue;
      const text = clean(lines.slice(index + 1).join(" ").replace(/<[^>]+>/g, ""));
      if (!text) continue;
      cues.push({ start_sec: seconds(match[1], match[2]!, match[3]!, match[4]!), end_sec: seconds(match[5], match[6]!, match[7]!, match[8]!), text });
    }
    if (!cues.length && /<(?:tt|p)\b/i.test(body)) {
      const clock = (value: string) => {
        if (/^\d+(?:\.\d+)?s$/.test(value)) return Number(value.slice(0, -1));
        const parts = value.split(":").map(Number);
        return parts.length === 3 && parts.every(Number.isFinite)
          ? parts[0]! * 3600 + parts[1]! * 60 + parts[2]!
          : Number.NaN;
      };
      const documentBody = new DOMParser().parseFromString(body, "application/xml");
      documentBody.querySelectorAll("p[begin][end]").forEach((node) => {
        const start = clock(node.getAttribute("begin") ?? "");
        const end = clock(node.getAttribute("end") ?? "");
        const text = clean(node.textContent ?? "");
        if (Number.isFinite(start) && Number.isFinite(end) && end >= start && text) cues.push({ start_sec: start, end_sec: end, text });
      });
    }
    return cues;
  };
  let cues: Array<{ start_sec: number; end_sec: number; text: string }> = [];
  for (const trackUrl of trackUrls) {
    try {
      const body = await fetch(trackUrl, { credentials: "include" }).then((result) => result.ok ? result.text() : "");
      cues = parseTimedText(body);
      if (cues.length) break;
    } catch { /* Try another observed caption resource without exposing its URL. */ }
  }
  const video = document.querySelector("video");
  const duration = Number(video?.duration);
  const pageUrl = `https://${location.hostname}${location.pathname}`;
  const canonicalCandidates = [location.href, ...Array.from(document.querySelectorAll("a[href]"), (anchor) => (anchor as HTMLAnchorElement).href)];
  const canonicalUrl = canonicalCandidates.flatMap((value) => {
    try {
      const parsed = new URL(value);
      return parsed.protocol === "https:" && ["ntulearn.ntu.edu.sg", "ntulearnvideo.ntu.edu.sg"].includes(parsed.hostname) && parsed.pathname.includes(entryId)
        ? [`https://${parsed.hostname}${parsed.pathname}`]
        : [];
    } catch { return []; }
  })[0] ?? `https://ntulearnvideo.ntu.edu.sg/media/${entryId}`;
  return {
    platform: "ntu_kaltura",
    platform_id: entryId,
    canonical_url: canonicalUrl,
    page_url: pageUrl,
    metadata: { title: clean(document.querySelector("h1")?.textContent ?? document.title).slice(0, 1000), author: null, duration_sec: Number.isFinite(duration) ? Math.max(0, Math.floor(duration)) : null, language: cues.length ? "und" : null, description: null, cover_url: null, tags: [], chapters: [] },
    caption: cues.length
      ? { status: "available", source: "official_cc", language: "und", cues }
      : { status: "unavailable", source: null, language: null, cues: [] },
  };
}
