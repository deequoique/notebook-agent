import type { Cue, PageCapture } from "./protocol.js";

type SafeCaptureError =
  | "unsupported_page"
  | "capture_unavailable"
  | "kaltura_entry_missing"
  | "caption_fetch_failed"
  | "caption_parse_failed"
  | "stale_player_response";

export type CaptureAttempt =
  | { status: "captured"; capture: PageCapture }
  | { status: "no_caption"; capture: PageCapture }
  | { status: "not_media_frame" }
  | { status: "failed"; error: SafeCaptureError };

type ScriptResult = { result?: unknown };

const SAFE_ERRORS = new Set<SafeCaptureError>([
  "unsupported_page",
  "capture_unavailable",
  "kaltura_entry_missing",
  "caption_fetch_failed",
  "caption_parse_failed",
  "stale_player_response",
]);

function isSafeError(value: unknown): value is SafeCaptureError {
  return typeof value === "string" && SAFE_ERRORS.has(value as SafeCaptureError);
}

function isFiniteCue(value: unknown): value is Cue {
  if (!value || typeof value !== "object") return false;
  const cue = value as Record<string, unknown>;
  return typeof cue.text === "string"
    && cue.text.trim().length > 0
    && cue.text.length <= 10_000
    && typeof cue.start_sec === "number"
    && Number.isFinite(cue.start_sec)
    && cue.start_sec >= 0
    && typeof cue.end_sec === "number"
    && Number.isFinite(cue.end_sec)
    && cue.end_sec >= cue.start_sec;
}

function isHttpsUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function isPublicCaptureUrl(
  value: unknown,
  platform: PageCapture["platform"],
  platformId: string,
  field: "canonical" | "page",
): value is string {
  if (!isHttpsUrl(value)) return false;
  try {
    const parsed = new URL(value);
    if (parsed.hash || parsed.username || parsed.password) return false;
    if (platform === "youtube") {
      if (parsed.hostname !== "www.youtube.com") return false;
      let onlyVideoId = true;
      parsed.searchParams.forEach((_value, key) => { if (key !== "v") onlyVideoId = false; });
      return parsed.pathname === "/watch" && parsed.searchParams.get("v") === platformId && onlyVideoId;
    }
    const canonicalHost = parsed.hostname === "ntulearn.ntu.edu.sg"
      || parsed.hostname === "ntulearnvideo.ntu.edu.sg";
    const pageHost = canonicalHost || parsed.hostname === "ntulearnv1.ntu.edu.sg";
    if (parsed.search || (field === "canonical" ? !canonicalHost : !pageHost)) return false;
    return field === "page" || parsed.pathname.includes(platformId);
  } catch {
    return false;
  }
}

function isPageCapture(value: unknown): value is PageCapture {
  if (!value || typeof value !== "object") return false;
  const capture = value as Record<string, unknown>;
  if (capture.platform !== "youtube" && capture.platform !== "ntu_kaltura") return false;
  if (typeof capture.platform_id !== "string") return false;
  if (capture.platform === "youtube" && !/^[A-Za-z0-9_-]{11}$/.test(capture.platform_id)) return false;
  if (capture.platform === "ntu_kaltura" && !/^[0-9]+_[A-Za-z0-9]+$/.test(capture.platform_id)) return false;
  if (!isPublicCaptureUrl(capture.canonical_url, capture.platform, capture.platform_id, "canonical")
    || !isPublicCaptureUrl(capture.page_url, capture.platform, capture.platform_id, "page")) return false;

  const metadata = capture.metadata;
  if (!metadata || typeof metadata !== "object") return false;
  const metadataRecord = metadata as Record<string, unknown>;
  if (metadataRecord.title !== null && (typeof metadataRecord.title !== "string" || metadataRecord.title.length > 1_000)) return false;
  if (metadataRecord.author !== null && (typeof metadataRecord.author !== "string" || metadataRecord.author.length > 1_000)) return false;
  if (metadataRecord.duration_sec !== null
    && (typeof metadataRecord.duration_sec !== "number" || !Number.isInteger(metadataRecord.duration_sec)
      || metadataRecord.duration_sec < 0 || metadataRecord.duration_sec > 31_536_000)) return false;
  if (metadataRecord.language !== null && (typeof metadataRecord.language !== "string" || metadataRecord.language.length > 64)) return false;
  if (metadataRecord.description !== null && (typeof metadataRecord.description !== "string" || metadataRecord.description.length > 50_000)) return false;
  if (metadataRecord.cover_url !== null && !isHttpsUrl(metadataRecord.cover_url)) return false;
  if (metadataRecord.cover_url !== null) {
    try {
      const cover = new URL(metadataRecord.cover_url as string);
      const expectedHost = capture.platform === "youtube" && cover.hostname === "i.ytimg.com";
      if (!expectedHost || cover.search || cover.hash || cover.username || cover.password) return false;
    } catch {
      return false;
    }
  }
  if (!Array.isArray(metadataRecord.tags) || metadataRecord.tags.length > 100
    || !metadataRecord.tags.every((tag) => typeof tag === "string" && tag.trim().length > 0 && tag.length <= 200)) return false;
  if (!Array.isArray(metadataRecord.chapters) || metadataRecord.chapters.length > 1_000
    || !metadataRecord.chapters.every((value) => {
      if (!value || typeof value !== "object") return false;
      const chapter = value as Record<string, unknown>;
      return typeof chapter.title === "string" && chapter.title.trim().length > 0 && chapter.title.length <= 500
        && typeof chapter.start_sec === "number" && Number.isFinite(chapter.start_sec) && chapter.start_sec >= 0
        && (chapter.end_sec === null || (typeof chapter.end_sec === "number" && Number.isFinite(chapter.end_sec)
          && chapter.end_sec >= chapter.start_sec));
    })) return false;

  const caption = capture.caption;
  if (!caption || typeof caption !== "object") return false;
  const captionRecord = caption as Record<string, unknown>;
  if (!Array.isArray(captionRecord.cues) || captionRecord.cues.length > 50_000 || !captionRecord.cues.every(isFiniteCue)) return false;
  let priorStart = -1;
  let textLength = 0;
  for (const value of captionRecord.cues) {
    const cue = value as Cue;
    if (cue.start_sec < priorStart) return false;
    priorStart = cue.start_sec;
    textLength += cue.text.length;
    if (textLength > 1_000_000) return false;
  }
  if (captionRecord.status === "available") {
    return (captionRecord.source === "official_cc" || captionRecord.source === "auto_caption")
      && typeof captionRecord.language === "string"
      && captionRecord.language.trim().length > 0
      && captionRecord.cues.length > 0;
  }
  return captionRecord.status === "unavailable"
    && captionRecord.source === null
    && captionRecord.language === null
    && captionRecord.cues.length === 0;
}

function normalizePageCapture(value: unknown): PageCapture | null {
  try {
    const cloned = structuredClone(value);
    if (!isPageCapture(cloned)) return null;
    const metadata = cloned.metadata;
    const caption: PageCapture["caption"] = cloned.caption.status === "available"
      ? {
        status: "available",
        source: cloned.caption.source,
        language: cloned.caption.language,
        cues: cloned.caption.cues.map((cue) => ({ start_sec: cue.start_sec, end_sec: cue.end_sec, text: cue.text })),
      }
      : { status: "unavailable", source: null, language: null, cues: [] };
    return {
      platform: cloned.platform,
      platform_id: cloned.platform_id,
      canonical_url: cloned.canonical_url,
      page_url: cloned.page_url,
      metadata: {
        title: metadata.title,
        author: metadata.author,
        duration_sec: metadata.duration_sec,
        language: metadata.language,
        description: metadata.description,
        cover_url: metadata.cover_url,
        tags: [...metadata.tags],
        chapters: metadata.chapters.map((chapter) => ({
          title: chapter.title,
          start_sec: chapter.start_sec,
          end_sec: chapter.end_sec,
        })),
      },
      caption,
    };
  } catch {
    return null;
  }
}

function captureScore(capture: PageCapture): number {
  const title = capture.metadata.title?.trim().toLowerCase() ?? "";
  const genericTitle = /^(media gallery|kaltura(?: media)? player|ntu learn|ntu learn video [a-z0-9_]+)$/.test(title);
  let canonicalHost = "";
  try { canonicalHost = new URL(capture.canonical_url).hostname; } catch { /* Validation filters this in normal use. */ }
  return (capture.caption.status === "available" ? 10_000 + capture.caption.cues.length : 0)
    + (capture.metadata.duration_sec !== null ? 200 : 0)
    + (title && !genericTitle ? 50 : 0)
    + (canonicalHost === "ntulearnvideo.ntu.edu.sg" ? 10 : 0);
}

export function selectBestCapture(captures: PageCapture[]): PageCapture | undefined {
  return captures.map(normalizePageCapture).filter((capture): capture is PageCapture => capture !== null).reduce<PageCapture | undefined>((best, candidate) => (
    !best || captureScore(candidate) > captureScore(best)
      || (captureScore(candidate) === captureScore(best) && JSON.stringify(candidate) < JSON.stringify(best)) ? candidate : best
  ), undefined);
}

