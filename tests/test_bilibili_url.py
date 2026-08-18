import pytest

from app.connectors.bilibili import BilibiliConnector, canonicalize_bilibili_url
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
        "https://user:password@www.bilibili.com/video/BV1xx411c7mD",
        "https://b23.tv/abc123",
        "https://www.bilibili.com/space/123",
        "https://www.bilibili.com/video/BV1short",
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
