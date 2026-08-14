"""Versioned, platform-neutral browser caption capture contract."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.connectors.base import Cue, ItemMeta, TextResult


CAPTURE_PROTOCOL_VERSION = "capture.v1"
CAPTURE_TRANSCRIPT_VERSION = "capture-transcript.v1"
MAX_CAPTURE_CUES = 50_000
MAX_CAPTURE_TEXT_CHARS = 1_000_000
MAX_CAPTURE_METADATA_CHARS = 50_000
Platform = Literal["youtube", "ntu_kaltura"]
CaptionSource = Literal["official_cc", "auto_caption"]

_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_KALTURA_ID_RE = re.compile(r"^[0-9]+_[A-Za-z0-9]+$")
_YOUTUBE_HOSTS = frozenset(
    {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"}
)
_KALTURA_HOSTS = frozenset(
    {"ntulearn.ntu.edu.sg", "ntulearnvideo.ntu.edu.sg"}
)
_YOUTUBE_COVER_HOSTS = frozenset({"i.ytimg.com", "img.youtube.com"})


class CaptureContractError(ValueError):
    """Stable contract failure that never includes submitted content."""

    def __init__(self, code: str = "capture_payload_invalid") -> None:
        self.error_code = code
        super().__init__(code)


class CaptureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaptureCue(CaptureModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_timing(self) -> "CaptureCue":
        if not math.isfinite(self.start_sec) or not math.isfinite(self.end_sec):
            raise ValueError("cue timing must be finite")
        if self.end_sec < self.start_sec:
            raise ValueError("cue end must not precede start")
        if not self.text.strip():
            raise ValueError("cue text must not be blank")
        return self


class CaptureChapter(CaptureModel):
    title: str = Field(min_length=1, max_length=500)
    start_sec: float = Field(ge=0)
    end_sec: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_timing(self) -> "CaptureChapter":
        if not math.isfinite(self.start_sec):
            raise ValueError("chapter timing must be finite")
        if self.end_sec is not None:
            if not math.isfinite(self.end_sec) or self.end_sec < self.start_sec:
                raise ValueError("chapter end must not precede start")
        return self


class CaptureMetadata(CaptureModel):
    title: str | None = Field(default=None, max_length=1_000)
    author: str | None = Field(default=None, max_length=1_000)
    published_at: datetime | None = None
    duration_sec: int | None = Field(default=None, ge=0, le=31_536_000)
    language: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_CAPTURE_METADATA_CHARS)
    cover_url: str | None = Field(default=None, max_length=2_048)
    tags: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    chapters: list[CaptureChapter] = Field(default_factory=list, max_length=1_000)

    @field_validator("title", "author", "language", "description", "cover_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CaptureCaption(CaptureModel):
    status: Literal["available", "unavailable"]
    source: CaptionSource | None = None
    language: str | None = Field(default=None, max_length=64)
    cues: list[CaptureCue] = Field(default_factory=list, max_length=MAX_CAPTURE_CUES)

    @model_validator(mode="after")
    def validate_status(self) -> "CaptureCaption":
        if self.status == "unavailable":
            if self.source is not None or self.language is not None or self.cues:
                raise ValueError("unavailable captions must not contain content")
            return self
        if self.source is None or not self.language or not self.cues:
            raise ValueError("available captions require source, language, and cues")
        previous_start = -1.0
        text_chars = 0
        for cue in self.cues:
            if cue.start_sec < previous_start:
                raise ValueError("caption cues must be ordered")
            previous_start = cue.start_sec
            text_chars += len(cue.text)
            if text_chars > MAX_CAPTURE_TEXT_CHARS:
                raise ValueError("caption text is too large")
        return self


class BrowserCaptureRequest(CaptureModel):
    protocol_version: Literal[CAPTURE_PROTOCOL_VERSION]
    client_version: str = Field(min_length=1, max_length=64)
    platform: Platform
    platform_id: str = Field(min_length=1, max_length=200)
    canonical_url: str = Field(min_length=1, max_length=2_048)
    page_url: str | None = Field(default=None, max_length=2_048)
    metadata: CaptureMetadata
    caption: CaptureCaption
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_platform_reference(self) -> "BrowserCaptureRequest":
        canonicalize_reference(self.platform, self.platform_id, self.canonical_url)
        if self.page_url is not None:
            canonicalize_page_url(self.platform, self.page_url)
        if self.metadata.cover_url is not None:
            parts = urlsplit(self.metadata.cover_url)
            allowed = (
                _YOUTUBE_COVER_HOSTS
                if self.platform == "youtube"
                else frozenset({"ntulearnvideo.ntu.edu.sg"})
            )
            if (
                parts.scheme != "https"
                or (parts.hostname or "").lower() not in allowed
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError("cover URL must be a secret-free platform asset")
        return self


class CanonicalTranscript(CaptureModel):
    schema_version: Literal[CAPTURE_TRANSCRIPT_VERSION]
    cues: list[CaptureCue] = Field(max_length=MAX_CAPTURE_CUES)


def canonicalize_reference(platform: Platform, platform_id: str, url: str) -> str:
    """Return a secret-free canonical URL for one supported media identity."""

    value = str(url).strip()
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.username or parts.password:
        raise CaptureContractError()
    host = (parts.hostname or "").lower()
    if platform == "youtube":
        if not _YOUTUBE_ID_RE.fullmatch(platform_id) or host not in _YOUTUBE_HOSTS:
            raise CaptureContractError()
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        observed_id = parts.path.strip("/") if host == "youtu.be" else query.get("v")
        if observed_id != platform_id:
            raise CaptureContractError()
        return f"https://www.youtube.com/watch?v={platform_id}"
    if not _KALTURA_ID_RE.fullmatch(platform_id) or host not in _KALTURA_HOSTS:
        raise CaptureContractError()
    if platform_id not in parts.path and platform_id not in parts.query:
        raise CaptureContractError()
    return urlunsplit(("https", host, parts.path or "/", "", ""))


def canonicalize_page_url(platform: Platform, url: str) -> str:
    parts = urlsplit(str(url).strip())
    if parts.scheme != "https" or parts.username or parts.password:
        raise CaptureContractError()
    host = (parts.hostname or "").lower()
    allowed = _YOUTUBE_HOSTS if platform == "youtube" else _KALTURA_HOSTS
    if host not in allowed:
        raise CaptureContractError()
    return urlunsplit(("https", host, parts.path or "/", "", ""))


def normalized_cues(caption: CaptureCaption) -> list[Cue]:
    return [Cue(cue.start_sec, cue.end_sec, cue.text.strip()) for cue in caption.cues]


def cue_content_hash(cues: list[Cue]) -> str:
    normalized = "\n".join(
        f"{cue.start:.3f}\t{cue.end:.3f}\t{cue.text.strip()}" for cue in cues
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def canonical_transcript_bytes(caption: CaptureCaption) -> bytes:
    payload = CanonicalTranscript(
        schema_version=CAPTURE_TRANSCRIPT_VERSION,
        cues=caption.cues,
    )
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_canonical_transcript(
    body: bytes, *, source: CaptionSource, language: str
) -> TextResult:
    try:
        payload = CanonicalTranscript.model_validate_json(body)
    except Exception as exc:
        raise CaptureContractError("transcript_invalid") from exc
    cues = [Cue(cue.start_sec, cue.end_sec, cue.text.strip()) for cue in payload.cues]
    return TextResult(body, cues, source, language, "capture_v1")


def item_meta(request: BrowserCaptureRequest, *, canonical_url: str) -> ItemMeta:
    metadata = request.metadata
    return ItemMeta(
        platform_id=request.platform_id,
        url=canonical_url,
        title=metadata.title,
        author=metadata.author,
        published_at=metadata.published_at,
        duration_sec=metadata.duration_sec,
        lang=metadata.language,
        description=metadata.description,
        tags=metadata.tags,
        chapters=[chapter.model_dump() for chapter in metadata.chapters],
        cover_url=metadata.cover_url,
    )


def timestamp_url(platform: str, url: str, seconds: float) -> str:
    """Build one platform-owned source link without preserving signed queries."""

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if platform == "youtube":
        query["t"] = str(max(0, int(seconds)))
    elif platform == "ntu_kaltura":
        # NTULearnVideo's supported deep-link parameter has not been verified.
        # Preserve the safe canonical source instead of inventing one.
        query = {}
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
