import { runInNewContext } from "node:vm";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  captureActivePage,
  captureKalturaPage,
  captureYouTubePage,
  selectBestCapture,
  type CaptureAttempt,
} from "./page-capture.js";
import type { Cue, PageCapture } from "./protocol.js";

const YOUTUBE_ID = "abcdefghijk";
const OTHER_YOUTUBE_ID = "zyxwvutsrqp";
const ENTRY_ID = "52_6ce010d";

function availableCapture(platform: "youtube" | "ntu_kaltura", cues: Cue[] = [{ start_sec: 1, end_sec: 2, text: "cue" }]): PageCapture {
  const canonical = platform === "youtube"
    ? `https://www.youtube.com/watch?v=${YOUTUBE_ID}`
    : `https://ntulearnvideo.ntu.edu.sg/media/${ENTRY_ID}`;
  return {
    platform,
    platform_id: platform === "youtube" ? YOUTUBE_ID : ENTRY_ID,
    canonical_url: canonical,
    page_url: canonical,
    metadata: {
      title: platform === "youtube" ? "A video" : "NTULearn lesson",
      author: null,
      duration_sec: 120,
      language: "en",
      description: null,
      cover_url: null,
      tags: [],
      chapters: [],
    },
    caption: { status: "available", source: "official_cc", language: "en", cues },
  };
}

function unavailableCapture(title = "Media Gallery", duration: number | null = null): PageCapture {
  return {
    platform: "ntu_kaltura",
    platform_id: ENTRY_ID,
    canonical_url: `https://ntulearnvideo.ntu.edu.sg/media/${ENTRY_ID}`,
    page_url: "https://ntulearn.ntu.edu.sg/ultra/courses/example",
    metadata: {
      title,
      author: null,
      duration_sec: duration,
      language: null,
      description: null,
      cover_url: null,
      tags: [],
      chapters: [],
    },
    caption: { status: "unavailable", source: null, language: null, cues: [] },
  };
}

function youtubeResponse(
  videoId = YOUTUBE_ID,
  tracks: Array<Record<string, unknown>> = [],
  renderer: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    videoDetails: { videoId, title: "Current title", author: "Author", lengthSeconds: "120" },
    captions: { playerCaptionsTracklistRenderer: { ...renderer, captionTracks: tracks } },
  };
}

function json3(text = "Hello", startMs = 1000): { events: Array<Record<string, unknown>> } {
  return { events: [{ tStartMs: startMs, dDurationMs: 1500, segs: [{ utf8: text }] }] };
}

type KalturaFixture = {
  html?: string;
  href?: string;
  title?: string;
  tracks?: Array<Record<string, unknown>>;
  videos?: Array<Record<string, unknown>>;
  resources?: string[];
};

function stubKaltura(fixture: KalturaFixture = {}): void {
  const tracks = fixture.tracks ?? [];
  const videos = fixture.videos ?? [];
  const html = fixture.html ?? `entry_id=${ENTRY_ID}`;
  const href = fixture.href ?? `https://cdnapisec.kaltura.com/p/123/embed?entry_id=${ENTRY_ID}&ks=private`;
  const parsed = new URL(href);
  const documentStub = {
    documentElement: { innerHTML: html, lang: "fr" },
    title: fixture.title ?? "Kaltura Player",
    querySelectorAll: (selector: string) => {
      if (selector === "track[src]" || selector === "track") return tracks;
      if (selector === "video") return videos;
      return [];
    },
    querySelector: (selector: string) => {
      if (selector === "meta[property='og:title']") return null;
      if (selector === "h1") return null;
      if (selector === "video") return videos[0] ?? null;
      return null;
    },
  };
  vi.stubGlobal("document", documentStub as unknown as Document);
  vi.stubGlobal("location", {
    href,
    hostname: parsed.hostname,
    pathname: parsed.pathname,
  });
  vi.stubGlobal("performance", { getEntriesByType: () => (fixture.resources ?? []).map((name) => ({ name })) });
}