function normalizeAttempt(value: unknown): CaptureAttempt | null {
  try {
    const directCapture = normalizePageCapture(value);
    if (directCapture) {
      return { status: directCapture.caption.status === "available" ? "captured" : "no_caption", capture: directCapture };
    }
    if (!value || typeof value !== "object") return null;
    const attempt = structuredClone(value) as Record<string, unknown>;
    if (attempt.status === "not_media_frame") return { status: "not_media_frame" };
    if (attempt.status === "captured" || attempt.status === "no_caption") {
      const capture = normalizePageCapture(attempt.capture);
      if (!capture) return null;
      if (attempt.status === "captured" && capture.caption.status !== "available") return null;
      if (attempt.status === "no_caption" && capture.caption.status !== "unavailable") return null;
      return { status: attempt.status, capture };
    }
    if (attempt.status === "failed" && isSafeError(attempt.error)) return { status: "failed", error: attempt.error };
    return null;
  } catch {
    return null;
  }
}

function aggregateAttempts(platform: "youtube" | "ntu_kaltura", values: unknown[]): PageCapture {
  const attempts = values.map(normalizeAttempt).filter((attempt): attempt is CaptureAttempt => attempt !== null);
  const captures = attempts.flatMap((attempt) => (attempt.status === "captured" || attempt.status === "no_caption") ? [attempt.capture] : []);
  const selected = selectBestCapture(captures);
  if (selected?.caption.status === "available") return selected;

  const failures = attempts.flatMap((attempt) => attempt.status === "failed" ? [attempt.error] : []);
  const retryableFailure = failures.find((error) => error === "caption_fetch_failed" || error === "caption_parse_failed");
  if (retryableFailure) throw new Error(retryableFailure);
  if (failures.includes("stale_player_response")) throw new Error("stale_player_response");
  if (selected) return selected;
  if (platform === "ntu_kaltura" && attempts.length > 0 && attempts.every((attempt) => attempt.status === "not_media_frame")) {
    throw new Error("kaltura_entry_missing");
  }
  throw new Error(failures[0] ?? "capture_unavailable");
}

export async function captureActivePage(tabId: number, url: string): Promise<PageCapture> {
  let parsed: URL;
  try { parsed = new URL(url); } catch { throw new Error("unsupported_page"); }
  if (parsed.protocol !== "https:") throw new Error("unsupported_page");

  const host = parsed.hostname.toLowerCase();
  const youtubeHost = host === "www.youtube.com" || host === "youtube.com" || host === "youtu.be";
  const youtubeShape = host === "youtu.be"
    ? /^\/[A-Za-z0-9_-]{11}(?:\/)?$/.test(parsed.pathname)
    : (parsed.pathname === "/watch" && /^[A-Za-z0-9_-]{11}$/.test(parsed.searchParams.get("v") ?? ""))
      || /^\/shorts\/[A-Za-z0-9_-]{11}(?:\/)?$/.test(parsed.pathname);
  const kalturaHost = host === "ntulearn.ntu.edu.sg"
    || host === "ntulearnvideo.ntu.edu.sg"
    || host === "ntulearnv1.ntu.edu.sg";
  const injection = youtubeHost && youtubeShape ? captureYouTubePage : kalturaHost ? captureKalturaPage : null;
  if (!injection) throw new Error("unsupported_page");

  const allFrames = kalturaHost;
  let results: unknown[];
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId, allFrames },
      world: "MAIN",
      func: injection,
    }) as unknown as unknown[];
  } catch {
    // A single inaccessible frame can reject an all-frames call. The top
    // frame is still useful for outer NTULearn pages and gives a stable error.
    if (!allFrames) throw new Error("capture_unavailable");
    try {
      results = await chrome.scripting.executeScript({
        target: { tabId, allFrames: false },
        world: "MAIN",
        func: injection,
      }) as unknown as unknown[];
    } catch {
      throw new Error("capture_unavailable");
    }
  }
  const values = results.flatMap((value) => {
    try {
      if (!value || typeof value !== "object") return [];
      return [(value as ScriptResult).result];
    } catch {
      return [];
    }
  });
  return aggregateAttempts(youtubeHost ? "youtube" : "ntu_kaltura", values);
}

