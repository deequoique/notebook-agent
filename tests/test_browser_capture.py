import hashlib
import json

import pytest
from pydantic import ValidationError

from app.browser_capture import (
    BrowserCaptureRequest,
    canonical_transcript_bytes,
    canonicalize_reference,
    cue_content_hash,
    normalized_cues,
    parse_canonical_transcript,
    timestamp_url,
)
from app.config import Settings
from app.browser_capture_submission import (
    BrowserCaptureSubmissionError,
    BrowserCaptureSubmissionService,
)
from app.channels.types import UserScope


def payload(*, platform="youtube", status="available"):
    caption = (
        {
            "status": "available",
            "source": "official_cc",
            "language": "en",
            "cues": [{"start_sec": 1, "end_sec": 2.5, "text": "hello"}],
        }
        if status == "available"
        else {"status": "unavailable", "source": None, "language": None, "cues": []}
    )
    platform_id = "dQw4w9WgXcQ" if platform == "youtube" else "123456_abCd"
    canonical_url = (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        if platform == "youtube"
        else "https://ntulearnvideo.ntu.edu.sg/media/123456_abCd?ks=SECRET"
    )
    request = {
        "protocol_version": "capture.v1",
        "client_version": "0.1.0",
        "platform": platform,
        "platform_id": platform_id,
        "canonical_url": canonical_url,
        "page_url": canonical_url,
        "metadata": {"title": "Lecture", "tags": [], "chapters": []},
        "caption": caption,
        "content_hash": "",
    }
    provisional = BrowserCaptureRequest.model_validate(
        {**request, "content_hash": "0" * 64}
    )
    request["content_hash"] = cue_content_hash(normalized_cues(provisional.caption))
    return request


def test_capture_contract_roundtrips_platform_neutral_transcript():
    request = BrowserCaptureRequest.model_validate(payload())
    body = canonical_transcript_bytes(request.caption)
    result = parse_canonical_transcript(body, source="official_cc", language="en")

    assert result.format == "capture_v1"
    assert [(cue.start, cue.end, cue.text) for cue in result.cues] == [(1, 2.5, "hello")]
    assert json.loads(body)["schema_version"] == "capture-transcript.v1"


def test_kaltura_reference_discards_signed_query_and_builds_platform_link():
    request = BrowserCaptureRequest.model_validate(payload(platform="ntu_kaltura"))
    canonical = canonicalize_reference(request.platform, request.platform_id, request.canonical_url)

    assert canonical == "https://ntulearnvideo.ntu.edu.sg/media/123456_abCd"
    assert "SECRET" not in canonical
    assert timestamp_url("ntu_kaltura", canonical, 12.9) == canonical


def test_unavailable_caption_requires_no_content_but_still_hashes_empty_input():
    request = BrowserCaptureRequest.model_validate(payload(status="unavailable"))

    assert request.content_hash == hashlib.sha256(b"").hexdigest()
    assert normalized_cues(request.caption) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra="forbidden"),
        lambda value: value["caption"]["cues"].append({"start_sec": 4, "end_sec": 3, "text": "bad"}),
        lambda value: value.update(canonical_url="https://evil.example/watch?v=dQw4w9WgXcQ"),
        lambda value: value["metadata"].update(cover_url="https://i.ytimg.com/vi/id/image.jpg?token=secret"),
    ],
)
def test_capture_contract_rejects_unknown_invalid_and_untrusted_input(mutation):
    value = payload()
    mutation(value)
    with pytest.raises((ValidationError, ValueError)):
        BrowserCaptureRequest.model_validate(value)


def test_extension_origin_configuration_is_exact_and_never_wildcarded():
    exact = "chrome-extension://omogodipchfidpikpeebgmlplpkjnpfm"
    assert Settings(browser_companion_allowed_origins=(exact,)).browser_companion_allowed_origins == (exact,)

    with pytest.raises(ValueError, match="exact chrome-extension origins"):
        Settings(browser_companion_allowed_origins=("chrome-extension://*",))


def test_configured_capture_limits_fail_before_database_or_object_io():
    request = BrowserCaptureRequest.model_validate(payload())
    service = BrowserCaptureSubmissionService(
        lambda: pytest.fail("oversized capture must not open the database"),
        lambda _dispatch_id: pytest.fail("oversized capture must not publish"),
        object(),
        quota_policy=object(),
        max_raw_bytes=5_000_000,
        max_cues=1,
        max_text_chars=2,
    )

    with pytest.raises(BrowserCaptureSubmissionError, match="capture_too_large"):
        service.submit(UserScope(7), request, request_key="one")