function recreateAdapter(
  adapter: () => Promise<CaptureAttempt>,
  context: Record<string, unknown>,
): () => Promise<CaptureAttempt> {
  return runInNewContext(`(${adapter.toString()})`, context) as () => Promise<CaptureAttempt>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("capture selection and coordinator", () => {
  it("prefers a valid caption capture over a generic shell", () => {
    const selected = selectBestCapture([unavailableCapture(), availableCapture("ntu_kaltura")]);
    expect(selected?.caption.status).toBe("available");
  });

  it("prefers a concrete player frame when both results have no captions", () => {
    const selected = selectBestCapture([unavailableCapture(), unavailableCapture("NTULearn video", 3600)]);
    expect(selected?.metadata.title).toBe("NTULearn video");
    expect(selected?.metadata.duration_sec).toBe(3600);
  });

  it("breaks equal-score ties independently of frame result order", () => {
    const first = availableCapture("ntu_kaltura");
    const second = availableCapture("ntu_kaltura");
    first.metadata.title = "Zulu lecture";
    second.metadata.title = "Alpha lecture";

    expect(selectBestCapture([first, second])).toEqual(selectBestCapture([second, first]));
  });

  it("routes YouTube to the top frame and aggregates a structured result", async () => {
    const executeScript = vi.fn(async (options: { target: { tabId: number; allFrames?: boolean } }) => {
      expect(options.target).toEqual({ tabId: 7, allFrames: false });
      return [{ result: { status: "captured", capture: availableCapture("youtube") } }];
    });
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(7, `https://www.youtube.com/watch?v=${YOUTUBE_ID}`);

    expect(result.platform).toBe("youtube");
    expect(executeScript).toHaveBeenCalledOnce();
  });

  it("runs Kaltura in all frames and lets a valid frame beat a failed frame", async () => {
    const executeScript = vi.fn(async (options: { target: { tabId: number; allFrames?: boolean } }) => {
      expect(options.target).toEqual({ tabId: 8, allFrames: true });
      return [
        { result: { status: "failed", error: "capture_unavailable" } },
        { result: { status: "captured", capture: availableCapture("ntu_kaltura") } },
      ];
    });
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(8, "https://ntulearn.ntu.edu.sg/ultra/courses/example");

    expect(result.caption.status).toBe("available");
    expect(executeScript).toHaveBeenCalledOnce();
  });

  it("ignores a browser-level frame error when another frame captured subtitles", async () => {
    const executeScript = vi.fn(async () => [
      { frameId: 1, error: "Frame URL contained ks=private-signature" },
      { frameId: 2, result: { status: "captured", capture: availableCapture("ntu_kaltura") } },
    ]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(8, "https://ntulearn.ntu.edu.sg/ultra/courses/example");

    expect(result.caption.status).toBe("available");
    expect(JSON.stringify(result)).not.toContain("private-signature");
  });

  it("surfaces a frame's caption read failure instead of returning unavailable", async () => {
    const executeScript = vi.fn(async () => [
      { result: { status: "no_caption", capture: unavailableCapture("NTULearn video") } },
      { result: { status: "failed", error: "caption_fetch_failed" } },
    ]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(captureActivePage(8, "https://ntulearnvideo.ntu.edu.sg/media/52_6ce010d")).rejects.toThrow("caption_fetch_failed");
  });

  it("falls back to a top-frame invocation if all-frame injection is rejected", async () => {
    const executeScript = vi.fn()
      .mockRejectedValueOnce(new Error("inaccessible frame"))
      .mockResolvedValueOnce([{ result: { status: "no_caption", capture: unavailableCapture("NTULearn video") } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(8, "https://ntulearnvideo.ntu.edu.sg/media/52_6ce010d");

    expect(result.caption.status).toBe("unavailable");
    expect(executeScript).toHaveBeenCalledTimes(2);
    expect(executeScript.mock.calls[1]?.[0].target).toEqual({ tabId: 8, allFrames: false });
  });

  it("rejects unsupported hosts, malformed results, and secret-bearing public URLs", async () => {
    await expect(captureActivePage(1, "http://www.youtube.com/watch?v=abcdefghijk")).rejects.toThrow("unsupported_page");
    await expect(captureActivePage(1, "https://example.com/video")).rejects.toThrow("unsupported_page");

    const secret = availableCapture("ntu_kaltura");
    secret.canonical_url += "?ks=private-signature";
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "captured", capture: secret } }]) } });
    await expect(captureActivePage(1, "https://ntulearnvideo.ntu.edu.sg/media/52_6ce010d")).rejects.toThrow("capture_unavailable");
  });

  it("rejects page-world captures that violate the backend runtime contract", async () => {
    const invalidId = availableCapture("ntu_kaltura") as PageCapture;
    invalidId.platform_id = "entry_id";
    invalidId.canonical_url = "https://ntulearnvideo.ntu.edu.sg/media/entry_id";
    const unsorted = availableCapture("youtube", [
      { start_sec: 2, end_sec: 3, text: "later" },
      { start_sec: 1, end_sec: 2, text: "earlier" },
    ]);
    const malformedChapter = availableCapture("youtube") as PageCapture;
    malformedChapter.metadata.chapters = [{ title: "bad", start_sec: 2, end_sec: 1 }];
    const values = [invalidId, unsorted, malformedChapter];
    const executeScript = vi.fn(async () => [{ result: { status: "captured", capture: values.shift() } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    for (let index = 0; index < 3; index += 1) {
      await expect(captureActivePage(1, `https://www.youtube.com/watch?v=${YOUTUBE_ID}`)).rejects.toThrow("capture_unavailable");
    }
  });

  it("rebuilds accepted captures from an allowlist and drops secret-bearing extras", async () => {
    const capture = availableCapture("ntu_kaltura") as PageCapture & Record<string, unknown>;
    capture.ks = "private-signature";
    (capture.metadata as PageCapture["metadata"] & Record<string, unknown>).authorization = "Bearer private";
    (capture.caption as PageCapture["caption"] & Record<string, unknown>).caption_url = "https://cfvod.kaltura.com/caption/file.vtt?ks=private";
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "captured", capture } }]) } });

    const result = await captureActivePage(1, "https://ntulearnvideo.ntu.edu.sg/media/example");
    const serialized = JSON.stringify(result);

    expect(result.caption.status).toBe("available");
    expect(serialized).not.toContain("private");
    expect(serialized).not.toContain("authorization");
    expect(serialized).not.toContain("caption_url");
  });

  it("turns a throwing injection result into a stable safe error", async () => {
    const injectionResult = Object.defineProperty({}, "result", {
      get: () => { throw new Error("ks=private-signature"); },
    });
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [injectionResult]) } });

    await expect(captureActivePage(1, `https://www.youtube.com/watch?v=${YOUTUBE_ID}`)).rejects.toThrow("capture_unavailable");
  });

  it("routes supported watch, shorts, and short-link shapes only", async () => {
    const executeScript = vi.fn(async () => [{ result: { status: "captured", capture: availableCapture("youtube") } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    for (const url of [
      `https://www.youtube.com/watch?v=${YOUTUBE_ID}`,
      `https://www.youtube.com/shorts/${YOUTUBE_ID}`,
      `https://youtu.be/${YOUTUBE_ID}?si=tracking-value`,
    ]) {
      await expect(captureActivePage(7, url)).resolves.toMatchObject({ platform_id: YOUTUBE_ID });
    }
    await expect(captureActivePage(7, `https://www.youtube.com/embed/${YOUTUBE_ID}`)).rejects.toThrow("unsupported_page");
    expect(executeScript).toHaveBeenCalledTimes(3);
  });

  it("routes the exact ntulearnv1 media origin through all Kaltura frames", async () => {
    const capture = availableCapture("ntu_kaltura");
    capture.page_url = `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`;
    const executeScript = vi.fn(async (options: { target: { tabId: number; allFrames?: boolean } }) => {
      expect(options.target).toEqual({ tabId: 9, allFrames: true });
      return [{ result: { status: "captured", capture } }];
    });
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(9, `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}?ks=private-signature`);

    expect(result.platform).toBe("ntu_kaltura");
    expect(result.page_url).toBe(`https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`);
    expect(executeScript).toHaveBeenCalledOnce();
  });

  it("does not accept ntulearnv1 as a replacement canonical media origin", async () => {
    const capture = availableCapture("ntu_kaltura");
    capture.canonical_url = `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`;
    capture.page_url = `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`;
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "captured", capture } }]) } });

    await expect(captureActivePage(9, `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`)).rejects.toThrow("capture_unavailable");
  });

  it("rejects near-hosts instead of treating them as NTU media pages", async () => {
    const executeScript = vi.fn();
    vi.stubGlobal("chrome", { scripting: { executeScript } });
    for (const host of [
      "ntulearnv10.ntu.edu.sg",
      "media.ntulearnv1.ntu.edu.sg",
      "ntulearnv1.ntu.edu.sg.evil.example",
    ]) {
      await expect(captureActivePage(9, `https://${host}/media/${ENTRY_ID}`)).rejects.toThrow("unsupported_page");
    }
    expect(executeScript).not.toHaveBeenCalled();
  });

  it("maps all empty Kaltura frames to the entry-missing error", async () => {
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "not_media_frame" } }, { result: { status: "not_media_frame" } }]) } });
    await expect(captureActivePage(2, "https://ntulearn.ntu.edu.sg/ultra/courses/example")).rejects.toThrow("kaltura_entry_missing");
  });
});

