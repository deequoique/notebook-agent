import hashlib
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.browser_capture import (
    BrowserCaptureRequest,
    canonical_transcript_bytes,
    canonicalize_page_url,
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
from app.models import BrowserCapture, ContentItem, IngestDispatch


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


def test_ntulearnv1_is_an_exact_kaltura_page_origin_without_changing_canonical_hosts():
    url = "https://ntulearnv1.ntu.edu.sg/media/123456_abCd?ks=SECRET"
    safe_page_url = "https://ntulearnv1.ntu.edu.sg/media/123456_abCd"

    with pytest.raises(ValueError):
        canonicalize_reference("ntu_kaltura", "123456_abCd", url)
    assert canonicalize_page_url("ntu_kaltura", url) == safe_page_url

    request = payload(platform="ntu_kaltura")
    request["page_url"] = safe_page_url
    validated = BrowserCaptureRequest.model_validate(request)
    assert validated.page_url == safe_page_url
    assert canonicalize_reference(
        validated.platform, validated.platform_id, validated.canonical_url
    ) == "https://ntulearnvideo.ntu.edu.sg/media/123456_abCd"


@pytest.mark.parametrize(
    "host",
    [
        "ntulearnv10.ntu.edu.sg",
        "media.ntulearnv1.ntu.edu.sg",
        "ntulearnv1.ntu.edu.sg.evil.example",
    ],
)
def test_ntulearnv1_contract_rejects_near_hosts(host):
    url = f"https://{host}/media/123456_abCd"

    with pytest.raises(ValueError):
        canonicalize_reference("ntu_kaltura", "123456_abCd", url)
    with pytest.raises(ValueError):
        canonicalize_page_url("ntu_kaltura", url)


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

    assert Settings(
        notebook_agent_env="development",
        web_host="127.0.0.1",
        browser_companion_allowed_origins=("chrome-extension://*",),
    ).browser_companion_allowed_origins == ("chrome-extension://*",)
    with pytest.raises(ValueError, match="loopback-only development"):
        Settings(
            notebook_agent_env="production",
            browser_companion_allowed_origins=("chrome-extension://*",),
        )
    with pytest.raises(ValueError, match="loopback-only development"):
        Settings(
            notebook_agent_env="development",
            web_host="0.0.0.0",
            browser_companion_allowed_origins=("chrome-extension://*",),
        )


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


def test_database_admission_does_not_consume_broker_publish_budget(monkeypatch):
    request = BrowserCaptureRequest.model_validate(payload(status="unavailable"))
    observed: list[tuple[int, float]] = []

    def publish(dispatch_id: int, *, remaining_budget_seconds: float) -> str:
        observed.append((dispatch_id, remaining_budget_seconds))
        return "task-id"

    service = BrowserCaptureSubmissionService(
        Mock(),
        publish,
        object(),
        quota_policy=object(),
        max_raw_bytes=5_000_000,
    )
    monkeypatch.setattr(service, "_admit", lambda *args, **kwargs: (11, "capture", "item", 22))
    monkeypatch.setattr(service, "_mark_capture_ready", lambda capture_id: None)
    monkeypatch.setattr(service, "_mark_enqueued", lambda dispatch_id, task_id: None)

    result = service.submit(
        UserScope(7),
        request,
        request_key="one",
        publish_budget_seconds=5,
    )

    assert observed == [(22, 5)]
    assert result.status == "queued"


@pytest.mark.parametrize("worker_state", ("running", "completed"))
def test_mark_enqueued_does_not_clobber_faster_worker(worker_state):
    dispatch = IngestDispatch(
        id=22,
        public_id="dispatch-public",
        item_id=11,
        request_key="capture-request",
        attempt=1,
        state=worker_state,
    )

    class LockedSession:
        def __init__(self):
            self.statement = None
            self.commits = 0

        def __enter__(self): return self
        def __exit__(self, *_args): return None

        def scalar(self, statement):
            self.statement = statement
            # Simulate the worker commit becoming visible before the
            # publisher obtains the row lock.
            return dispatch

        def commit(self): self.commits += 1

    session = LockedSession()
    service = BrowserCaptureSubmissionService(
        lambda: session,
        lambda _dispatch_id: "task",
        object(),
        quota_policy=object(),
        max_raw_bytes=5_000_000,
    )

    service._mark_enqueued(dispatch.id, "publisher-task")

    assert dispatch.state == worker_state
    assert dispatch.task_id is None
    assert session.commits == 0
    assert "FOR UPDATE" in str(session.statement)


def test_mark_enqueued_transitions_pending_locked_row():
    dispatch = IngestDispatch(
        id=23,
        public_id="pending-dispatch-public",
        item_id=11,
        request_key="pending-capture-request",
        attempt=1,
        state="pending",
    )

    class LockedSession:
        def __init__(self): self.commits = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def scalar(self, _statement): return dispatch
        def commit(self): self.commits += 1

    session = LockedSession()
    service = BrowserCaptureSubmissionService(
        lambda: session,
        lambda _dispatch_id: "task",
        object(),
        quota_policy=object(),
        max_raw_bytes=5_000_000,
    )

    service._mark_enqueued(dispatch.id, "publisher-task")

    assert dispatch.state == "enqueued"
    assert dispatch.task_id == "publisher-task"
    assert session.commits == 1


@pytest.mark.parametrize("worker_state", ("running", "completed"))
def test_publish_failure_does_not_clobber_faster_worker(worker_state):
    dispatch = IngestDispatch(
        id=24,
        public_id="failed-publish-dispatch",
        item_id=11,
        request_key="failed-publish-request",
        attempt=1,
        state=worker_state,
    )

    class LockedSession:
        def __init__(self):
            self.statement = None
            self.commits = 0
            self.get_calls = 0

        def __enter__(self): return self
        def __exit__(self, *_args): return None

        def scalar(self, statement):
            self.statement = statement
            # The broker publisher lost its acknowledgement after the worker
            # had already claimed or completed this dispatch.
            return dispatch

        def get(self, *_args):
            self.get_calls += 1
            pytest.fail("late publish failure must not update capture/item rows")

        def commit(self): self.commits += 1

    session = LockedSession()
    service = BrowserCaptureSubmissionService(
        lambda: session,
        lambda _dispatch_id: "task",
        object(),
        quota_policy=object(),
        max_raw_bytes=5_000_000,
    )

    service._mark_failed(31, dispatch.id, "queue_unavailable")

    assert dispatch.state == worker_state
    assert dispatch.error_code is None
    assert session.commits == 0
    assert session.get_calls == 0
    assert "FOR UPDATE" in str(session.statement)


def test_publish_failure_marks_capture_and_dispatch_when_still_pending():
    dispatch = IngestDispatch(
        id=25,
        public_id="pending-failed-dispatch",
        item_id=12,
        request_key="pending-failed-request",
        attempt=1,
        state="pending",
    )
    capture = Mock(state="ready", item_id=12, error_code=None, updated_at=None)
    item = Mock(state="pending", fail_reason=None)

    class Session:
        def __init__(self): self.commits = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def scalar(self, _statement): return dispatch
        def get(self, model, object_id):
            if model is BrowserCapture and object_id == 31: return capture
            if model is ContentItem and object_id == 12: return item
            return None
        def commit(self): self.commits += 1

    session = Session()
    service = BrowserCaptureSubmissionService(
        lambda: session,
        lambda _dispatch_id: "task",
        object(),
        quota_policy=object(),
        max_raw_bytes=5_000_000,
    )

    service._mark_failed(31, dispatch.id, "queue_unavailable")

    assert capture.state == "failed"
    assert capture.error_code == "queue_unavailable"
    assert item.state == "failed"
    assert item.fail_reason == "queue_unavailable"
    assert dispatch.state == "failed"
    assert dispatch.error_code == "queue_unavailable"
    assert session.commits == 1
