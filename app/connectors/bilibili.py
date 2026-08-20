"""Bilibili URL and subtitle connector backed by yt-dlp.

The connector never persists or supplies Bilibili cookies. yt-dlp resolves
public metadata and any server-visible official subtitle into an in-memory SRT
payload; login-only subtitles are handed to the browser-companion path.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit, urlunsplit

from app.connectors.base import (
    Cue,
    ItemMeta,
    NeedsASR,
    NeedsExtension,
    TextResult,
    TransientFetchError,
)
from app.ingest.validate import IngestLimitExceeded, guard_transcript


_BILIBILI_HOSTS = frozenset({"bilibili.com", "www.bilibili.com"})
_BILIBILI_VIDEO_RE = re.compile(
    r"^/video/(?P<id>(?:[aA][vV])\d+|(?:[bB][vV])[0-9A-Za-z]{10})/?$"
)
_SRT_TIMING_RE = re.compile(
    r"^(?P<start>\d{1,3}:[0-5]\d:[0-5]\d[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,3}:[0-5]\d:[0-5]\d[,.]\d{3})(?:\s+.*)?$"
)
_LOGIN_SUBTITLE_MARKER = "subtitles are only available when logged in"
DEFAULT_MAX_TRANSCRIPT_BYTES = 5_000_000
DEFAULT_FETCH_TIMEOUT_SECONDS = 30.0


def _srt_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt(body: bytes) -> list[Cue]:
    """Parse bounded UTF-8 SRT into normalized, ordered cues."""

    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TransientFetchError("invalid Bilibili SRT encoding") from exc
    cues: list[Cue] = []
    previous_start = -1.0
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    for block in re.split(r"\n[ \t]*\n", normalized):
        lines = [line.strip() for line in block.split("\n")]
        timing_index = next(
            (
                index
                for index, line in enumerate(lines[:2])
                if _SRT_TIMING_RE.fullmatch(line)
            ),
            None,
        )
        if timing_index is None:
            continue
        timing = _SRT_TIMING_RE.fullmatch(lines[timing_index])
        assert timing is not None
        start = _srt_seconds(timing.group("start"))
        end = _srt_seconds(timing.group("end"))
        if end < start:
            raise TransientFetchError("invalid Bilibili SRT timing")
        if start < previous_start:
            raise TransientFetchError("unordered Bilibili SRT cues")
        previous_start = start
        cue_text = " ".join(
            html.unescape(re.sub(r"<[^>]+>", " ", line)).strip()
            for line in lines[timing_index + 1 :]
        )
        cue_text = " ".join(cue_text.split())
        if cue_text:
            cues.append(Cue(start, max(end, start + 0.01), cue_text))
    return cues


def _base_language(value: object) -> str:
    if not isinstance(value, str):
        return "und"
    normalized = value.strip().lower().replace("_", "-")
    if normalized.startswith("ai-"):
        normalized = normalized[3:]
    return normalized.split("-", 1)[0] or "und"


def _safe_cover_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    if (
        parts.scheme not in {"http", "https"}
        or parts.username
        or parts.password
        or not (host == "hdslb.com" or host.endswith(".hdslb.com"))
    ):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None:
        return None
    return urlunsplit(("https", parts.netloc, parts.path, "", ""))


class BilibiliConnector:
    platform = "bilibili"

    def __init__(
        self,
        *,
        runner=subprocess.run,
        max_transcript_bytes: int = DEFAULT_MAX_TRANSCRIPT_BYTES,
        fetch_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        if max_transcript_bytes <= 0 or fetch_timeout_seconds <= 0:
            raise ValueError("connector limits must be positive")
        self._runner = runner
        self._max_transcript_bytes = max_transcript_bytes
        self._fetch_timeout_seconds = fetch_timeout_seconds
        self._meta: dict[str, dict] = {}
        self._login_subtitles: set[str] = set()

    def match(self, url: str) -> str | None:
        parts = urlsplit(str(url).strip())
        try:
            port = parts.port
        except ValueError:
            return None
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.fragment
            or port is not None
            or (parts.hostname or "").lower() not in _BILIBILI_HOSTS
        ):
            return None
        page_values = parse_qs(parts.query, keep_blank_values=True).get("p", [])
        if page_values and page_values != ["1"]:
            return None
        match = _BILIBILI_VIDEO_RE.fullmatch(parts.path)
        if match is None:
            return None
        platform_id = match.group("id")
        return (
            "BV" + platform_id[2:]
            if platform_id[:2].lower() == "bv"
            else "av" + platform_id[2:]
        )

    def canonical_url(self, platform_id: str) -> str:
        if re.fullmatch(r"[bB][vV][0-9A-Za-z]{10}", platform_id):
            normalized_id = "BV" + platform_id[2:]
        elif re.fullmatch(r"[aA][vV]\d+", platform_id):
            normalized_id = "av" + platform_id[2:]
        else:
            raise ValueError("invalid Bilibili platform id")
        return f"https://www.bilibili.com/video/{normalized_id}"

    @staticmethod
    def _classify_failure(stderr: object) -> str:
        message = str(stderr or "").lower()
        if any(
            marker in message
            for marker in ("429", "too many requests", "-352", "risk control", "风控")
        ):
            return "bilibili_rate_limited"
        return "bilibili_fetch_failed"

    def _run_metadata(self, platform_id: str) -> subprocess.CompletedProcess[str]:
        url = self.canonical_url(platform_id)
        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--socket-timeout",
            str(self._fetch_timeout_seconds),
            "--no-playlist",
            "--skip-download",
            "--dump-single-json",
            url,
        ]
        try:
            result = self._runner(
                args,
                text=True,
                capture_output=True,
                check=False,
                timeout=self._fetch_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise TransientFetchError("bilibili_fetch_timeout") from None
        if result.returncode:
            raise TransientFetchError(self._classify_failure(result.stderr))
        return result

    def fetch_meta(self, platform_id: str) -> ItemMeta:
        result = self._run_metadata(platform_id)
        try:
            data = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TransientFetchError(
                "yt-dlp returned invalid Bilibili metadata JSON"
            ) from exc
        if not isinstance(data, dict):
            raise TransientFetchError("yt-dlp returned invalid Bilibili metadata JSON")
        self._meta[platform_id] = data
        if _LOGIN_SUBTITLE_MARKER in str(result.stderr or "").lower():
            self._login_subtitles.add(platform_id)
        timestamp = data.get("timestamp")
        published_at = None
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            try:
                published_at = datetime.fromtimestamp(timestamp, timezone.utc)
            except (OverflowError, OSError, ValueError):
                published_at = None
        resolved_id = data.get("id")
        canonical_id = (
            "BV" + resolved_id[2:]
            if isinstance(resolved_id, str)
            and re.fullmatch(r"[bB][vV][0-9A-Za-z]{10}", resolved_id)
            else platform_id
        )
        duration = data.get("duration")
        tags = data.get("tags")
        chapters = data.get("chapters")
        return ItemMeta(
            platform_id=platform_id,
            url=self.canonical_url(canonical_id),
            title=data.get("title") if isinstance(data.get("title"), str) else None,
            author=(
                data.get("uploader")
                if isinstance(data.get("uploader"), str)
                else None
            ),
            published_at=published_at,
            duration_sec=(
                round(duration)
                if isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and duration >= 0
                else None
            ),
            lang=None,
            description=(
                data.get("description")
                if isinstance(data.get("description"), str)
                else None
            ),
            tags=(
                [tag for tag in tags if isinstance(tag, str)]
                if isinstance(tags, list)
                else []
            ),
            chapters=(
                [chapter for chapter in chapters if isinstance(chapter, dict)]
                if isinstance(chapters, list)
                else []
            ),
            cover_url=_safe_cover_url(data.get("thumbnail")),
        )

    @staticmethod
    def _select_track(data: dict) -> tuple[str, str, dict] | None:
        subtitles = data.get("subtitles")
        if not isinstance(subtitles, dict):
            return None
        candidates: list[tuple[tuple[int, int, int], str, str, dict]] = []
        for language_position, (language, formats) in enumerate(subtitles.items()):
            if not isinstance(language, str) or language.lower() == "danmaku":
                continue
            if not isinstance(formats, list):
                continue
            source = (
                "auto_caption"
                if language.lower().startswith("ai-")
                else "official_cc"
            )
            for format_position, subtitle_format in enumerate(formats):
                if (
                    isinstance(subtitle_format, dict)
                    and subtitle_format.get("ext") == "srt"
                ):
                    candidates.append(
                        (
                            (
                                1 if source == "auto_caption" else 0,
                                language_position,
                                format_position,
                            ),
                            source,
                            language,
                            subtitle_format,
                        )
                    )
        if not candidates:
            return None
        _, source, language, subtitle_format = min(
            candidates, key=lambda item: item[0]
        )
        return source, language, subtitle_format

    def fetch_text(self, platform_id: str) -> TextResult | NeedsExtension | NeedsASR:
        data = self._meta.get(platform_id)
        if data is None:
            self.fetch_meta(platform_id)
            data = self._meta[platform_id]
        selected = self._select_track(data)
        if selected is None:
            if platform_id in self._login_subtitles:
                return NeedsExtension("Bilibili subtitles require browser login")
            return NeedsASR()
        source, language, subtitle_format = selected
        subtitle_data = subtitle_format.get("data")
        if not isinstance(subtitle_data, str):
            return NeedsExtension("Bilibili subtitle data requires browser capture")
        body = subtitle_data.encode("utf-8")
        if len(body) > self._max_transcript_bytes:
            raise IngestLimitExceeded()
        cues = parse_srt(body)
        guard_transcript(body, cues, platform=self.platform)
        return TextResult(body, cues, source, _base_language(language), "srt")


def canonicalize_bilibili_url(url: str) -> tuple[str, str] | None:
    """Return ``(platform_id, canonical_url)`` for one safe video URL."""

    connector = BilibiliConnector()
    platform_id = connector.match(url)
    if platform_id is None:
        return None
    return platform_id, connector.canonical_url(platform_id)