describe("YouTube caption adapter", () => {
  function stubYouTube(href = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`): void {
    vi.stubGlobal("location", { href, hostname: new URL(href).hostname, pathname: new URL(href).pathname });
    vi.stubGlobal("document", { title: "Current title - YouTube" });
  }

  it("uses the current player response and prefers manual captions over ASR", async () => {
    stubYouTube();
    const manual = "https://www.youtube.com/api/timedtext?lang=en&kind=manual";
    const auto = "https://www.youtube.com/api/timedtext?lang=en&kind=asr";
    vi.stubGlobal("movie_player", { getPlayerResponse: () => youtubeResponse(YOUTUBE_ID, [
      { kind: "asr", languageCode: "en", baseUrl: auto },
      { languageCode: "en", baseUrl: manual },
    ]) });
    const fetchStub = vi.fn(async () => ({ ok: true, json: async () => json3("manual <b>caption</b>") }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.source).toBe("official_cc");
      expect(result.capture.caption.cues[0]?.text).toBe("manual caption");
      expect(result.capture.metadata.title).toBe("Current title");
    }
    expect(fetchStub).toHaveBeenCalledWith(`${manual}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
  });

  it("honors the current player track before the response default and fallback order", async () => {
    stubYouTube();
    const tracks = [
      { languageCode: "ar", baseUrl: "https://www.youtube.com/api/timedtext?lang=ar" },
      { languageCode: "zh-CN", baseUrl: "https://www.youtube.com/api/timedtext?lang=zh-CN" },
      { languageCode: "fr", baseUrl: "https://www.youtube.com/api/timedtext?lang=fr" },
    ];
    vi.stubGlobal("movie_player", {
      getPlayerResponse: () => youtubeResponse(YOUTUBE_ID, tracks, {
        audioTracks: [{ defaultCaptionTrackIndex: 1 }],
      }),
      getOption: () => ({ languageCode: "fr" }),
    });
    const fetchStub = vi.fn(async () => ({ ok: true, json: async () => json3("français") }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("fr");
    expect(fetchStub).toHaveBeenCalledWith(`${tracks[2]!.baseUrl}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
  });

  it("uses a valid audio-track default before the first manual language", async () => {
    stubYouTube();
    const tracks = [
      { languageCode: "ar", baseUrl: "https://www.youtube.com/api/timedtext?lang=ar" },
      { languageCode: "zh-CN", baseUrl: "https://www.youtube.com/api/timedtext?lang=zh-CN" },
    ];
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, tracks, {
      audioTracks: [{ defaultCaptionTrackIndex: 1 }],
    }));
    const fetchStub = vi.fn(async () => ({ ok: true, json: async () => json3("默认字幕") }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("zh-CN");
    expect(fetchStub).toHaveBeenCalledWith(`${tracks[1]!.baseUrl}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
  });

  it("reads a JSON player_response string from the current player config", async () => {
    stubYouTube();
    vi.stubGlobal("ytplayer", { config: { args: { player_response: JSON.stringify(youtubeResponse(YOUTUBE_ID, [{ languageCode: "zh-Hans", baseUrl: "https://www.youtube.com/api/timedtext?lang=zh" }])) } } });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => json3("你好") })));

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("zh-Hans");
  });

  it("falls back from an unreadable manual track to a working ASR track", async () => {
    stubYouTube();
    const manual = "https://www.youtube.com/api/timedtext?kind=manual";
    const auto = "https://www.youtube.com/api/timedtext?kind=asr";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [
      { languageCode: "en", baseUrl: manual },
      { kind: "asr", languageCode: "en", baseUrl: auto },
    ]));
    const fetchStub = vi.fn(async (url: string) => url.includes("manual")
      ? { ok: false, json: async () => ({}) }
      : { ok: true, json: async () => json3("automatic") });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.source).toBe("auto_caption");
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("rejects a successful caption response that resolves after SPA navigation", async () => {
    const locationState = {
      href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`,
      hostname: "www.youtube.com",
      pathname: "/watch",
    };
    vi.stubGlobal("location", locationState);
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => {
        locationState.href = `https://www.youtube.com/watch?v=${OTHER_YOUTUBE_ID}`;
        return json3("old video caption");
      },
    })));

    await expect(captureYouTubePage()).resolves.toEqual({
      status: "failed",
      error: "stale_player_response",
    });
  });

  it("falls back from an unreadable JSON3 response to WebVTT on the same trusted track", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => { throw new SyntaxError("empty body"); } }
      : { ok: true, text: async () => "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nFallback caption" });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.language).toBe("en");
      expect(result.capture.caption.cues).toEqual([{ start_sec: 1, end_sec: 3, text: "Fallback caption" }]);
    }
    expect(fetchStub).toHaveBeenNthCalledWith(1, `${baseUrl}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
    expect(fetchStub).toHaveBeenNthCalledWith(2, `${baseUrl}&fmt=vtt`, expect.objectContaining({ credentials: "include" }));
  });

  it("falls back through empty VTT to the original YouTube XML transcript", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      if (url.includes("fmt=vtt")) return { ok: true, text: async () => "" };
      return { ok: true, text: async () => "<?xml version=\"1.0\"?><transcript><text start=\"2.5\" dur=\"1.25\">Raw &amp; valid</text></transcript>" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 2.5, end_sec: 3.75, text: "Raw & valid" }]);
    }
    expect(fetchStub).toHaveBeenNthCalledWith(3, baseUrl, expect.objectContaining({ credentials: "include" }));
  });

  it("falls back to the current-video official transcript endpoint when timed-text formats are empty", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`video:${YOUTUBE_ID}`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    const fetchStub = vi.fn(async (url: string, init?: RequestInit) => {
      void init;
      if (url.includes("/youtubei/v1/get_transcript")) return {
        ok: true,
        url: "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        json: async () => ({ actions: [{ transcriptSegmentRenderer: { startMs: "1000", endMs: "2500", snippet: { runs: [{ text: "Official transcript cue" }] } } }] }),
      };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 1, end_sec: 2.5, text: "Official transcript cue" }]);
    }
    expect(fetchStub).toHaveBeenCalledTimes(4);
    expect(fetchStub.mock.calls[3]?.[0]).toContain("/youtubei/v1/get_transcript");
    expect(fetchStub.mock.calls[3]?.[1]).toEqual(expect.objectContaining({
      method: "POST",
      credentials: "include",
      headers: expect.objectContaining({ "X-YouTube-Client-Version": "1.2.3" }),
    }));
  });

  it("decodes percent-encoded transcript params before binding them to the current video", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = encodeURIComponent(Buffer.from(`video:${YOUTUBE_ID}`).toString("base64"));
    expect(params).toContain("%3D");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("/youtubei/v1/get_transcript")) return {
        ok: true,
        url: "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        json: async () => ({ actions: [{ transcriptSegmentRenderer: { startMs: "500", endMs: "1500", snippet: { simpleText: "Encoded params cue" } } }] }),
      };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 0.5, end_sec: 1.5, text: "Encoded params cue" }]);
    }
    const transcriptCall = fetchStub.mock.calls.find(([url]) => String(url).includes("/youtubei/v1/get_transcript"));
    expect(transcriptCall).toBeDefined();
    const transcriptInit = (transcriptCall as unknown as [string, RequestInit?] | undefined)?.[1];
    expect(JSON.parse(String(transcriptInit?.body))).toEqual(expect.objectContaining({
      params: Buffer.from(`video:${YOUTUBE_ID}`).toString("base64"),
    }));
  });

  it("binds a real nested length-delimited protobuf transcript param to the current video", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const videoBytes = Array.from(YOUTUBE_ID, (character) => character.charCodeAt(0));
    const nestedMessage = [0x0a, videoBytes.length, ...videoBytes];
    const protobuf = Uint8Array.from([0x0a, nestedMessage.length, ...nestedMessage]);
    const params = Buffer.from(protobuf).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("/youtubei/v1/get_transcript")) return {
        ok: true,
        url: "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        json: async () => ({ actions: [{ transcriptSegmentRenderer: { startMs: "750", endMs: "1750", snippet: { simpleText: "Protobuf-bound cue" } } }] }),
      };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 0.75, end_sec: 1.75, text: "Protobuf-bound cue" }]);
    }
    const transcriptCall = fetchStub.mock.calls.find(([url]) => String(url).includes("/youtubei/v1/get_transcript"));
    const transcriptInit = (transcriptCall as unknown as [string, RequestInit?] | undefined)?.[1];
    expect(JSON.parse(String(transcriptInit?.body))).toEqual(expect.objectContaining({ params }));
  });

  it("does not call the transcript endpoint for malformed percent-encoded params", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params: "%not-valid" } });
    vi.stubGlobal("ytcfg", { get: vi.fn(() => "configured") });
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(fetchStub.mock.calls.some(([url]) => String(url).includes("/youtubei/v1/get_transcript"))).toBe(false);
  });

  it("uses YouTube's rendered transcript UI when direct caption endpoints reject the request", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en&kind=asr";
    const params = encodeURIComponent(Buffer.from(`video:${YOUTUBE_ID}`).toString("base64"));
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ kind: "asr", languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ clickTrackingParams: "tracking", getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    let transcriptVisible = false;
    const expandClick = vi.fn();
    const hiddenTranscriptClick = vi.fn();
    const transcriptClick = vi.fn(() => { transcriptVisible = true; });
    const hiddenTranscriptButton = { click: hiddenTranscriptClick, getClientRects: () => [] };
    const visibleTranscriptButton = { click: transcriptClick, getClientRects: () => [{ width: 100, height: 30 }] };
    const segments = [
      { data: { startMs: "0", endMs: "1000", snippet: { runs: [{ text: "First DOM cue" }] } } },
      { data: { startMs: "0", endMs: "1000", snippet: { runs: [{ text: "First DOM cue" }] } } },
      { data: { startMs: "1000", endMs: "2500", snippet: { simpleText: "Second DOM cue" } } },
    ];
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: () => null,
      querySelector: (selector: string) => {
        if (selector.includes("#expand")) return { click: expandClick };
        if (selector === "ytd-video-description-transcript-section-renderer button") return hiddenTranscriptButton;
        return null;
      },
      querySelectorAll: (selector: string) => {
        if (selector === "ytd-video-description-transcript-section-renderer button") {
          return [hiddenTranscriptButton, visibleTranscriptButton];
        }
        return selector === "ytd-transcript-segment-renderer" && transcriptVisible ? segments : [];
      },
    });
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("/youtubei/v1/get_transcript")) return { ok: false, status: 400 };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption).toEqual({
        status: "available",
        source: "auto_caption",
        language: "en",
        cues: [
          { start_sec: 0, end_sec: 1, text: "First DOM cue" },
          { start_sec: 1, end_sec: 2.5, text: "Second DOM cue" },
        ],
      });
    }
    expect(expandClick).toHaveBeenCalledOnce();
    expect(transcriptClick).toHaveBeenCalledOnce();
    expect(hiddenTranscriptClick).not.toHaveBeenCalled();
  });

  it("immediately consumes an already-open rendered transcript without clicking the page", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    const click = vi.fn();
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: () => null,
      querySelector: () => ({ click }),
      querySelectorAll: (selector: string) => selector === "ytd-transcript-segment-renderer"
        ? [{ data: { startMs: "250", endMs: "1250", snippet: { simpleText: "Already rendered" } } }]
        : [],
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" }));

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 0.25, end_sec: 1.25, text: "Already rendered" }]);
    }
    expect(click).not.toHaveBeenCalled();
  });

  it("abandons the rendered transcript fallback if YouTube navigates to another video", async () => {
    const locationState = {
      href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`,
      hostname: "www.youtube.com",
      pathname: "/watch",
    };
    vi.stubGlobal("location", locationState);
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`video:${YOUTUBE_ID}`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params } });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: {} },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: () => null,
      querySelector: (selector: string) => selector === "ytd-video-description-transcript-section-renderer button"
        ? { click: () => { locationState.href = `https://www.youtube.com/watch?v=${OTHER_YOUTUBE_ID}`; } }
        : null,
      querySelectorAll: () => [],
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : url.includes("/youtubei/v1/get_transcript")
        ? { ok: false, status: 400 }
        : { ok: true, text: async () => "" }));

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });
  });

  it("does not call the transcript endpoint when its opaque params belong to another video", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`video:${OTHER_YOUTUBE_ID}`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params } });
    vi.stubGlobal("ytcfg", { get: vi.fn(() => "configured") });
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(fetchStub.mock.calls.some(([url]) => String(url).includes("/youtubei/v1/get_transcript"))).toBe(false);
  });

  it("does not bind a transcript endpoint to an incidental video ID substring", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`unrelated-prefix:${YOUTUBE_ID}:suffix`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params } });
    vi.stubGlobal("ytcfg", { get: vi.fn(() => "configured") });
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(fetchStub.mock.calls.some(([url]) => String(url).includes("/youtubei/v1/get_transcript"))).toBe(false);
  });

  it("rejects stale responses after same-tab SPA navigation", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(OTHER_YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));

    const result = await captureYouTubePage();

    expect(result).toEqual({ status: "failed", error: "stale_player_response" });
  });

  it("does not trust an ID-less fallback when every identified response is stale", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(OTHER_YOUTUBE_ID));
    vi.stubGlobal("ytplayer", { config: { captions: { playerCaptionsTracklistRenderer: { captionTracks: [{ baseUrl: "https://www.youtube.com/api/timedtext" }] } } } });
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("does not let an ID-less fallback override an exact current response", async () => {
    stubYouTube();
    vi.stubGlobal("movie_player", { getPlayerResponse: () => youtubeResponse(YOUTUBE_ID) });
    vi.stubGlobal("ytplayer", { config: { captions: { playerCaptionsTracklistRenderer: { captionTracks: [{ baseUrl: "https://www.youtube.com/api/timedtext" }] } } } });
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("no_caption");
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("distinguishes no captions from a discovered but unparsable caption", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID));
    const noCaption = await captureYouTubePage();
    expect(noCaption.status).toBe("no_caption");

    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ events: [{ tStartMs: "bad", segs: [] }] }) })));
    const malformed = await captureYouTubePage();
    expect(malformed).toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("bounds hanging YouTube caption requests", async () => {
    vi.useFakeTimers();
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    let observedSignal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      observedSignal = init?.signal;
      return new Promise<Response>(() => undefined);
    }));

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(12_000);

    await expect(pending).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(observedSignal?.aborted).toBe(true);
  });

  it("bounds a response body that hangs after the caption headers arrive", async () => {
    vi.useFakeTimers();
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const cancel = vi.fn();
    const observedSignals: Array<AbortSignal | null | undefined> = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      observedSignals.push(init?.signal);
      return {
        ok: true,
        body: {
          getReader: () => ({
            read: async () => new Promise<never>(() => undefined),
            cancel,
            releaseLock: vi.fn(),
          }),
        },
      };
    }));

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(12_000);

    await expect(pending).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(observedSignals.length).toBe(3);
    expect(observedSignals.every((signal) => signal?.aborted)).toBe(true);
    expect(cancel).toHaveBeenCalled();
  });

  it("rejects oversized YouTube response bodies before parsing them", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const oversized = "x".repeat(4_000_001);
    const observedSignals: Array<AbortSignal | null | undefined> = [];
    const fetchStub = vi.fn(async (_url: string, init?: RequestInit) => {
      observedSignals.push(init?.signal);
      return { ok: true, text: async () => oversized };
    });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(observedSignals.every((signal) => signal?.aborted)).toBe(true);
  });

  it("cancels an oversized streamed YouTube response before parsing it", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const cancel = vi.fn();
    const oversized = new Uint8Array(4_000_001);
    const observedSignals: Array<AbortSignal | null | undefined> = [];
    const fetchStub = vi.fn(async (_url: string, init?: RequestInit) => {
      observedSignals.push(init?.signal);
      return {
        ok: true,
        body: {
          getReader: () => ({
            read: vi.fn()
              .mockResolvedValueOnce({ done: false, value: oversized })
              .mockResolvedValue({ done: true, value: undefined }),
            cancel,
            releaseLock: vi.fn(),
          }),
        },
      };
    });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(observedSignals.every((signal) => signal?.aborted)).toBe(true);
    expect(cancel).toHaveBeenCalled();
  });

  it("reads a bounded streamed YouTube response body", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const encoded = new TextEncoder().encode(JSON.stringify(json3("streamed cue")));
    let readCount = 0;
    const fetchStub = vi.fn(async () => ({
      ok: true,
      body: {
        getReader: () => ({
          read: async () => readCount++ === 0
            ? { done: false, value: encoded }
            : { done: true, value: undefined },
          releaseLock: vi.fn(),
        }),
      },
    }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("streamed cue");
    expect(fetchStub).toHaveBeenCalledOnce();
  });

  it("does not accept untrusted caption endpoints or invalid timings", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://evil.example/caption.vtt" }]));
    const untrusted = await captureYouTubePage();
    expect(untrusted).toEqual({ status: "failed", error: "caption_fetch_failed" });

    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ events: [{ tStartMs: -1, dDurationMs: 2, segs: [{ utf8: "bad" }] }] }) })));
    const invalid = await captureYouTubePage();
    expect(invalid).toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("rejects a caption request redirected outside trusted YouTube hosts", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      url: "https://evil.example/caption",
      json: async () => json3("must not be consumed"),
    })));

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
  });

  it("survives the MV3 function serialization boundary without module closures", async () => {
    const response = youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl: "https://www.youtube.com/api/timedtext?lang=en" }]);
    const adapter = recreateAdapter(captureYouTubePage, {
      Date,
      clearTimeout,
      URL,
      location: { href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`, hostname: "www.youtube.com", pathname: "/watch" },
      document: { title: "Serialized player - YouTube" },
      setTimeout,
      ytInitialPlayerResponse: response,
      fetch: async () => ({ ok: true, json: async () => json3("serialized cue") }),
    });

    const result = await adapter();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("serialized cue");
  });
});

describe("Kaltura caption adapter", () => {
  it("reads a top-level ntulearnv1 TextTrack and keeps its page URL secret-free", async () => {
    const track = { kind: "subtitles", mode: "disabled", language: "en", cues: [{ startTime: 1, endTime: 2, text: "Top-level subtitle" }] };
    stubKaltura({
      href: `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}?ks=private-signature`,
      videos: [{ textTracks: [track], duration: 20 }],
    });

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.page_url).toBe(`https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`);
      expect(result.capture.canonical_url).toBe(`https://ntulearnvideo.ntu.edu.sg/media/${ENTRY_ID}`);
      expect(JSON.stringify(result.capture)).not.toContain("private-signature");
    }
    expect(track.mode).toBe("disabled");
  });

  it("fetches a caption resource from the exact ntulearnv1 host only", async () => {
    const captionUrl = "https://ntulearnv1.ntu.edu.sg/caption/lesson.vtt?ks=private-signature";
    stubKaltura({
      href: `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`,
      tracks: [{ kind: "subtitles", src: captionUrl, srclang: "en" }],
    });
    const fetchStub = vi.fn(async () => ({
      ok: true,
      text: async () => "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nExact host caption\n",
    }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    expect(fetchStub).toHaveBeenCalledWith(captionUrl, expect.objectContaining({ credentials: "include" }));
    expect(JSON.stringify(result)).not.toContain("private-signature");
  });

  it("does not fetch caption resources from ntulearnv1 near-hosts", async () => {
    const fetchStub = vi.fn();
    stubKaltura({
      href: `https://ntulearnv1.ntu.edu.sg/media/${ENTRY_ID}`,
      tracks: [
        { kind: "subtitles", src: "https://ntulearnv10.ntu.edu.sg/caption/lesson.vtt", srclang: "en" },
        { kind: "subtitles", src: "https://media.ntulearnv1.ntu.edu.sg/caption/lesson.vtt", srclang: "en" },
        { kind: "subtitles", src: "https://ntulearnv1.ntu.edu.sg.evil.example/caption/lesson.vtt", srclang: "en" },
      ],
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("no_caption");
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("consumes a signed VTT locally and never returns the signed URL", async () => {
    const signedCaptionUrl = "https://cfvod.kaltura.com/caption/asset/caption.vtt?ks=private-signature";
    stubKaltura({ tracks: [{ src: signedCaptionUrl, srclang: "en" }], title: "Lecture 7" });
    const fetchStub = vi.fn(async () => ({ ok: true, text: async () => "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nWelcome to <b>the lecture</b>.\n" }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 1, end_sec: 3.5, text: "Welcome to the lecture." }]);
      expect(result.capture.metadata.title).toBe("Lecture 7");
      expect(JSON.stringify(result.capture)).not.toContain("private-signature");
      expect(JSON.stringify(result.capture)).not.toContain("ks=");
    }
    expect(fetchStub).toHaveBeenCalledWith(signedCaptionUrl, expect.objectContaining({ credentials: "omit" }));
  });

  it("reads native TextTrack cues and restores the prior mode", async () => {
    const track = { mode: "disabled", language: "fr", cues: [{ startTime: 1, endTime: 2, text: "Bonjour" }] };
    const video = { textTracks: [track], duration: 20 };
    stubKaltura({ videos: [video] });

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption).toEqual({ status: "available", source: "official_cc", language: "fr", cues: [{ start_sec: 1, end_sec: 2, text: "Bonjour" }] });
    expect(track.mode).toBe("disabled");
  });

  it("does not submit metadata text tracks as captions", async () => {
    const track = { kind: "metadata", mode: "disabled", language: "en", cues: [{ startTime: 1, endTime: 2, text: "analytics marker" }] };
    stubKaltura({ videos: [{ textTracks: [track], duration: 20 }] });

    const result = await captureKalturaPage();

    expect(result.status).toBe("no_caption");
    expect(track.mode).toBe("disabled");
  });

  it("parses SRT timestamps and continues after the first candidate fails", async () => {
    const first = "https://cfvod.kaltura.com/caption/first.srt";
    const second = "https://cfvod.kaltura.com/subtitle/second.srt";
    stubKaltura({ tracks: [{ src: first, srclang: "en" }, { src: second, srclang: "en" }] });
    const fetchStub = vi.fn(async (url: string) => url === first
      ? { ok: false, text: async () => "" }
      : { ok: true, text: async () => "1\n00:00:02,000 --> 00:00:04,000\nSecond line\n" });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues).toEqual([{ start_sec: 2, end_sec: 4, text: "Second line" }]);
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("parses TTML/DFXP with begin plus dur", async () => {
    const url = "https://cfvod.kaltura.com/caption/lesson.ttml";
    stubKaltura({ tracks: [{ src: url, srclang: "zh-Hans" }] });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, text: async () => "<tt><body><p begin=\"1s\" dur=\"2s\">你好 <b>世界</b></p></body></tt>" })));

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption).toEqual({ status: "available", source: "official_cc", language: "zh-Hans", cues: [{ start_sec: 1, end_sec: 3, text: "你好 世界" }] });
  });

  it("discovers player-exposed track descriptors", async () => {
    const url = "https://cfvod.kaltura.com/caption/player.vtt";
    stubKaltura();
    vi.stubGlobal("kalturaPlayer", { getTracks: () => [{ url, languageCode: "en" }] });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, text: async () => "WEBVTT\n\n00:00:00.500 --> 00:00:01.500\nFrom player state\n" })));

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("From player state");
  });

  it("discovers text descriptors inside the object returned by getTracks", async () => {
    const url = "https://cfvod.kaltura.com/caption/player-object.vtt";
    stubKaltura();
    vi.stubGlobal("kalturaPlayer", { getTracks: () => ({ text: [{ url, languageCode: "de" }] }) });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, text: async () => "WEBVTT\n\n00:00:00.500 --> 00:00:01.500\nAus Playerstatus\n" })));

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("de");
  });

  it("fails soft when a page-owned player getter throws", async () => {
    const url = "https://cfvod.kaltura.com/subtitle/fallback.vtt";
    stubKaltura({ resources: [url] });
    const player = {};
    Object.defineProperty(player, "tracks", { get: () => { throw new Error("ks=private"); } });
    vi.stubGlobal("kalturaPlayer", player);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, url, text: async () => "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nFallback\n" })));

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    expect(JSON.stringify(result)).not.toContain("private");
  });

  it("discovers an already loaded trusted caption resource", async () => {
    const url = "https://cfvod.kaltura.com/subtitle/performance.vtt?ks=private";
    stubKaltura({ resources: [url] });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, text: async () => "WEBVTT\n\n00:00:02.000 --> 00:00:03.000\nObserved resource\n" })));

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    expect(JSON.stringify(result)).not.toContain("private");
  });

  it("expands a Kaltura serveWebVTT playlist, applies timestamp maps, and deduplicates cues", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8?ks=private-signature`;
    const first = new URL("segmentIndex/1.vtt", playlist).toString();
    const second = new URL("segmentIndex/2.vtt", playlist).toString();
    stubKaltura({ resources: [playlist, first] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url === playlist) return { ok: true, url: playlist, text: async () => "#EXTM3U\n#EXTINF:2,\nsegmentIndex/1.vtt\n#EXTINF:2,\nsegmentIndex/2.vtt\n#EXT-X-ENDLIST\n" };
      if (url === first) return { ok: true, url: first, text: async () => "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000\n\n00:00:01.000 --> 00:00:02.000\nFirst\n" };
      if (url === second) return { ok: true, url: second, text: async () => "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:1080000,LOCAL:00:00:00.000\n\n00:00:01.000 --> 00:00:02.000\nSecond\n\n00:00:01.000 --> 00:00:02.000\nSecond\n" };
      return { ok: false, url, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues).toEqual([
      { start_sec: 11, end_sec: 12, text: "First" },
      { start_sec: 13, end_sec: 14, text: "Second" },
    ]);
    expect(fetchStub.mock.calls.map(([url]) => url)).toEqual([playlist, first, second]);
    expect(fetchStub.mock.calls.map((call) => (
      (call as unknown as [string, RequestInit])[1].credentials
    ))).toEqual(["omit", "omit", "omit"]);
    expect(JSON.stringify(result)).not.toContain("private-signature");
  });

  it("prefers a complete playlist over the native TextTrack buffered window", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    const first = new URL("segmentIndex/1.vtt", playlist).toString();
    const second = new URL("segmentIndex/2.vtt", playlist).toString();
    const nativeTrack = {
      kind: "subtitles",
      mode: "showing",
      language: "en",
      cues: [{ startTime: 10, endTime: 11, text: "Buffered only" }],
    };
    stubKaltura({ resources: [playlist, first], videos: [{ textTracks: [nativeTrack], duration: 30 }] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url === playlist) return { ok: true, url, text: async () => "#EXTM3U\nsegmentIndex/1.vtt\nsegmentIndex/2.vtt\n#EXT-X-ENDLIST\n" };
      if (url === first) return { ok: true, url, text: async () => "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nFull first\n" };
      return { ok: true, url: second, text: async () => "WEBVTT\n\n00:00:02.000 --> 00:00:03.000\nFull second\n" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.language).toBe("en");
      expect(result.capture.caption.cues).toEqual([
        { start_sec: 0, end_sec: 1, text: "Full first" },
        { start_sec: 2, end_sec: 3, text: "Full second" },
      ]);
    }
    expect(nativeTrack.mode).toBe("showing");
    expect(fetchStub.mock.calls.map(([url]) => url)).toEqual([playlist, first, second]);
  });

  it("rejects CSS and JavaScript pseudo-caption resources before fetching them", async () => {
    const css = "https://cfvod.kaltura.com/player/captions.css";
    const script = "https://cfvod.kaltura.com/player/captions-thumbRotator.js";
    stubKaltura({ resources: [css, script] });
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("no_caption");
    expect(fetchStub).not.toHaveBeenCalled();
    expect(JSON.stringify(result)).not.toContain("captions.css");
    expect(JSON.stringify(result)).not.toContain("thumbRotator");
  });

  it("rejects a live playlist without ENDLIST instead of returning buffered native cues", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/live.m3u8`;
    const nativeTrack = { kind: "subtitles", mode: "showing", language: "en", cues: [{ startTime: 0, endTime: 1, text: "Buffered partial" }] };
    stubKaltura({ resources: [playlist], videos: [{ textTracks: [nativeTrack], duration: 30 }] });
    const fetchStub = vi.fn(async (url: string) => ({
      ok: true,
      url,
      text: async () => "#EXTM3U\n#EXTINF:2,\nsegmentIndex/1.vtt\n",
    }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result).toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledOnce();
    expect(nativeTrack.mode).toBe("showing");
  });

  it("rejects malicious cross-origin or non-VTT playlist fragments", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8?ks=private`;
    stubKaltura({ resources: [playlist] });
    const fetchStub = vi.fn(async (url: string) => url === playlist
      ? { ok: true, url: playlist, text: async () => "#EXTM3U\nhttps://evil.example/caption/segment.vtt\n#EXT-X-ENDLIST\n" }
      : { ok: true, url, text: async () => "WEBVTT" });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result).toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledOnce();
    expect(JSON.stringify(result)).not.toContain("evil.example");
    expect(JSON.stringify(result)).not.toContain("private");
  });

  it("does not submit partial playlist captions when one segment fails", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    const first = new URL("chunks/first.vtt", playlist).toString();
    const second = new URL("chunks/second.vtt", playlist).toString();
    const nativeTrack = { kind: "subtitles", mode: "showing", language: "en", cues: [{ startTime: 0, endTime: 1, text: "Buffered partial" }] };
    stubKaltura({ resources: [playlist, first], videos: [{ textTracks: [nativeTrack], duration: 30 }] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url === playlist) return { ok: true, url, text: async () => "#EXTM3U\nchunks/first.vtt\nchunks/second.vtt\n#EXT-X-ENDLIST\n" };
      if (url === first) return { ok: true, url, text: async () => "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nPartial\n" };
      return { ok: false, url, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureKalturaPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(fetchStub.mock.calls.map(([url]) => url)).toEqual([playlist, first, second]);
    expect(nativeTrack.mode).toBe("showing");
  });

  it("does not submit a standalone serveWebVTT segment as a complete transcript", async () => {
    const segment = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/segmentIndex/1.vtt?ks=private`;
    stubKaltura({ resources: [segment] });
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result).toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).not.toHaveBeenCalled();
    expect(JSON.stringify(result)).not.toContain("private");
  });

  it("accepts valid empty WebVTT segments without dropping surrounding cues", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    const first = new URL("segmentIndex/1.vtt", playlist).toString();
    const empty = new URL("segmentIndex/2.vtt", playlist).toString();
    const last = new URL("segmentIndex/3.vtt", playlist).toString();
    stubKaltura({ resources: [playlist] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url === playlist) return { ok: true, url, text: async () => "#EXTM3U\nsegmentIndex/1.vtt\nsegmentIndex/2.vtt\nsegmentIndex/3.vtt\n#EXT-X-ENDLIST\n" };
      if (url === first) return { ok: true, url, text: async () => "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000\n\n00:00:00.000 --> 00:00:01.000\nBefore silence\n" };
      if (url === empty) return { ok: true, url, text: async () => "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:990000\n\nNOTE no spoken captions in this segment\n" };
      return { ok: true, url, text: async () => "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:1080000\n\n00:00:00.000 --> 00:00:01.000\nAfter silence\n" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureKalturaPage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues).toEqual([
      { start_sec: 10, end_sec: 11, text: "Before silence" },
      { start_sec: 12, end_sec: 13, text: "After silence" },
    ]);
    expect(fetchStub.mock.calls.map(([url]) => url)).toEqual([playlist, first, empty, last]);
  });

  it("rejects a malformed timestamp map instead of submitting earlier segments", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    const first = new URL("segmentIndex/1.vtt", playlist).toString();
    const malformed = new URL("segmentIndex/2.vtt", playlist).toString();
    stubKaltura({ resources: [playlist] });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === playlist) return { ok: true, url, text: async () => "#EXTM3U\nsegmentIndex/1.vtt\nsegmentIndex/2.vtt\n#EXT-X-ENDLIST\n" };
      if (url === first) return { ok: true, url, text: async () => "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nPartial\n" };
      return { ok: true, url: malformed, text: async () => "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000\n\n00:00:00.000 --> 00:00:01.000\nWrong offset\n" };
    }));

    await expect(captureKalturaPage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("bounds playlist segment count before making segment requests", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    const lines = Array.from({ length: 241 }, (_value, index) => `segmentIndex/${index + 1}.vtt`).join("\n");
    stubKaltura({ resources: [playlist] });
    const fetchStub = vi.fn(async (url: string) => ({ ok: true, url, text: async () => `#EXTM3U\n${lines}\n#EXT-X-ENDLIST\n` }));
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureKalturaPage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledOnce();
  });

  it("rejects an oversized playlist body before parsing or segment fetches", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    stubKaltura({ resources: [playlist] });
    const fetchStub = vi.fn(async (url: string) => ({ ok: true, url, text: async () => `${"#EXTM3U\n"}${"segmentIndex/1.vtt\n"}${"x".repeat(256_001)}` }));
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureKalturaPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(fetchStub).toHaveBeenCalledOnce();
  });

  it("bounds the aggregate size of otherwise valid empty segments", async () => {
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    const lines = Array.from({ length: 9 }, (_value, index) => `segmentIndex/${index + 1}.vtt`).join("\n");
    const emptyBody = `WEBVTT\nNOTE ${"x".repeat(499_987)}\n`;
    stubKaltura({ resources: [playlist] });
    const fetchStub = vi.fn(async (url: string) => url === playlist
      ? { ok: true, url, text: async () => `#EXTM3U\n${lines}\n#EXT-X-ENDLIST\n` }
      : { ok: true, url, text: async () => emptyBody });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureKalturaPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(10);
  });

  it("times out a stalled playlist request with a safe failure", async () => {
    vi.useFakeTimers();
    const playlist = `https://cfvod.kaltura.com/caption_captionasset/action/serveWebVTT/entry/${ENTRY_ID}/a.m3u8`;
    stubKaltura({ resources: [playlist] });
    let observedSignal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      observedSignal = init?.signal;
      return new Promise<Response>(() => undefined);
    }));

    const pending = captureKalturaPage();
    await vi.advanceTimersByTimeAsync(1_200);

    await expect(pending).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(observedSignal?.aborted).toBe(true);
  });

  it("returns a retryable parse failure when a discovered caption is unreadable", async () => {
    const url = "https://cfvod.kaltura.com/caption/broken.vtt?ks=secret";
    stubKaltura({ tracks: [{ src: url, srclang: "en" }] });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, text: async () => "not timed text" })));

    const result = await captureKalturaPage();

    expect(result).toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(JSON.stringify(result)).not.toContain("secret");
  });

  it("rejects a trusted descriptor that redirects to an untrusted resource", async () => {
    const url = "https://cfvod.kaltura.com/caption/redirect.vtt";
    stubKaltura({ tracks: [{ src: url, srclang: "en" }] });
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      url: "https://evil.example/caption.vtt",
      text: async () => "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nDo not consume\n",
    })));

    await expect(captureKalturaPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
  });

  it("returns no_caption only when no caption path is discovered", async () => {
    stubKaltura({
      href: "https://cdnapisec.kaltura.com/p/123/embed",
      html: `entry_id="${ENTRY_ID}"`,
    });

    const result = await captureKalturaPage();

    expect(result.status).toBe("no_caption");
    if (result.status === "no_caption") expect(result.capture.caption).toEqual({ status: "unavailable", source: null, language: null, cues: [] });
  });

  it("does not mistake serialized Kaltura field names for an entry ID", async () => {
    stubKaltura({
      href: "https://cdnapisec.kaltura.com/p/123/embed",
      html: "entry_id uiconf_id media_gallery 99_randomhash",
    });

    await expect(captureKalturaPage()).resolves.toEqual({ status: "not_media_frame" });
  });

  it("does not run the Kaltura adapter inside unrelated allFrames hosts", async () => {
    stubKaltura({
      href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}&entry_id=${ENTRY_ID}`,
      html: `entry_id=${ENTRY_ID}`,
    });

    await expect(captureKalturaPage()).resolves.toEqual({ status: "not_media_frame" });
  });

  it("ignores untrusted caption resources and keeps secrets out of captures", async () => {
    stubKaltura({ tracks: [
      { src: "https://evil.example/caption.vtt?ks=secret", srclang: "en" },
      { src: "https://user:password@cfvod.kaltura.com/caption/private.vtt", srclang: "en" },
    ] });

    const result = await captureKalturaPage();

    expect(result.status).toBe("no_caption");
    expect(JSON.stringify(result)).not.toContain("evil.example");
    expect(JSON.stringify(result)).not.toContain("secret");
  });

  it("recreates the Kaltura adapter from its serialized function source", async () => {
    const adapter = recreateAdapter(captureKalturaPage, {
      URL,
      location: {
        href: `https://cdnapisec.kaltura.com/p/123/embed?entry_id=${ENTRY_ID}`,
        hostname: "cdnapisec.kaltura.com",
        pathname: "/p/123/embed",
      },
      document: {
        documentElement: { innerHTML: `entry_id=${ENTRY_ID}` },
        title: "Kaltura Player",
        querySelectorAll: () => [],
        querySelector: () => null,
      },
      performance: { getEntriesByType: () => [] },
      setTimeout,
    });

    await expect(adapter()).resolves.toMatchObject({ status: "no_caption" });
  });
});

describe("capture attempt shape", () => {
  it("keeps the public PageCapture shape inside captured attempts", () => {
    const attempt: CaptureAttempt = { status: "captured", capture: availableCapture("youtube") };
    expect(attempt.capture).not.toHaveProperty("captionUrl");
    expect(attempt.capture).not.toHaveProperty("ks");
  });
});