/** MAIN-world YouTube adapter; keep this function self-contained for MV3. */
export async function captureYouTubePage(): Promise<CaptureAttempt> {
  type PlayerResponse = Record<string, unknown>;
  const clean = (value: string) => value
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .replace(/\s+([,.;:!?])/g, "$1")
    .trim();
  const fail = (error: SafeCaptureError): CaptureAttempt => ({ status: "failed", error });
  try {
    const currentUrl = new URL(location.href);
    const id = currentUrl.hostname === "youtu.be"
      ? currentUrl.pathname.split("/").filter(Boolean)[0] ?? ""
      : currentUrl.searchParams.get("v") ?? currentUrl.pathname.split("/").filter(Boolean).at(-1) ?? "";
    if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return fail("unsupported_page");
    const captureDeadline = Date.now() + 10_000;
    const responseControllers = new WeakMap<Response, AbortController>();
    const abortResponse = (response: Response): void => {
      try { responseControllers.get(response)?.abort(); } catch { /* A test/page response may not be weak-map compatible. */ }
    };
    const fetchBeforeDeadline = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const remaining = captureDeadline - Date.now();
      if (remaining <= 0) throw new Error("caption_fetch_timeout");
      const controller = typeof AbortController === "function" ? new AbortController() : undefined;
      const requestInit = controller ? { ...(init ?? {}), signal: controller.signal } : init;
      let timedOut = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const response = await Promise.race<Response>([
          fetch(input, requestInit),
          new Promise<Response>((_resolve, reject) => {
            timer = setTimeout(() => {
              timedOut = true;
              try { controller?.abort(); } catch { /* AbortController is best effort in page shims. */ }
              reject(new Error("caption_fetch_timeout"));
            }, Math.min(1_200, remaining));
          }),
        ]);
        if (controller) responseControllers.set(response, controller);
        return response;
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        if (timedOut) {
          try { controller?.abort(); } catch { /* Already aborted or unavailable. */ }
        }
      }
    };
    const readResponseBeforeDeadline = async <T>(
      reader: () => Promise<T>,
      onTimeout?: () => void,
    ): Promise<T> => {
      const remaining = captureDeadline - Date.now();
      if (remaining <= 0) throw new Error("caption_body_timeout");
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        return await Promise.race<T>([
          Promise.resolve().then(reader),
          new Promise<T>((_resolve, reject) => {
            timer = setTimeout(
              () => {
                try { onTimeout?.(); } catch { /* Cleanup is best effort. */ }
                reject(new Error("caption_body_timeout"));
              },
              Math.min(1_200, remaining),
            );
          }),
        ]);
      } finally {
        if (timer !== undefined) clearTimeout(timer);
      }
    };
    const MAX_YOUTUBE_RESPONSE_BYTES = 4_000_000;
    const readResponseText = async (response: Response): Promise<string> => {
      const stream = response.body;
      if (stream && typeof stream.getReader === "function") {
        const streamReader = stream.getReader();
        const chunks: Uint8Array[] = [];
        let totalBytes = 0;
        const cancelReader = (): void => {
          try {
            const cancel = (streamReader as ReadableStreamDefaultReader<Uint8Array> & { cancel?: () => Promise<void> }).cancel;
            if (typeof cancel !== "function") return;
            const pending = cancel.call(streamReader);
            if (pending && typeof pending.then === "function") void pending.catch(() => undefined);
          } catch { /* A body can already be closed by the browser. */ }
        };
        try {
          while (true) {
            const next = await readResponseBeforeDeadline(
              () => streamReader.read(),
              () => {
                abortResponse(response);
                cancelReader();
              },
            );
            if (next.done) break;
            const chunk = next.value;
            if (!(chunk instanceof Uint8Array)) {
              abortResponse(response);
              cancelReader();
              throw new Error("caption_body_unavailable");
            }
            totalBytes += chunk.byteLength;
            if (totalBytes > MAX_YOUTUBE_RESPONSE_BYTES) {
              abortResponse(response);
              cancelReader();
              throw new Error("caption_body_too_large");
            }
            chunks.push(chunk);
          }
        } finally {
          try { streamReader.releaseLock(); } catch { /* A consumed body is already released. */ }
        }
        const bytes = new Uint8Array(totalBytes);
        let offset = 0;
        for (const chunk of chunks) {
          bytes.set(chunk, offset);
          offset += chunk.byteLength;
        }
        const text = typeof TextDecoder === "function"
          ? new TextDecoder().decode(bytes)
          : String.fromCharCode(...bytes);
        if (text.length > MAX_YOUTUBE_RESPONSE_BYTES) {
          abortResponse(response);
          cancelReader();
          throw new Error("caption_body_too_large");
        }
        return text;
      }
      const reader = response as Response & { text?: () => Promise<string> };
      if (typeof reader.text !== "function") throw new Error("caption_body_unavailable");
      const text = await readResponseBeforeDeadline(() => reader.text!(), () => abortResponse(response));
      if (text.length > MAX_YOUTUBE_RESPONSE_BYTES) {
        abortResponse(response);
        throw new Error("caption_body_too_large");
      }
      return text;
    };
    const readResponseJson = async (response: Response): Promise<unknown> => {
      const reader = response as Response & { text?: () => Promise<string>; json?: () => Promise<unknown> };
      // Real browser Responses are read as text first so a trusted endpoint
      // cannot hand an unbounded body straight to JSON.parse. The JSON-only
      // fallback keeps the page-world adapter compatible with lightweight
      // test doubles and older embedded player shims.
      if (typeof reader.text === "function"
        || (reader.body && typeof reader.body.getReader === "function")) {
        const text = await readResponseText(response);
        return JSON.parse(text) as unknown;
      }
      if (typeof reader.json === "function") {
        return readResponseBeforeDeadline(() => reader.json!(), () => abortResponse(response));
      }
      throw new Error("caption_body_unavailable");
    };
    const currentVideoId = (): string => {
      try {
        const current = new URL(location.href);
        return current.hostname === "youtu.be"
          ? current.pathname.split("/").filter(Boolean)[0] ?? ""
          : current.searchParams.get("v") ?? current.pathname.split("/").filter(Boolean).at(-1) ?? "";
      } catch {
        return "";
      }
    };
    const isCurrentVideo = (): boolean => currentVideoId() === id;

    const page = globalThis as typeof globalThis & {
      ytInitialPlayerResponse?: unknown;
      ytplayer?: { config?: unknown; getPlayerResponse?: () => unknown };
      ytPlayer?: { config?: unknown; getPlayerResponse?: () => unknown };
      movie_player?: { getPlayerResponse?: () => unknown };
      ytdPlayer?: { getPlayerResponse?: () => unknown };
      ytplayerConfig?: unknown;
      ytInitialData?: unknown;
      ytcfg?: { get?: (key: string) => unknown };
    };
    const responses: PlayerResponse[] = [];
    const addResponse = (value: unknown, depth = 0) => {
      if (depth > 2 || value === null || value === undefined) return;
      let parsed: unknown = value;
      if (typeof parsed === "string") {
        try { parsed = JSON.parse(parsed); } catch { return; }
      }
      if (!parsed || typeof parsed !== "object") return;
      const record = parsed as PlayerResponse;
      if (record.videoDetails || record.captions) responses.push(record);
      const args = record.args && typeof record.args === "object" ? record.args as Record<string, unknown> : null;
      const playerVars = record.PLAYER_VARS && typeof record.PLAYER_VARS === "object" ? record.PLAYER_VARS as Record<string, unknown> : null;
      for (const nested of [record.playerResponse, record.player_response, record.raw_player_response, args?.player_response, args?.playerResponse, args?.raw_player_response, playerVars?.player_response]) addResponse(nested, depth + 1);
    };
    const playerElement = (() => {
      try {
        if (typeof document.getElementById === "function") {
          const moviePlayer = document.getElementById("movie_player");
          if (moviePlayer) return moviePlayer;
        }
        if (typeof document.querySelector === "function") return document.querySelector("ytd-player");
      } catch { /* The player element may be replaced during navigation. */ }
      return null;
    })();
    const holders = [page.movie_player, page.ytdPlayer, page.ytplayer, page.ytPlayer, playerElement];
    const selectedTrackHints: PlayerResponse[] = [];
    for (const holder of holders) {
      try {
        const player = holder as { getPlayerResponse?: () => unknown; getOption?: (namespace: string, option: string) => unknown } | null;
        if (player && typeof player.getPlayerResponse === "function") addResponse(player.getPlayerResponse());
        if (player && typeof player.getOption === "function") {
          const selected = player.getOption("captions", "track");
          if (selected && typeof selected === "object") {
            selectedTrackHints.push(selected as PlayerResponse);
            const nested = (selected as PlayerResponse).track;
            if (nested && typeof nested === "object") selectedTrackHints.push(nested as PlayerResponse);
          }
        }
      } catch { /* Player can be replaced during SPA navigation. */ }
    }
    for (const holder of [page.ytplayer?.config, page.ytPlayer?.config, page.ytplayerConfig]) addResponse(holder);
    addResponse(page.ytInitialPlayerResponse);

    const responseId = (response: PlayerResponse): string | null => {
      const details = response.videoDetails;
      if (!details || typeof details !== "object") return null;
      const value = (details as Record<string, unknown>).videoId;
      return typeof value === "string" ? value : null;
    };
    const accepted = responses.filter((response) => {
      const candidateId = responseId(response);
      return candidateId === null || candidateId === id;
    });
    if (!responses.length) return fail("capture_unavailable");
    if (!accepted.length) return fail("stale_player_response");
    const identifiedResponses = responses.filter((candidate) => responseId(candidate) !== null);
    if (identifiedResponses.length && !identifiedResponses.some((candidate) => responseId(candidate) === id)) {
      return fail("stale_player_response");
    }
    const captionRenderer = (response: PlayerResponse): Record<string, unknown> | null => {
      const captions = response.captions;
      if (!captions || typeof captions !== "object") return null;
      const renderer = (captions as Record<string, unknown>).playerCaptionsTracklistRenderer;
      return renderer && typeof renderer === "object" ? renderer as Record<string, unknown> : null;
    };
    const trackList = (response: PlayerResponse): Array<Record<string, unknown>> => {
      const tracks = captionRenderer(response)?.captionTracks;
      return Array.isArray(tracks) ? tracks.filter((track): track is Record<string, unknown> => Boolean(track && typeof track === "object")) : [];
    };
    const exactResponses = accepted.filter((candidate) => responseId(candidate) === id);
    const responsePool = exactResponses.length ? exactResponses : accepted;
    const response = responsePool.find((candidate) => trackList(candidate).length > 0) ?? responsePool[0]!;
    const tracks = trackList(response);
    const details = response.videoDetails && typeof response.videoDetails === "object" ? response.videoDetails as Record<string, unknown> : {};
    const title = typeof details.title === "string"
      ? clean(details.title).slice(0, 1000)
      : clean(typeof document.title === "string" ? document.title.replace(/\s*-\s*YouTube\s*$/i, "") : "").slice(0, 1000) || null;
    const author = typeof details.author === "string" ? clean(details.author).slice(0, 1000) || null : null;
    const durationValue = Number(details.lengthSeconds);
    const duration = Number.isFinite(durationValue) && durationValue >= 0 ? Math.floor(durationValue) : null;
    const baseCapture = (caption: PageCapture["caption"]): PageCapture => ({
      platform: "youtube",
      platform_id: id,
      canonical_url: `https://www.youtube.com/watch?v=${id}`,
      page_url: `https://www.youtube.com/watch?v=${id}`,
      metadata: { title, author, duration_sec: duration, language: caption.status === "available" ? caption.language : null, description: null, cover_url: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`, tags: [], chapters: [] },
      caption,
    });
    if (!tracks.length) return { status: "no_caption", capture: baseCapture({ status: "unavailable", source: null, language: null, cues: [] }) };

    const normalizeCues = (cues: Cue[]): Cue[] => cues
      .filter((cue) => Number.isFinite(cue.start_sec) && Number.isFinite(cue.end_sec)
        && cue.start_sec >= 0 && cue.end_sec >= cue.start_sec && clean(cue.text))
      .map((cue) => ({ start_sec: cue.start_sec, end_sec: cue.end_sec, text: clean(cue.text) }))
      .sort((left, right) => left.start_sec - right.start_sec);
    const transcriptRendererCues = (renderers: Array<Record<string, unknown>>): Cue[] => {
      const unique = new Map<string, Cue>();
      for (const renderer of renderers) {
        const startMs = Number(renderer.startMs);
        const endMs = Number(renderer.endMs);
        const snippet = renderer.snippet && typeof renderer.snippet === "object"
          ? renderer.snippet as Record<string, unknown>
          : {};
        const runs = Array.isArray(snippet.runs) ? snippet.runs as Array<Record<string, unknown>> : [];
        const text = clean(runs.map((run) => typeof run.text === "string" ? run.text : "").join("")
          || (typeof snippet.simpleText === "string" ? snippet.simpleText : ""));
        if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs < 0 || endMs < startMs || !text) continue;
        const cue = { start_sec: startMs / 1000, end_sec: endMs / 1000, text };
        unique.set(`${startMs}\u0000${endMs}\u0000${text}`, cue);
      }
      return normalizeCues([...unique.values()]);
    };
    const parseJson3 = (body: unknown): Cue[] => {
      const events = body && typeof body === "object" && Array.isArray((body as Record<string, unknown>).events)
        ? (body as Record<string, unknown>).events as Array<Record<string, unknown>>
        : [];
      return normalizeCues(events.flatMap((event) => {
        if (!event || typeof event !== "object") return [];
        const startMs = Number(event.tStartMs);
        const durationMs = Number(event.dDurationMs ?? 0);
        const segments = Array.isArray(event.segs) ? event.segs as Array<Record<string, unknown>> : [];
        const text = clean(segments.map((segment) => typeof segment.utf8 === "string" ? segment.utf8 : "").join(""));
        const start = startMs / 1000;
        const end = (startMs + durationMs) / 1000;
        return Number.isFinite(startMs) && Number.isFinite(durationMs) && startMs >= 0 && durationMs >= 0
          && Number.isFinite(end) && end >= start && text ? [{ start_sec: start, end_sec: end, text }] : [];
      }));
    };
    const parseClock = (value: string): number => {
      const parts = value.trim().replace(",", ".").split(":").map(Number);
      if (!parts.length || parts.some((part) => !Number.isFinite(part) || part < 0)) return Number.NaN;
      return parts.reduce((total, part) => total * 60 + part, 0);
    };
    const parseTimedText = (value: string): Cue[] => {
      const text = value.replace(/\r/g, "").trim();
      if (!text) return [];
      if (text.startsWith("{")) {
        try {
          const jsonCues = parseJson3(JSON.parse(text));
          if (jsonCues.length) return jsonCues;
        } catch { /* Continue with text formats. */ }
      }
      const blockCues = text.split(/\n{2,}/).flatMap((block) => {
        const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
        const timingIndex = lines.findIndex((line) => line.includes("-->"));
        if (timingIndex < 0) return [];
        const match = lines[timingIndex]!.match(/^([^\s]+)\s+-->\s+([^\s]+)/);
        if (!match) return [];
        const start = parseClock(match[1]!);
        const end = parseClock(match[2]!);
        const cueText = clean(lines.slice(timingIndex + 1).join(" "));
        return Number.isFinite(start) && Number.isFinite(end) && end >= start && cueText
          ? [{ start_sec: start, end_sec: end, text: cueText }]
          : [];
      });
      const xmlCues = Array.from(text.matchAll(/<text\b([^>]*)>([\s\S]*?)<\/text>/gi)).flatMap((match) => {
        const attributes = match[1] ?? "";
        const attribute = (name: string) => {
          const found = attributes.match(new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`, "i"));
          return found?.[1] ?? "";
        };
        const start = Number(attribute("start"));
        const duration = Number(attribute("dur"));
        const end = start + duration;
        const cueText = clean(match[2] ?? "");
        return Number.isFinite(start) && Number.isFinite(duration) && start >= 0 && duration >= 0
          && Number.isFinite(end) && end >= start && cueText
          ? [{ start_sec: start, end_sec: end, text: cueText }]
          : [];
      });
      return normalizeCues([...blockCues, ...xmlCues]);
    };

    const isAuto = (track: Record<string, unknown>) => track.kind === "asr" || (typeof track.vssId === "string" && track.vssId.startsWith("a."));
    const sameTrack = (hint: PlayerResponse, track: PlayerResponse): boolean => {
      if (typeof hint.baseUrl === "string") return hint.baseUrl === track.baseUrl;
      if (typeof hint.vssId === "string") return hint.vssId === track.vssId;
      if (typeof hint.languageCode !== "string" || hint.languageCode !== track.languageCode) return false;
      return hint.kind === undefined || hint.kind === track.kind;
    };
    const preferredIndices: number[] = [];
    const addPreferredIndex = (value: unknown) => {
      const index = typeof value === "number" ? value
        : typeof value === "string" && /^\d+$/.test(value) ? Number(value) : Number.NaN;
      if (Number.isInteger(index) && index >= 0 && index < tracks.length && !preferredIndices.includes(index)) preferredIndices.push(index);
    };
    for (const hint of selectedTrackHints) addPreferredIndex(tracks.findIndex((track) => sameTrack(hint, track)));
    const renderer = captionRenderer(response);
    addPreferredIndex(renderer?.defaultCaptionTrackIndex);
    const audioTracks = Array.isArray(renderer?.audioTracks)
      ? renderer.audioTracks.filter((track): track is Record<string, unknown> => Boolean(track && typeof track === "object"))
      : [];
    const rawDefaultAudioIndex = renderer?.defaultAudioTrackIndex;
    const defaultAudioIndex = typeof rawDefaultAudioIndex === "number" ? rawDefaultAudioIndex
      : typeof rawDefaultAudioIndex === "string" && /^\d+$/.test(rawDefaultAudioIndex) ? Number(rawDefaultAudioIndex) : Number.NaN;
    if (Number.isInteger(defaultAudioIndex) && defaultAudioIndex >= 0 && defaultAudioIndex < audioTracks.length) {
      addPreferredIndex(audioTracks[defaultAudioIndex]?.defaultCaptionTrackIndex);
    }
    audioTracks.forEach((audioTrack) => addPreferredIndex(audioTrack.defaultCaptionTrackIndex));
    const fallbackIndices = tracks.map((_track, index) => index)
      .sort((left, right) => Number(isAuto(tracks[left]!)) - Number(isAuto(tracks[right]!)));
    const ordered = [...preferredIndices, ...fallbackIndices.filter((index) => !preferredIndices.includes(index))]
      .map((index) => tracks[index]!);
    const trustedCaptionResponse = (result: Response): boolean => {
      if (!result.ok) return false;
      if (!result.url) return true;
      try {
        const finalUrl = new URL(result.url);
        const finalHost = finalUrl.hostname.toLowerCase();
        const trustedFinalHost = finalHost === "youtube.com" || finalHost.endsWith(".youtube.com")
          || finalHost === "googlevideo.com" || finalHost.endsWith(".googlevideo.com");
        return finalUrl.protocol === "https:" && !finalUrl.username && !finalUrl.password && trustedFinalHost
          && /timedtext|caption|subtitle/i.test(finalUrl.pathname);
      } catch {
        return false;
      }
    };
    const captured = (track: Record<string, unknown>, cues: Cue[]): CaptureAttempt => {
      const languageValue = track.languageCode ?? track.languageName;
      const language = typeof languageValue === "string" && clean(languageValue).length > 0 ? clean(languageValue).slice(0, 64) : "und";
      return {
        status: "captured",
        capture: baseCapture({ status: "available", source: isAuto(track) ? "auto_caption" : "official_cc", language, cues }),
      };
    };
    const errors: SafeCaptureError[] = [];
    for (const track of ordered) {
      const rawUrl = typeof track.baseUrl === "string" ? track.baseUrl : "";
      let baseCaptionUrl: URL;
      try {
        baseCaptionUrl = new URL(rawUrl, currentUrl);
        const host = baseCaptionUrl.hostname.toLowerCase();
        const trustedHost = host === "youtube.com" || host.endsWith(".youtube.com") || host === "googlevideo.com" || host.endsWith(".googlevideo.com");
        if (baseCaptionUrl.protocol !== "https:" || baseCaptionUrl.username || baseCaptionUrl.password
          || !trustedHost || !/timedtext|caption|subtitle/i.test(baseCaptionUrl.pathname)) throw new Error("bad_url");
      } catch {
        errors.push("caption_fetch_failed");
        continue;
      }
      const jsonUrl = new URL(baseCaptionUrl);
      jsonUrl.searchParams.set("fmt", "json3");
      let jsonResponse: Response;
      try {
        jsonResponse = await fetchBeforeDeadline(jsonUrl.toString(), { credentials: "include" });
      } catch {
        errors.push("caption_fetch_failed");
        continue;
      }
      if (!trustedCaptionResponse(jsonResponse)) {
        errors.push("caption_fetch_failed");
        continue;
      }
      try {
        const cues = parseJson3(await readResponseJson(jsonResponse));
        if (cues.length) return isCurrentVideo() ? captured(track, cues) : fail("stale_player_response");
      } catch { /* Try text formats using fresh requests. */ }
      errors.push("caption_parse_failed");

      const vttUrl = new URL(baseCaptionUrl);
      vttUrl.searchParams.set("fmt", "vtt");
      const fallbackUrls = Array.from(new Set([vttUrl.toString(), baseCaptionUrl.toString()]));
      for (const fallbackUrl of fallbackUrls) {
        let fallbackResponse: Response;
        try {
          fallbackResponse = await fetchBeforeDeadline(fallbackUrl, { credentials: "include" });
        } catch {
          errors.push("caption_fetch_failed");
          continue;
        }
        if (!trustedCaptionResponse(fallbackResponse)) {
          errors.push("caption_fetch_failed");
          continue;
        }
        try {
          const cues = parseTimedText(await readResponseText(fallbackResponse));
          if (cues.length) return isCurrentVideo() ? captured(track, cues) : fail("stale_player_response");
        } catch { /* Continue to the next representation or track. */ }
        errors.push("caption_parse_failed");
      }
    }

    const findTranscriptCommand = (root: unknown): { params: string; clickTrackingParams: string | null } | null => {
      if (!root || typeof root !== "object") return null;
      const queue: unknown[] = [root];
      const seen = new Set<unknown>();
      for (let inspected = 0; queue.length && inspected < 50_000; inspected += 1) {
        const value = queue.shift();
        if (!value || typeof value !== "object" || seen.has(value)) continue;
        seen.add(value);
        const record = value as Record<string, unknown>;
        const endpoint = record.getTranscriptEndpoint;
        if (endpoint && typeof endpoint === "object") {
          const params = (endpoint as Record<string, unknown>).params;
          if (typeof params === "string" && params.length > 0 && params.length <= 2_000) {
            const clickTrackingParams = record.clickTrackingParams;
            return {
              params,
              clickTrackingParams: typeof clickTrackingParams === "string" && clickTrackingParams.length <= 2_000
                ? clickTrackingParams
                : null,
            };
          }
        }
        if (Array.isArray(value)) queue.push(...value);
        else queue.push(...Object.values(record));
      }
      return null;
    };
    const transcriptCommand = findTranscriptCommand(page.ytInitialData);
    const transcriptParams = transcriptCommand?.params ?? null;
    let decodedTranscriptParams: string | null = null;
    const transcriptParamsMatchCurrentVideo = (() => {
      if (!transcriptParams) return false;
      try {
        decodedTranscriptParams = decodeURIComponent(transcriptParams);
        const normalized = decodedTranscriptParams.replace(/-/g, "+").replace(/_/g, "/");
        const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
        const binary = atob(padded);
        const target = Array.from(id).map((character) => character.charCodeAt(0));
        const bytes = Array.from(binary, (character) => character.charCodeAt(0));
        const readVarint = (offset: number): { value: number; next: number } | null => {
          let value = 0;
          let shift = 0;
          let cursor = offset;
          while (cursor < bytes.length && shift <= 63) {
            const byte = bytes[cursor++]!;
            value += (byte & 0x7f) * 2 ** shift;
            if (!(byte & 0x80)) return { value, next: cursor };
            shift += 7;
          }
          return null;
        };
        const sameBytes = (start: number, end: number): boolean => {
          if (end - start !== target.length) return false;
          return target.every((byte, index) => bytes[start + index] === byte);
        };
        const containsDelimitedVideoId = (start: number, end: number, depth: number): boolean => {
          if (depth > 5) return false;
          let cursor = start;
          while (cursor < end) {
            const key = readVarint(cursor);
            if (!key || key.value < 8) return false;
            cursor = key.next;
            const wireType = key.value & 0x07;
            if (wireType === 0) {
              const value = readVarint(cursor);
              if (!value) return false;
              cursor = value.next;
              continue;
            }
            if (wireType === 1) {
              cursor += 8;
              if (cursor > end) return false;
              continue;
            }
            if (wireType === 2) {
              const length = readVarint(cursor);
              if (!length || length.value < 0 || length.value > end - length.next) return false;
              const valueStart = length.next;
              const valueEnd = valueStart + length.value;
              if (sameBytes(valueStart, valueEnd)) return true;
              if (containsDelimitedVideoId(valueStart, valueEnd, depth + 1)) return true;
              cursor = valueEnd;
              continue;
            }
            if (wireType === 5) {
              cursor += 4;
              if (cursor > end) return false;
              continue;
            }
            return false;
          }
          return false;
        };
        // The real endpoint uses a base64url-encoded protobuf. Require the
        // current ID as a complete length-delimited protobuf value (including
        // nested messages), rather than accepting an incidental substring.
        if (containsDelimitedVideoId(0, bytes.length, 0)) return true;
        // Keep the tiny marker form accepted by page-world test shims and old
        // embedded players, but bind it exactly instead of using substring
        // matching. Real YouTube params never rely on this compatibility path.
        return binary === `video:${id}`;
      } catch {
        return false;
      }
    })();
    const configGet = page.ytcfg && typeof page.ytcfg.get === "function" ? page.ytcfg.get.bind(page.ytcfg) : null;
    const apiKey = configGet?.("INNERTUBE_API_KEY");
    const context = configGet?.("INNERTUBE_CONTEXT");
    const clientName = configGet?.("INNERTUBE_CLIENT_NAME");
    const clientVersion = configGet?.("INNERTUBE_CLIENT_VERSION");
    if (isCurrentVideo() && transcriptParamsMatchCurrentVideo && typeof apiKey === "string" && apiKey.length > 0 && apiKey.length <= 256
      && context && typeof context === "object" && (typeof clientName === "string" || typeof clientName === "number")
      && typeof clientVersion === "string" && clientVersion.length > 0 && clientVersion.length <= 128) {
      const endpoint = new URL("/youtubei/v1/get_transcript", currentUrl.origin);
      endpoint.searchParams.set("prettyPrint", "false");
      endpoint.searchParams.set("key", apiKey);
      let transcriptResponse: Response | null = null;
      try {
        const requestContext = transcriptCommand?.clickTrackingParams
          ? { ...(context as Record<string, unknown>), clickTracking: { clickTrackingParams: transcriptCommand.clickTrackingParams } }
          : context;
        transcriptResponse = await fetchBeforeDeadline(endpoint.toString(), {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            "X-YouTube-Client-Name": String(clientName),
            "X-YouTube-Client-Version": clientVersion,
          },
          body: JSON.stringify({ context: requestContext, params: decodedTranscriptParams }),
        });
      } catch {
        errors.push("caption_fetch_failed");
      }
      if (transcriptResponse) {
        const validTranscriptResponse = (() => {
          if (!transcriptResponse?.ok) return false;
          if (!transcriptResponse.url) return true;
          try {
            const finalUrl = new URL(transcriptResponse.url);
            return finalUrl.origin === currentUrl.origin && finalUrl.pathname === "/youtubei/v1/get_transcript";
          } catch {
            return false;
          }
        })();
        if (!validTranscriptResponse) {
          errors.push("caption_fetch_failed");
        } else {
          try {
            const body = await readResponseJson(transcriptResponse);
            const renderers: Array<Record<string, unknown>> = [];
            const queue: unknown[] = [body];
            const seen = new Set<unknown>();
            for (let inspected = 0; queue.length && inspected < 100_000; inspected += 1) {
              const value = queue.shift();
              if (!value || typeof value !== "object" || seen.has(value)) continue;
              seen.add(value);
              const record = value as Record<string, unknown>;
              const renderer = record.transcriptSegmentRenderer;
              if (renderer && typeof renderer === "object") renderers.push(renderer as Record<string, unknown>);
              if (Array.isArray(value)) queue.push(...value);
              else queue.push(...Object.values(record));
            }
            const cues = transcriptRendererCues(renderers);
            if (cues.length) return isCurrentVideo()
              ? captured(ordered[0] ?? tracks[0]!, cues)
              : fail("stale_player_response");
            errors.push("caption_parse_failed");
          } catch {
            errors.push("caption_parse_failed");
          }
        }
      }
    }

    const renderedTranscriptCues = (): Cue[] => {
      try {
        if (typeof document.querySelectorAll !== "function") return [];
        const renderers = Array.from(document.querySelectorAll("ytd-transcript-segment-renderer")).flatMap((node) => {
          const data = (node as Element & { data?: unknown }).data;
          return data && typeof data === "object" ? [data as Record<string, unknown>] : [];
        });
        return transcriptRendererCues(renderers);
      } catch {
        return [];
      }
    };
    const clickNode = (selector: string): boolean => {
      try {
        if (typeof document.querySelector !== "function") return false;
        const candidates = typeof document.querySelectorAll === "function"
          ? Array.from(document.querySelectorAll(selector)) as Array<Element & { click?: () => void }>
          : [];
        const visible = candidates.find((candidate) => {
          try { return typeof candidate.getClientRects === "function" && candidate.getClientRects().length > 0; } catch { return false; }
        });
        const node = visible ?? candidates[0]
          ?? document.querySelector(selector) as (Element & { click?: () => void }) | null;
        if (!node || typeof node.click !== "function") return false;
        node.click();
        return true;
      } catch {
        return false;
      }
    };
    let domCues = renderedTranscriptCues();
    if (domCues.length) return isCurrentVideo() ? captured(ordered[0] ?? tracks[0]!, domCues) : fail("stale_player_response");
    const supportsTranscriptDom = typeof document.querySelector === "function"
      && typeof document.querySelectorAll === "function";
    if (!domCues.length && supportsTranscriptDom && isCurrentVideo()) {
      clickNode("#description-inline-expander #expand, ytd-text-inline-expander #expand, tp-yt-paper-button#expand");
      let transcriptUiRequested = false;
      for (let attempt = 0; attempt < 10 && Date.now() < captureDeadline && isCurrentVideo(); attempt += 1) {
        if (clickNode("ytd-video-description-transcript-section-renderer button")) {
          transcriptUiRequested = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      for (let attempt = 0; transcriptUiRequested && attempt < 40 && Date.now() < captureDeadline && isCurrentVideo(); attempt += 1) {
        domCues = renderedTranscriptCues();
        if (domCues.length) return isCurrentVideo() ? captured(ordered[0] ?? tracks[0]!, domCues) : fail("stale_player_response");
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    if (!isCurrentVideo()) return fail("stale_player_response");
    return fail(errors.includes("caption_fetch_failed") ? "caption_fetch_failed" : "caption_parse_failed");
  } catch {
    return fail("capture_unavailable");
  }
}

/** MAIN-world NTULearn/Kaltura adapter; keep this function self-contained for MV3. */
export async function captureKalturaPage(): Promise<CaptureAttempt> {
  type LocalCue = { start_sec: number; end_sec: number; text: string };
  type Candidate = { url?: string; language: string; cues?: LocalCue[]; duration?: number | null };
  type ResourceFailure = "fetch" | "parse";
  type ResourceRead = { cues: LocalCue[]; failure?: ResourceFailure };
  const MAX_CAPTURE_MS = 10_000;
  const MAX_PLAYLIST_CHARS = 256_000;
  const MAX_SEGMENT_CHARS = 512_000;
  const MAX_TOTAL_SEGMENT_CHARS = 4_000_000;
  const MAX_PLAYLIST_SEGMENTS = 240;
  const MAX_PLAYLIST_CUES = 50_000;
  const clean = (value: string) => value
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .replace(/\s+([,.;:!?])/g, "$1")
    .trim();
  const queryAll = (selector: string): Element[] => {
    try { return typeof document.querySelectorAll === "function" ? Array.from(document.querySelectorAll(selector)) : []; } catch { return []; }
  };
  const queryOne = (selector: string): Element | null => {
    try { return typeof document.querySelector === "function" ? document.querySelector(selector) : null; } catch { return null; }
  };
  const attribute = (node: Element | null, name: string): string => {
    try { return node?.getAttribute(name) ?? ""; } catch { return ""; }
  };
  const fail = (error: SafeCaptureError): CaptureAttempt => ({ status: "failed", error });
  try {
    const frameHost = location.hostname.toLowerCase();
    const captureDeadline = Date.now() + MAX_CAPTURE_MS;
    const supportedFrameHost = frameHost === "ntulearn.ntu.edu.sg"
      || frameHost === "ntulearnvideo.ntu.edu.sg"
      || frameHost === "ntulearnv1.ntu.edu.sg"
      || frameHost === "cdnapisec.kaltura.com";
    if (!supportedFrameHost) return { status: "not_media_frame" };
    const isNtuMediaHost = frameHost === "ntulearn.ntu.edu.sg"
      || frameHost === "ntulearnvideo.ntu.edu.sg"
      || frameHost === "ntulearnv1.ntu.edu.sg";
    const page = globalThis as typeof globalThis & {
      kWidget?: unknown;
      kalturaPlayer?: unknown;
      KalturaPlayer?: unknown;
      __INITIAL_STATE__?: unknown;
      __INITIAL_DATA__?: unknown;
    };
    const readPageValue = (key: string): unknown => {
      try { return (page as Record<string, unknown>)[key]; } catch { return undefined; }
    };
    const resources = (() => {
      try {
        return typeof performance !== "undefined" ? performance.getEntriesByType("resource").flatMap((entry) => typeof entry.name === "string" ? [entry.name] : []) : [];
      } catch { return []; }
    })();
    const pageText = (() => {
      try { return document.documentElement?.innerHTML ?? ""; } catch { return ""; }
    })();
    const iframeUrls = queryAll("iframe[src]").flatMap((frame) => {
      const value = (frame as HTMLIFrameElement).src || attribute(frame, "src");
      return value ? [value] : [];
    });
    // Kaltura entry IDs use a numeric partner prefix. Keeping this aligned
    // with capture.v1 also avoids treating keys such as `entry_id` and
    // `uiconf_id` in serialized page state as media identities.
    const entryPattern = /\b[0-9]+_[A-Za-z0-9]+\b/;
    const entryIds: string[] = [];
    const addEntry = (value: unknown) => {
      if (typeof value !== "string") return;
      const match = value.match(entryPattern)?.[0];
      if (match && !entryIds.includes(match)) entryIds.push(match);
    };
    addEntry(location.href);
    resources.forEach(addEntry);
    iframeUrls.forEach(addEntry);
    const scanEntries = (value: unknown, depth = 0) => {
      if (depth > 3 || value === null || value === undefined) return;
      if (typeof value === "string") { addEntry(value); return; }
      if (typeof value !== "object") return;
      try {
        if (Array.isArray(value)) { value.slice(0, 80).forEach((item) => scanEntries(item, depth + 1)); return; }
        for (const key of Object.keys(value as Record<string, unknown>).slice(0, 120)) {
          try {
            const nested = (value as Record<string, unknown>)[key];
            if (/entry.?id|media.?id|video.?id/i.test(key)) addEntry(nested);
            if (depth < 2) scanEntries(nested, depth + 1);
          } catch { /* Skip only the throwing property. */ }
        }
      } catch { /* A page-owned getter is untrusted; continue with other sources. */ }
    };
    for (const key of ["kWidget", "kalturaPlayer", "KalturaPlayer", "__INITIAL_STATE__", "__INITIAL_DATA__"]) scanEntries(readPageValue(key));
    queryAll("[data-entry-id], [data-entryid]").forEach((node) => { addEntry(attribute(node, "data-entry-id")); addEntry(attribute(node, "data-entryid")); });
    for (const match of pageText.matchAll(/(?:entry[_-]?id|entryId)[^A-Za-z0-9]{0,16}([0-9]+_[A-Za-z0-9]+)/gi)) addEntry(match[1]);
    const entryId = entryIds[0];
    if (!entryId) return { status: "not_media_frame" };

    const parseClock = (value: string): number => {
      const normalized = value.trim();
      if (/^\d+(?:\.\d+)?ms$/.test(normalized)) return Number(normalized.slice(0, -2)) / 1000;
      if (/^\d+(?:\.\d+)?s$/.test(normalized)) return Number(normalized.slice(0, -1));
      const parts = normalized.replace(",", ".").split(":").map(Number);
      if ((parts.length === 2 || parts.length === 3) && parts.every((part) => Number.isFinite(part) && part >= 0)) {
        return parts.length === 3 ? parts[0]! * 3600 + parts[1]! * 60 + parts[2]! : parts[0]! * 60 + parts[1]!;
      }
      return Number.NaN;
    };
    const normalizeCues = (cues: LocalCue[]): LocalCue[] => cues
      .filter((cue) => Number.isFinite(cue.start_sec) && Number.isFinite(cue.end_sec) && cue.start_sec >= 0 && cue.end_sec >= cue.start_sec && clean(cue.text))
      .map((cue) => ({ start_sec: cue.start_sec, end_sec: cue.end_sec, text: clean(cue.text) }))
      .sort((left, right) => left.start_sec - right.start_sec);
    const parseTimedText = (body: string): LocalCue[] => {
      const parsed: LocalCue[] = [];
      const normalized = body.replace(/^\uFEFF/, "").replace(/\r/g, "");
      const timestampMapLine = normalized.match(/^X-TIMESTAMP-MAP\s*=\s*(.+)$/im)?.[1];
      let timestampOffset = 0;
      if (timestampMapLine !== undefined) {
        const localValue = timestampMapLine.match(/(?:^|,)\s*LOCAL:([^,\s]+)/i)?.[1];
        const mpegValue = timestampMapLine.match(/(?:^|,)\s*MPEGTS:(\d+)/i)?.[1];
        const localSeconds = localValue === undefined ? Number.NaN : parseClock(localValue);
        const mpegTimestamp = mpegValue === undefined ? Number.NaN : Number(mpegValue);
        if (!Number.isFinite(localSeconds) || !Number.isFinite(mpegTimestamp)
          || mpegTimestamp < 0 || mpegTimestamp > 8_589_934_591) return [];
        timestampOffset = mpegTimestamp / 90_000 - localSeconds;
      }
      const timing = /^\s*((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$/;
      for (const block of normalized.split(/\n\s*\n+/)) {
        const lines = block.split("\n");
        const lineIndex = lines.findIndex((line) => timing.test(line));
        if (lineIndex < 0) continue;
        const match = lines[lineIndex]?.match(timing);
        if (!match) continue;
        const start = parseClock(match[1]!);
        const end = parseClock(match[2]!);
        const text = clean(lines.slice(lineIndex + 1).join(" "));
        const adjustedStart = start + timestampOffset;
        const adjustedEnd = end + timestampOffset;
        if (Number.isFinite(adjustedStart) && Number.isFinite(adjustedEnd) && adjustedStart >= 0 && adjustedEnd >= adjustedStart && text) parsed.push({ start_sec: adjustedStart, end_sec: adjustedEnd, text });
      }
      if (!parsed.length && /<(?:tt|p)\b/i.test(normalized)) {
        const addXmlCue = (begin: string, endValue: string, dur: string, textValue: string) => {
          const start = parseClock(begin);
          const explicitEnd = parseClock(endValue);
          const duration = parseClock(dur);
          const end = Number.isFinite(explicitEnd) ? explicitEnd : start + duration;
          const text = clean(textValue);
          const adjustedStart = start + timestampOffset;
          const adjustedEnd = end + timestampOffset;
          if (Number.isFinite(adjustedStart) && Number.isFinite(adjustedEnd) && adjustedStart >= 0 && adjustedEnd >= adjustedStart && text) parsed.push({ start_sec: adjustedStart, end_sec: adjustedEnd, text });
        };
        if (typeof DOMParser !== "undefined") {
          try {
            const xml = new DOMParser().parseFromString(normalized, "application/xml");
            Array.from(xml.querySelectorAll("p[begin]")).forEach((node) => addXmlCue(node.getAttribute("begin") ?? "", node.getAttribute("end") ?? "", node.getAttribute("dur") ?? "", node.textContent ?? ""));
          } catch { /* Use the conservative attribute parser below. */ }
        }
        if (!parsed.length) {
          const pPattern = /<p\b([^>]*)>([\s\S]*?)<\/p>/gi;
          for (const match of normalized.matchAll(pPattern)) {
            const attrs = match[1] ?? "";
            const get = (name: string) => attrs.match(new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`, "i"))?.[1] ?? "";
            addXmlCue(get("begin"), get("end"), get("dur"), match[2] ?? "");
          }
        }
      }
      return normalizeCues(parsed);
    };
    const readNativeCues = (track: TextTrack): LocalCue[] => {
      try {
        return normalizeCues(Array.from(track.cues ?? []).flatMap((rawCue) => {
          const cue = rawCue as TextTrackCue & { text?: string };
          return typeof cue.text === "string" ? [{ start_sec: cue.startTime, end_sec: cue.endTime, text: cue.text }] : [];
        }));
      } catch { return []; }
    };
    const trackLanguage = (value: unknown): string => typeof value === "string" && clean(value).length ? clean(value).slice(0, 64) : "und";
    const isCaptionKind = (value: unknown): boolean => typeof value !== "string" || !value
      || value === "captions" || value === "subtitles";
    const nativeTracks: TextTrack[] = queryAll("video").flatMap((video) => {
      try { return Array.from((video as HTMLVideoElement).textTracks ?? []); } catch { return []; }
    }).filter((track) => isCaptionKind(track.kind));
    const previousModes = nativeTracks.map((track) => {
      try { return track.mode; } catch { return "disabled" as TextTrackMode; }
    });
    let nativeCapture: Candidate | null = null;
    try {
      nativeTracks.forEach((track) => { try { if (track.mode === "disabled") track.mode = "hidden"; } catch { /* Continue with other tracks. */ } });
      for (let attempt = 0; attempt < 10 && !nativeTracks.some((track) => (track.cues?.length ?? 0) > 0); attempt += 1) {
        if (!nativeTracks.length) break;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      const ordered = nativeTracks.map((track, index) => ({ track, index })).sort((left, right) => Number(previousModes[right.index] === "showing") - Number(previousModes[left.index] === "showing"));
      for (const item of ordered) {
        const cues = readNativeCues(item.track);
        if (cues.length) {
          nativeCapture = { language: trackLanguage(item.track.language), cues };
          break;
        }
      }
    } finally {
      nativeTracks.forEach((track, index) => { try { track.mode = previousModes[index] ?? "disabled"; } catch { /* A removed track is harmless. */ } });
    }
    if (nativeCapture?.cues?.length) {
      const video = queryOne("video") as HTMLVideoElement | null;
      const durationValue = Number(video?.duration);
      nativeCapture.duration = Number.isFinite(durationValue) && durationValue >= 0 ? Math.floor(durationValue) : null;
    }

    const isPlaylistUrl = (value: string): boolean => {
      try {
        const parsed = new URL(value, location.href);
        return /\.m3u8$/i.test(parsed.pathname)
          && /caption_captionasset[^/]*\/action\/servewebvtt\b/i.test(parsed.pathname);
      } catch { return false; }
    };
    const isPlaylistSegmentUrl = (value: string): boolean => {
      try {
        const parsed = new URL(value, location.href);
        return /caption_captionasset[^/]*\/action\/servewebvtt\b/i.test(parsed.pathname)
          && /(?:\/|[?&])segmentindex(?:\/|=)/i.test(`${parsed.pathname}?${parsed.searchParams.toString()}`);
      } catch { return false; }
    };
    const isDirectTimedTextUrl = (value: string): boolean => {
      try {
        const parsed = new URL(value, location.href);
        const pathname = parsed.pathname.toLowerCase();
        if (/\.(?:css|js|mjs|html?|json|png|jpe?g|gif|svg|woff2?)$/i.test(pathname)) return false;
        return /\.(?:vtt|srt|dfxp|ttml)$/i.test(pathname)
          || (/(?:caption|subtitle|timedtext|texttrack)/i.test(pathname)
            && !/\/captions?(?:[-_]|\/|$)/i.test(pathname));
      } catch { return false; }
    };
    const trustedCaptionUrl = (value: string): string | null => {
      try {
        const parsed = new URL(value, location.href);
        const host = parsed.hostname.toLowerCase();
        const trustedHost = host === "ntulearn.ntu.edu.sg"
          || host === "ntulearnvideo.ntu.edu.sg"
          || host === "ntulearnv1.ntu.edu.sg"
          || host === "kaltura.com"
          || host.endsWith(".kaltura.com");
        const normalized = parsed.toString();
        const captionShape = isPlaylistUrl(normalized) || isDirectTimedTextUrl(normalized);
        return parsed.protocol === "https:" && !parsed.username && !parsed.password && trustedHost && captionShape ? normalized : null;
      } catch { return null; }
    };
    const dedupeCues = (cues: LocalCue[]): LocalCue[] => {
      const unique = new Map<string, LocalCue>();
      for (const cue of normalizeCues(cues)) {
        const key = `${cue.start_sec}\u0000${cue.end_sec}\u0000${cue.text}`;
        if (!unique.has(key)) unique.set(key, cue);
        if (unique.size > MAX_PLAYLIST_CUES) return [];
      }
      return [...unique.values()].sort((left, right) => left.start_sec - right.start_sec);
    };
    const captionRequestCredentials = (value: string): "include" | "omit" => {
      try {
        const resourceOrigin = new URL(value, location.href).origin;
        const pageOrigin = new URL(location.href).origin;
        return resourceOrigin === pageOrigin ? "include" : "omit";
      } catch {
        return "omit";
      }
    };
    const fetchCaptionResponse = async (url: string): Promise<{ body: string; finalUrl: string } | { failure: "fetch" }> => {
      const remaining = captureDeadline - Date.now();
      if (remaining <= 0) return { failure: "fetch" };
      let timer: ReturnType<typeof setTimeout> | undefined;
      const controller = typeof AbortController === "undefined" ? undefined : new AbortController();
      try {
        const response = await Promise.race<Response>([
          fetch(url, { credentials: captionRequestCredentials(url), ...(controller ? { signal: controller.signal } : {}) }),
          new Promise<Response>((_resolve, reject) => {
            timer = setTimeout(() => {
              controller?.abort();
              reject(new Error("caption_fetch_timeout"));
            }, Math.min(1_200, remaining));
          }),
        ]);
        if (!response.ok) return { failure: "fetch" };
        const finalUrl = response.url || url;
        if (!trustedCaptionUrl(finalUrl)) return { failure: "fetch" };
        const textRemaining = captureDeadline - Date.now();
        if (textRemaining <= 0) return { failure: "fetch" };
        if (timer !== undefined) clearTimeout(timer);
        timer = undefined;
        const body = await Promise.race<string>([
          response.text(),
          new Promise<string>((_resolve, reject) => {
            timer = setTimeout(() => {
              controller?.abort();
              reject(new Error("caption_body_timeout"));
            }, Math.min(1_200, textRemaining));
          }),
        ]);
        const maxChars = isPlaylistUrl(finalUrl) ? MAX_PLAYLIST_CHARS : MAX_SEGMENT_CHARS;
        if (body.length > maxChars) return { failure: "fetch" };
        return { body, finalUrl };
      } catch {
        return { failure: "fetch" };
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        controller?.abort();
      }
    };
    const playlistSegments = (body: string, playlistUrl: string): string[] | null => {
      const lines = body.replace(/^\uFEFF/, "").replace(/\r/g, "").split("\n");
      if (lines[0]?.trim() !== "#EXTM3U") return null;
      const segments: string[] = [];
      let ended = false;
      for (const rawLine of lines.slice(1)) {
        const line = rawLine.trim();
        if (!line) continue;
        if (/^#EXT-X-ENDLIST\b/i.test(line)) { ended = true; continue; }
        if (line.startsWith("#")) continue;
        if (ended) return null;
        let resolved: string;
        try { resolved = new URL(line, playlistUrl).toString(); } catch { return null; }
        const trusted = trustedCaptionUrl(resolved);
        if (!trusted || isPlaylistUrl(trusted)) return null;
        if (!segments.includes(trusted)) segments.push(trusted);
        if (segments.length > MAX_PLAYLIST_SEGMENTS) return null;
      }
      return ended && segments.length ? segments : null;
    };
    const isEmptyWebVttSegment = (body: string): boolean => {
      const normalized = body.replace(/^\uFEFF/, "").replace(/\r/g, "");
      return /^WEBVTT(?:[ \t].*)?(?:\n|$)/.test(normalized) && !normalized.includes("-->");
    };
    const playlistMemberUrls = new Set<string>();
    const readCaptionResource = async (url: string): Promise<ResourceRead> => {
      const response = await fetchCaptionResponse(url);
      if ("failure" in response) return { cues: [], failure: "fetch" };
      const playlist = isPlaylistUrl(response.finalUrl);
      if (!playlist) {
        const cues = parseTimedText(response.body);
        return cues.length ? { cues } : { cues: [], failure: "parse" };
      }
      const segments = playlistSegments(response.body, response.finalUrl);
      if (!segments) return { cues: [], failure: "parse" };
      segments.forEach((segment) => playlistMemberUrls.add(segment));
      const merged: LocalCue[] = [];
      let totalSegmentChars = 0;
      for (const segment of segments) {
        const segmentResponse = await fetchCaptionResponse(segment);
        if ("failure" in segmentResponse) return { cues: [], failure: "fetch" };
        totalSegmentChars += segmentResponse.body.length;
        if (totalSegmentChars > MAX_TOTAL_SEGMENT_CHARS) return { cues: [], failure: "fetch" };
        const cues = parseTimedText(segmentResponse.body);
        if (!cues.length) {
          if (isEmptyWebVttSegment(segmentResponse.body)) continue;
          return { cues: [], failure: "parse" };
        }
        merged.push(...cues);
        if (merged.length > MAX_PLAYLIST_CUES) return { cues: [], failure: "parse" };
      }
      const cues = dedupeCues(merged);
      return cues.length ? { cues } : { cues: [], failure: "parse" };
    };
    const candidates: Candidate[] = [];
    const urls = new Set<string>();
    let sawStandalonePlaylistSegment = false;
    const addUrl = (value: unknown, language = "und") => {
      if (typeof value !== "string") return;
      const trusted = trustedCaptionUrl(value.replace(/\\\//g, "/").replace(/&amp;/g, "&"));
      if (!trusted || urls.has(trusted)) return;
      if (isPlaylistSegmentUrl(trusted)) {
        sawStandalonePlaylistSegment = true;
        return;
      }
      urls.add(trusted);
      candidates.push({ url: trusted, language: trackLanguage(language) });
    };
    const inlineCandidates: Candidate[] = [];
    const addInline = (value: unknown, language = "und") => {
      if (!Array.isArray(value)) return;
      const cues = normalizeCues(value.flatMap((raw) => {
        if (!raw || typeof raw !== "object") return [];
        const record = raw as Record<string, unknown>;
        const start = Number(record.start_sec ?? record.startTime ?? record.start ?? record.begin);
        const end = Number(record.end_sec ?? record.endTime ?? record.end);
        const text = typeof record.text === "string" ? record.text : typeof record.content === "string" ? record.content : "";
        return Number.isFinite(start) && Number.isFinite(end) ? [{ start_sec: start, end_sec: end, text }] : [];
      }));
      if (cues.length) inlineCandidates.push({ language: trackLanguage(language), cues });
    };
    const domTracks = [...queryAll("track[src]"), ...queryAll("track")].filter((node, index, nodes) => nodes.indexOf(node) === index);
    domTracks.forEach((node) => {
      const track = node as HTMLTrackElement & { track?: TextTrack };
      if (!isCaptionKind(track.kind || attribute(node, "kind"))) return;
      const language = trackLanguage(track.srclang || attribute(node, "srclang"));
      if (track.track) {
        const cues = readNativeCues(track.track);
        if (cues.length) inlineCandidates.push({ language: trackLanguage(track.track.language || language), cues });
      }
      addUrl(track.src || attribute(node, "src"), language);
    });

    const playerRoots: unknown[] = ["kWidget", "kalturaPlayer", "KalturaPlayer", "__INITIAL_STATE__", "__INITIAL_DATA__"]
      .map(readPageValue);
    const inspectDescriptor = (value: unknown, keyHint = "", depth = 0): void => {
      if (depth > 3 || value === null || value === undefined) return;
      if (typeof value === "string") {
        if (/caption|subtitle|timedtext|texttrack|track|src|url|href/i.test(keyHint)) addUrl(value);
        return;
      }
      if (Array.isArray(value)) { value.slice(0, 80).forEach((item) => inspectDescriptor(item, keyHint, depth + 1)); return; }
      if (typeof value !== "object") return;
      const record = value as Record<string, unknown>;
      let language = "und";
      try { language = trackLanguage(record.languageCode ?? record.language ?? record.lang ?? record.srclang); } catch { /* Use und. */ }
      if (/caption|subtitle|timedtext|texttrack|^text$|getTextTracks|getCaptionTracks/i.test(keyHint)) {
        try { addInline(record.cues ?? record.events, language); } catch { /* Skip malformed inline cues. */ }
      }
      try {
        for (const key of Object.keys(record).slice(0, 80)) {
          try {
            const nested = record[key];
            if (typeof nested === "string" && /caption|subtitle|timedtext|texttrack|track|src|url|href/i.test(key)) addUrl(nested, language);
            else if (depth < 2 && (Array.isArray(nested) || (nested && typeof nested === "object"))) inspectDescriptor(nested, key, depth + 1);
          } catch { /* Skip only the throwing property. */ }
        }
      } catch { /* A player-owned getter must not abort other caption sources. */ }
    };
    const addPlayerTracks = (root: unknown) => {
      if (!root || (typeof root !== "object" && typeof root !== "function")) return;
      const holder = root as Record<string, unknown>;
      for (const method of ["getTextTracks", "getTracks", "getCaptionTracks"]) {
        try {
          const fn = holder[method];
          if (typeof fn === "function") {
            const value = (fn as () => unknown).call(root);
            inspectDescriptor(value, method);
          }
        } catch { /* Player APIs differ by Kaltura version. */ }
      }
      for (const key of ["textTracks", "tracks", "captionTracks", "subtitles", "captions"]) {
        try {
          const value = holder[key];
          if (Array.isArray(value)) value.forEach((track) => inspectDescriptor(track, key));
        } catch { /* A player-owned getter must not abort other caption sources. */ }
      }
    };
    playerRoots.forEach(addPlayerTracks);
    playerRoots.forEach((root) => inspectDescriptor(root));
    resources.forEach((value) => addUrl(value));
    for (const match of pageText.matchAll(/https?:\\?\/\\?\/[^"'<>\s]+/gi)) addUrl(match[0]);
    const playlistCandidates = candidates
      .filter((candidate) => candidate.url && isPlaylistUrl(candidate.url))
      .map((candidate) => candidate.language === "und" && nativeCapture?.language
        ? { ...candidate, language: nativeCapture.language }
        : candidate);
    const resourceCandidates = candidates.filter((candidate) => !candidate.url || !isPlaylistUrl(candidate.url));
    const nativeCandidates = nativeCapture?.cues?.length ? [nativeCapture] : [];
    // Native/DOM cues on an HLS-backed TextTrack may contain only the currently
    // buffered window. A discovered finite playlist is the only source that
    // can prove every segment was read, so do not fall back to partial in-memory
    // cues when that playlist exists but fails.
    const allCandidates = playlistCandidates.length
      ? [...playlistCandidates, ...resourceCandidates]
      : [...nativeCandidates, ...inlineCandidates, ...resourceCandidates];
    const makeCapture = (cues: LocalCue[], language: string, title: string | null, duration: number | null): PageCapture => {
      const pageUrl = isNtuMediaHost
        ? `https://${frameHost}${location.pathname}` : `https://ntulearnvideo.ntu.edu.sg/media/${entryId}`;
      return {
        platform: "ntu_kaltura",
        platform_id: entryId,
        canonical_url: `https://ntulearnvideo.ntu.edu.sg/media/${entryId}`,
        page_url: pageUrl,
        metadata: { title, author: null, duration_sec: duration, language, description: null, cover_url: null, tags: [], chapters: [] },
        caption: { status: "available", source: "official_cc", language, cues },
      };
    };
    if (!allCandidates.length && !nativeTracks.length) {
      if (sawStandalonePlaylistSegment) return fail("caption_parse_failed");
      return {
        status: "no_caption",
        capture: {
          platform: "ntu_kaltura",
          platform_id: entryId,
          canonical_url: `https://ntulearnvideo.ntu.edu.sg/media/${entryId}`,
          page_url: isNtuMediaHost
            ? `https://${frameHost}${location.pathname}` : `https://ntulearnvideo.ntu.edu.sg/media/${entryId}`,
          metadata: { title: null, author: null, duration_sec: null, language: null, description: null, cover_url: null, tags: [], chapters: [] },
          caption: { status: "unavailable", source: null, language: null, cues: [] },
        },
      };
    }
    const errors: SafeCaptureError[] = [];
    for (const candidate of allCandidates) {
      if (candidate.cues?.length) return {
        status: "captured",
        capture: makeCapture(candidate.cues, trackLanguage(candidate.language), null, candidate.duration ?? null),
      };
      if (!candidate.url) continue;
      if (playlistMemberUrls.has(candidate.url)) continue;
      const resource = await readCaptionResource(candidate.url);
      if (resource.failure) {
        errors.push(resource.failure === "fetch" ? "caption_fetch_failed" : "caption_parse_failed");
        continue;
      }
      const cues = resource.cues;
      const language = trackLanguage(candidate.language);
      const video = queryOne("video") as HTMLVideoElement | null;
      const durationValue = Number(video?.duration);
      const metaTitle = (queryOne("meta[property='og:title']") as HTMLMetaElement | null)?.content;
      const title = [metaTitle, queryOne("h1")?.textContent, typeof document.title === "string" ? document.title : ""]
        .map((value) => clean(value ?? ""))
        .find((value) => value && !/^(media gallery|kaltura(?: media)? player)$/i.test(value)) ?? `NTULearn video ${entryId}`;
      return { status: "captured", capture: makeCapture(cues, language, title.slice(0, 1000), Number.isFinite(durationValue) && durationValue >= 0 ? Math.floor(durationValue) : null) };
    }
    return fail(errors.includes("caption_fetch_failed") ? "caption_fetch_failed" : "caption_parse_failed");
  } catch {
    return fail("capture_unavailable");
  }
}
