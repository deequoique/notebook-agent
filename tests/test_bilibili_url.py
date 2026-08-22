import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.browser_capture import timestamp_url
from app.connectors.base import NeedsASR, NeedsExtension, TransientFetchError
from app.connectors.bilibili import (
    BilibiliConnector,
    canonicalize_bilibili_url,
    parse_srt,
)
from app.ingest.tasks import _connector
from app.retrieval.search import _hits
from app.ingest.submission import (
    ItemReference,
    UnsupportedURL,
    normalize_item_reference,
    prepare_submission,
)


@pytest.mark.parametrize(
    ("url", "platform_id", "canonical"),
    [
        (
            "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.1007",
            "BV1xx411c7mD",
            "https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        (
            "https://bilibili.com/video/bv1xx411c7mD/",
            "BV1xx411c7mD",
            "https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        (
            "https://www.bilibili.com/video/av1074402/",
            "av1074402",
            "https://www.bilibili.com/video/av1074402",
        ),
    ],
)
def test_bilibili_url_is_normalized_without_remote_fetch(url, platform_id, canonical):
    assert normalize_item_reference(url) == ItemReference(
        platform="bilibili",
        platform_id=platform_id,
        canonical_url=canonical,
    )
    assert canonicalize_bilibili_url(url) == (platform_id, canonical)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.bilibili.com/video/BV1xx411c7mD",
        "https://www.bilibili.com/video/BV1xx411c7mD.evil.example",
        "https://www.bilibili.com/video/BV1xx411c7mD/extra",
        "https://www.bilibili.com/video/BV1xx411c7mD#fragment",
        "https://www.bilibili.com:444/video/BV1xx411c7mD",
        "https://user:password@www.bilibili.com/video/BV1xx411c7mD",
        "https://b23.tv/abc123",
        "https://www.bilibili.com/space/123",
        "https://www.bilibili.com/video/BV1short",
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
        "https://www.bilibili.com/video/BV1xx411c7mD?p=1&p=2",
    ],
)
def test_bilibili_url_rejects_unsafe_or_ambiguous_shapes(url):
    assert BilibiliConnector().match(url) is None
    with pytest.raises(UnsupportedURL):
        normalize_item_reference(url)


def test_bilibili_preflight_does_not_turn_other_hosts_into_platform_urls():
    prepared = prepare_submission([
        "https://example.test/video/BV1xx411c7mD",
        "https://www.bilibili.com/video/BV1xx411c7mD",
    ])

    assert prepared.items[0].failure is not None
    assert prepared.items[0].failure.status == "unsupported_url"
    assert prepared.items[1].reference == ItemReference(
        platform="bilibili",
        platform_id="BV1xx411c7mD",
        canonical_url="https://www.bilibili.com/video/BV1xx411c7mD",
    )


def test_parse_srt_normalizes_multiline_markup_and_timestamps():
    body = (
        "\ufeff1\r\n00:00:01,500 --> 00:00:03,000\r\n"
        "<b>Hello</b> &amp;\r\nworld\r\n\r\n"
        "2\r\n00:01:02.250 --> 00:01:03.000\r\nNext\r\n"
    ).encode()

    cues = parse_srt(body)

    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (1.5, 3.0, "Hello & world"),
        (62.25, 63.0, "Next"),
    ]


def test_fetch_meta_maps_public_fields_and_never_passes_cookies():
    calls = []
    metadata = {
        "id": "BV13x41117TL",
        "title": "A title",
        "uploader": "A creator",
        "timestamp": 1488353834,
        "duration": 554.117,
        "description": "Description",
        "tags": ["one", 2, "two"],
        "chapters": [{"title": "Intro", "start_time": 0, "end_time": 10}],
        "thumbnail": "http://i2.hdslb.com/bfs/archive/cover.jpg",
        "subtitles": {},
    }

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="")

    connector = BilibiliConnector(runner=runner, fetch_timeout_seconds=7)
    result = connector.fetch_meta("BV13x41117TL")

    assert result.url == "https://www.bilibili.com/video/BV13x41117TL"
    assert result.title == "A title"
    assert result.author == "A creator"
    assert result.published_at == datetime.fromtimestamp(1488353834, UTC)
    assert result.duration_sec == 554
    assert result.tags == ["one", "two"]
    assert result.cover_url == "https://i2.hdslb.com/bfs/archive/cover.jpg"
    assert calls[0][1]["timeout"] == 7
    assert "--no-playlist" in calls[0][0]
    assert "--skip-download" in calls[0][0]
    assert not any("cookie" in str(value).lower() for value in calls[0][0])


