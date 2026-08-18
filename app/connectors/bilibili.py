"""Bilibili URL identity helpers.

The first Bilibili slice intentionally owns only local URL recognition and
canonicalization. Metadata and caption fetching are added in the connector
follow-up, so accepting a URL here never requires a network lookup.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


_BILIBILI_HOSTS = frozenset({"bilibili.com", "www.bilibili.com"})
_BILIBILI_VIDEO_RE = re.compile(
    r"^/(?:video/)(?P<id>(?P<prefix>[aA][vV])\d+|(?P<bv>[bB][vV][0-9A-Za-z]{10}))/?$"
)


class BilibiliConnector:
    """Local Bilibili URL matcher used by submission preflight.

    ``fetch_meta``/``fetch_text`` deliberately do not exist yet.  Keeping the
    URL contract in a platform-owned module lets the later yt-dlp connector
    reuse the exact same identity rules without making URL validation perform
    remote work.
    """

    platform = "bilibili"

    def match(self, url: str) -> str | None:
        parts = urlsplit(str(url).strip())
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.fragment
            or (parts.hostname or "").lower() not in _BILIBILI_HOSTS
        ):
            return None
        match = _BILIBILI_VIDEO_RE.fullmatch(parts.path)
        if match is None:
            return None
        platform_id = match.group("id")
        if platform_id[:2].lower() == "bv":
            return "BV" + platform_id[2:]
        return "av" + platform_id[2:]

    def canonical_url(self, platform_id: str) -> str:
        if re.fullmatch(r"[bB][vV][0-9A-Za-z]{10}", platform_id):
            normalized_id = "BV" + platform_id[2:]
        elif re.fullmatch(r"[aA][vV]\d+", platform_id):
            normalized_id = "av" + platform_id[2:]
        else:
            raise ValueError("invalid Bilibili platform id")
        return f"https://www.bilibili.com/video/{normalized_id}"


def canonicalize_bilibili_url(url: str) -> tuple[str, str] | None:
    """Return ``(platform_id, canonical_url)`` for one safe video URL."""

    connector = BilibiliConnector()
    platform_id = connector.match(url)
    if platform_id is None:
        return None
    return platform_id, connector.canonical_url(platform_id)
