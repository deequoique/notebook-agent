"""Tenant-safe projection of original JSON3 subtitles into readable pages."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Protocol

from sqlalchemy import select

from app.connectors.base import Cue, TransientFetchError
from app.connectors.youtube import parse_json3
from app.browser_capture import parse_canonical_transcript, timestamp_url
from app.models import ContentItem
from app.object_store import ObjectStoreError, ObjectTooLarge


class UserScopeLike(Protocol):
    app_user_id: int


class TranscriptError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True)
class TranscriptBlock:
    ordinal: int
    start_sec: float
    end_sec: float
    text: str
    source_url: str


@dataclass(frozen=True)
class TranscriptPage:
    blocks: tuple[TranscriptBlock, ...]
    next_cursor: str | None


def _encode_cursor(content_hash: str, index: int) -> str:
    raw = json.dumps({"h": content_hash, "i": index}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_cursor(cursor: str, content_hash: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if payload.get("h") != content_hash:
            raise ValueError("revision mismatch")
        index = int(payload["i"])
        if index < 0:
            raise ValueError("negative cursor")
        return index
    except Exception as exc:
        raise TranscriptError("transcript_invalid") from exc


def _blocks(
    cues: list[Cue], source_url: str, *, platform: str = "youtube"
) -> list[TranscriptBlock]:
    cleaned: list[tuple[float, float, str]] = []
    previous_text: str | None = None
    previous_end = 0.0
    for cue in cues:
        text = " ".join(str(cue.text).split())
        if not text or text == previous_text:
            continue
        start = float(cue.start)
        end = float(cue.end)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < 0:
            raise TranscriptError("transcript_invalid")
        start = max(start, previous_end)
        end = max(start, end)
        cleaned.append((start, end, text))
        previous_text = text
        previous_end = end

    merged: list[tuple[float, float, str]] = []
    for start, end, text in cleaned:
        if merged:
            old_start, old_end, old_text = merged[-1]
            combined = f"{old_text} {text}"
            if start - old_end <= 2.0 and end - old_start <= 45.0 and len(combined) <= 800:
                merged[-1] = (old_start, max(old_end, end), combined)
                continue
        merged.append((start, end, text))
    return [
        TranscriptBlock(
            index, start, end, text, timestamp_url(platform, source_url, start)
        )
        for index, (start, end, text) in enumerate(merged)
    ]


class TranscriptService:
    def __init__(
        self,
        session_factory,
        object_store,
        *,
        max_object_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self._session_factory = session_factory
        self._object_store = object_store
        self.max_object_bytes = max_object_bytes

    def get(
        self,
        scope: UserScopeLike,
        item_public_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TranscriptPage:
        limit = max(1, min(int(limit), 100))
        with self._session_factory() as db:
            item = db.scalar(
                select(ContentItem).where(
                    ContentItem.user_id == scope.app_user_id,
                    ContentItem.public_id == item_public_id,
                    ContentItem.deleted_at.is_(None),
                )
            )
            if item is None:
                raise TranscriptError("not_found")
            if item.state != "ready" or not item.raw_object_key:
                raise TranscriptError("transcript_unavailable")
            key = item.raw_object_key
            source_url = item.url
            platform = getattr(item, "platform", "youtube")
            raw_format = getattr(item, "raw_format", "json3")
            text_source = getattr(item, "text_source", "official_cc")
            language = getattr(item, "lang", None) or "und"
        if not key.startswith(f"{scope.app_user_id}/"):
            raise TranscriptError("transcript_unavailable")
        try:
            body = self._object_store.get(key, max_bytes=self.max_object_bytes)
        except ObjectTooLarge as exc:
            raise TranscriptError("transcript_too_large") from exc
        except ObjectStoreError as exc:
            raise TranscriptError("transcript_unavailable") from exc
        try:
            if raw_format == "capture_v1":
                parsed = parse_canonical_transcript(
                    body,
                    source=text_source,
                    language=language,
                )
                cues = parsed.cues
            elif raw_format == "json3":
                cues = parse_json3(body)
            else:
                raise TranscriptError("transcript_invalid")
            blocks = _blocks(cues, source_url, platform=platform)
        except (TransientFetchError, TranscriptError, ValueError) as exc:
            raise TranscriptError("transcript_invalid") from exc
        revision = hashlib.sha256(body).hexdigest()
        index = _decode_cursor(cursor, revision) if cursor else 0
        if index > len(blocks):
            raise TranscriptError("transcript_invalid")
        page = blocks[index : index + limit]
        next_index = index + len(page)
        next_cursor = _encode_cursor(revision, next_index) if next_index < len(blocks) else None
        return TranscriptPage(tuple(page), next_cursor)