def test_fetch_text_uses_inline_official_srt_and_ignores_danmaku():
    body = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"
    connector = BilibiliConnector()
    connector._meta["BV1xx411c7mD"] = {
        "subtitles": {
            "danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}],
            "ai-zh": [{"ext": "srt", "data": body.replace("你好", "自动字幕")}],
            "zh-CN": [{"ext": "srt", "data": body}],
        }
    }

    result = connector.fetch_text("BV1xx411c7mD")

    assert result.format == "srt"
    assert result.source == "official_cc"
    assert result.lang == "zh"
    assert result.raw_body == body.encode()
    assert result.cues[0].text == "你好"


def test_login_only_subtitle_routes_to_browser_companion():
    metadata = {"id": "BV12N4y1M7rh", "subtitles": {}}
    connector = BilibiliConnector(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(metadata),
            stderr="WARNING: Subtitles are only available when logged in.",
        )
    )

    connector.fetch_meta("BV12N4y1M7rh")

    assert isinstance(connector.fetch_text("BV12N4y1M7rh"), NeedsExtension)


def test_video_without_subtitles_routes_to_asr():
    connector = BilibiliConnector()
    connector._meta["BV13x41117TL"] = {"subtitles": {}}

    assert isinstance(connector.fetch_text("BV13x41117TL"), NeedsASR)


def test_subtitle_without_inline_data_fails_closed_to_browser_capture():
    connector = BilibiliConnector()
    connector._meta["BV1xx411c7mD"] = {
        "subtitles": {
            "zh-CN": [
                {
                    "ext": "srt",
                    "url": "https://aisubtitle.hdslb.com/private-signed-query",
                }
            ]
        }
    }

    result = connector.fetch_text("BV1xx411c7mD")

    assert isinstance(result, NeedsExtension)
    assert "private-signed-query" not in result.reason


@pytest.mark.parametrize(
    ("stderr", "classification"),
    [
        ("HTTP Error 429: private response", "bilibili_rate_limited"),
        ("Unable to download video info: -352: 风控校验失败", "bilibili_rate_limited"),
        ("private signed URL failed", "bilibili_fetch_failed"),
    ],
)
def test_yt_dlp_failures_are_stable_and_redacted(stderr, classification):
    connector = BilibiliConnector(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=stderr,
        )
    )

    with pytest.raises(TransientFetchError, match=rf"^{classification}$") as caught:
        connector.fetch_meta("BV1xx411c7mD")

    assert "private" not in str(caught.value)


def test_bilibili_metadata_timeout_is_bounded():
    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    connector = BilibiliConnector(runner=timed_out, fetch_timeout_seconds=0.25)

    with pytest.raises(TransientFetchError, match="^bilibili_fetch_timeout$"):
        connector.fetch_meta("BV1xx411c7mD")


def test_worker_routes_bilibili_url_to_bilibili_connector(monkeypatch):
    settings = replace(Settings(), bilibili_fetch_timeout_seconds=9)
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)
    monkeypatch.setattr("app.ingest.tasks.configure_trusted_ca", lambda _bundle: None)

    connector = _connector("https://www.bilibili.com/video/BV1xx411c7mD")

    assert isinstance(connector, BilibiliConnector)
    assert connector._fetch_timeout_seconds == 9


def test_bilibili_fetch_timeout_setting_must_be_positive():
    with pytest.raises(ValueError, match="BILIBILI_FETCH_TIMEOUT_SECONDS"):
        Settings(bilibili_fetch_timeout_seconds=0)


def test_bilibili_timestamp_link_drops_tracking_query():
    assert timestamp_url(
        "bilibili",
        "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=private",
        42.9,
    ) == "https://www.bilibili.com/video/BV1xx411c7mD?t=42"


def test_retrieval_hit_uses_bilibili_source_instead_of_youtube_fallback():
    segment = SimpleNamespace(id=3, text="evidence", start_sec=42)
    item = SimpleNamespace(
        id=2,
        title="Bilibili source",
        platform="bilibili",
        platform_id="BV1xx411c7mD",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )

    hit = _hits([(segment, item, 0.9)])[0]

    assert hit.url == "https://www.bilibili.com/video/BV1xx411c7mD?t=42"
