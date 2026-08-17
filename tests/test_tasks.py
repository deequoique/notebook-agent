import json
import inspect
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import certifi
from kombu import Connection
import pytest

from app.connectors.base import (
    Cue,
    ItemMeta,
    NeedsASR,
    TextResult,
    TransientFetchError,
)
from app.config import Settings
from app.ingest.tasks import (
    IngestTask,
    _claim_dispatch,
    _complete_dispatch,
    _connector,
    _mark_dispatch_failed,
    _release_dispatch_for_retry,
    build_worker_embedder,
    create_item,
    fetch_text_task,
    process_item,
    publish_ingest_dispatch,
    run_isolated_batch,
)
from app.models import AppUser, BrowserCapture, ContentItem, IngestDispatch, Segment
from app.tls import TLSConfigurationError, TrustedCA


def test_celery_task_declares_exponential_item_retry():
    assert fetch_text_task.max_retries == 5
    assert fetch_text_task.retry_backoff == 8
    assert fetch_text_task.retry_backoff_max == 600


def test_worker_connector_resolves_trusted_ca_before_construction(monkeypatch):
    settings = replace(
        Settings(),
        tls_ca_bundle="/operator/ca.pem",
        youtube_proxy_url="http://127.0.0.1:18080",
    )
    calls = []
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.ingest.tasks.configure_trusted_ca",
        lambda bundle: calls.append(("ca", bundle)),
    )

    class Connector:
        def __init__(self, **kwargs):
            calls.append(("constructor", kwargs))

        def match(self, _url):
            return "dQw4w9WgXcQ"

    monkeypatch.setattr("app.ingest.tasks.YouTubeConnector", Connector)

    connector = _connector("https://youtu.be/dQw4w9WgXcQ")

    assert isinstance(connector, Connector)
    assert calls[0] == ("ca", "/operator/ca.pem")
    assert calls[1][0] == "constructor"
    assert calls[1][1]["proxy_url"] == "http://127.0.0.1:18080"


def test_worker_connector_fails_closed_before_constructor_for_invalid_ca(
    monkeypatch, tmp_path
):
    settings = replace(
        Settings(), tls_ca_bundle=str(tmp_path / "missing-ca.pem")
    )
    constructed = []
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)

    class Connector:
        def __init__(self, **_kwargs):
            constructed.append(True)

    monkeypatch.setattr("app.ingest.tasks.YouTubeConnector", Connector)

    with pytest.raises(TLSConfigurationError, match="readable file"):
        _connector("https://youtu.be/dQw4w9WgXcQ")

    assert constructed == []


def test_worker_ca_environment_reaches_bounded_subtitle_child(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    settings = replace(Settings(), tls_ca_bundle=certifi.where())
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)

    connector = _connector("https://youtu.be/dQw4w9WgXcQ")
    observed = []
    body = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 0,
                    "dDurationMs": 1000,
                    "segs": [{"utf8": "hello"}],
                }
            ]
        }
    ).encode()

    def subtitle_runner(_args, **kwargs):
        assert kwargs.get("env") is None
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, os, sys; "
                    "sys.stdout.write(json.dumps({"
                    "'ssl': os.environ.get('SSL_CERT_FILE'), "
                    "'requests': os.environ.get('REQUESTS_CA_BUNDLE')}))"
                ),
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        observed.append(json.loads(child.stdout))
        return SimpleNamespace(returncode=0, stdout=body, stderr=b"")

    connector._subtitle_runner = subtitle_runner
    connector._meta["dQw4w9WgXcQ"] = {
        "language": "en",
        "subtitles": {
            "en": [
                {
                    "ext": "json3",
                    "url": "https://www.youtube.com/api/timedtext",
                }
            ]
        },
        "automatic_captions": {},
    }

    result = connector.fetch_text("dQw4w9WgXcQ")

    assert result.cues[0].text == "hello"
    assert observed == [
        {"ssl": certifi.where(), "requests": certifi.where()}
    ]


def test_one_429_does_not_interrupt_fifteen_item_batch():
    attempts = {7: 0}
    sleeps = []
    calls = []

    def worker(item):
        calls.append(item)
        if item == 7 and attempts[7] == 0:
            attempts[7] += 1
            raise TransientFetchError("429")
        return item

    result = run_isolated_batch(list(range(15)), worker, sleep=sleeps.append)
    assert result == list(range(15))
    assert sleeps == [8]
    assert calls[:15] == list(range(15))
    assert calls[15:] == [7]


def test_retry_exhaustion_marks_dispatch_failed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.ingest.tasks._mark_dispatch_failed",
        lambda dispatch_id, exc, **_kwargs: calls.append(
            (dispatch_id, type(exc).__name__, _kwargs.get("task_id"))
        ),
    )
    IngestTask().on_failure(TransientFetchError("empty body"), "task", (41,), {}, None)
    assert calls == [(41, "TransientFetchError", "task")]


def test_retryable_first_failure_does_not_prematurely_mark_failed():
    class Item:
        id = 41
        platform_id = "dQw4w9WgXcQ"
        url = "https://youtu.be/dQw4w9WgXcQ"
        state = "fetching"

    item = Item()

    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, model, item_id): return item
        def commit(self): pass

    class Connector:
        def fetch_meta(self, platform_id): pass
        def fetch_text(self, platform_id): raise TransientFetchError("429")

    from app.ingest.tasks import process_item

    with pytest.raises(TransientFetchError, match="429"):
        process_item(41, connector=Connector(), session_factory=lambda: DB())
    assert item.state == "fetching"


def test_worker_fetches_and_persists_metadata_before_text():
    class Item:
        id = 41
        user_id = 57
        platform = "youtube"
        platform_id = "dQw4w9WgXcQ"
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        title = None
        author = None
        published_at = None
        duration_sec = None
        lang = None
        description = None
        tags = None
        chapters = None
        cover_url = None
        state = "pending"

    item = Item()
    commits = []

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, _model, _item_id): return item
        def commit(self): commits.append(item.state)

    calls = []
    published_at = datetime(2026, 8, 6, tzinfo=UTC)

    class Connector:
        def fetch_meta(self, platform_id):
            calls.append(("meta", platform_id))
            return ItemMeta(
                platform_id=platform_id,
                url=item.url,
                title="worker title",
                author="worker author",
                published_at=published_at,
                duration_sec=42,
                lang="zh",
                description="worker description",
                tags=["worker"],
                chapters=[{"start": 0, "end": 42, "title": "all"}],
                cover_url="https://example.test/cover",
            )

        def fetch_text(self, platform_id):
            calls.append(("text", platform_id))
            return NeedsASR()

    state = process_item(
        item.id,
        connector=Connector(),
        session_factory=lambda: DB(),
    )

    assert state == "needs_asr"
    assert calls == [
        ("meta", item.platform_id),
        ("text", item.platform_id),
    ]
    assert commits == ["fetching", "needs_asr"]
    assert item.title == "worker title"
    assert item.author == "worker author"
    assert item.published_at == published_at
    assert item.duration_sec == 42
    assert item.tags == ["worker"]
    assert item.state == "needs_asr"


def test_browser_capture_without_captions_goes_to_needs_asr_without_remote_fetch(monkeypatch):
    item = type("Item", (), {"id": 41, "user_id": 7, "state": "pending", "text_source": "none"})()
    capture = type("Capture", (), {"caption_status": "unavailable"})()
    statements = []

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, _model, _item_id): return item
        def scalar(self, statement):
            statements.append(str(statement))
            return capture
        def commit(self): return None

    monkeypatch.setattr("app.ingest.tasks._connector", lambda _url: pytest.fail("capture path must not use the remote connector"))
    state = process_item(item.id, session_factory=lambda: DB())

    assert state == "needs_asr"
    assert item.text_source == "none"
    assert "browser_capture.app_user_id" in statements[0]


def test_browser_capture_ready_from_original_attempt_is_reused_on_retry(monkeypatch):
    item = type(
        "Item",
        (),
        {
            "id": 41,
            "user_id": 7,
            "platform": "ntu_kaltura",
            "platform_id": "123456_retry",
            "url": "https://ntulearnvideo.ntu.edu.sg/media/123456_retry",
            "chapters": [],
            "state": "pending",
            "raw_object_key": None,
            "raw_format": "json3",
            "content_hash": None,
            "text_source": "none",
            "lang": None,
            "fail_reason": None,
            "deleted_at": None,
            "purge_claimed_at": None,
        },
    )()
    capture = type(
        "Capture",
        (),
        {
            "app_user_id": 7,
            "caption_status": "available",
            "caption_source": "official_cc",
            "language": "en",
            "raw_object_key": "7/ntu_kaltura/123456_retry/retry.capture.json",
        },
    )()
    dispatch_states = iter(("running", "failed"))
    statements = []

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, model, _item_id):
            assert model is ContentItem
            return item
        def scalar(self, statement):
            sql = str(statement)
            statements.append(sql)
            dispatch_state = next(dispatch_states)
            # A ready capture remains reusable after the dispatch that staged
            # it has failed. The pre-fix query incorrectly required that
            # dispatch to remain running.
            if "ingest_dispatch.state" in sql and dispatch_state != "running":
                return None
            return capture
        def commit(self): return None
        def refresh(self, _value): return None
        def execute(self, _statement): return None
        def add(self, _value): return None

    body = json.dumps(
        {
            "schema_version": "capture-transcript.v1",
            "cues": [{"start_sec": 0, "end_sec": 2, "text": "retry text"}],
        }
    ).encode()

    class Store:
        def __init__(self): self.gets = []
        def get(self, key, *, max_bytes):
            self.gets.append((key, max_bytes))
            return body
        def put(self, *_args): pytest.fail("captured object must not be uploaded twice")

    class Embedder:
        def embed(self, values): return [[1.0, 0.0] for _ in values]

    monkeypatch.setattr("app.ingest.tasks._connector", lambda _url: pytest.fail("retry must reuse the ready browser capture"))
    store = Store()
    assert process_item(item.id, embedder=Embedder(), object_store=store, session_factory=lambda: DB()) == "ready"
    item.state = "pending"
    assert process_item(item.id, embedder=Embedder(), object_store=store, session_factory=lambda: DB()) == "ready"
    assert len(store.gets) == 2
    assert all("browser_capture.app_user_id" in sql for sql in statements)
    assert all("ingest_dispatch.state" not in sql for sql in statements)
    assert all("browser_capture.created_at DESC" in sql and "browser_capture.id DESC" in sql for sql in statements)


def test_browser_capture_retry_does_not_reuse_a_different_users_capture(monkeypatch):
    item = type(
        "Item",
        (),
        {
            "id": 41,
            "user_id": 7,
            "platform": "ntu_kaltura",
            "platform_id": "123456_retry",
            "url": "https://ntulearnvideo.ntu.edu.sg/media/123456_retry",
            "chapters": [],
            "state": "pending",
        },
    )()
    foreign_capture = type(
        "Capture",
        (),
        {
            "app_user_id": 99,
            "caption_status": "available",
            "caption_source": "official_cc",
            "language": "en",
            "raw_object_key": "99/ntu_kaltura/123456_retry/foreign.capture.json",
        },
    )()

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, model, _item_id):
            assert model is ContentItem
            return item
        def scalar(self, statement):
            sql = str(statement)
            # Simulate a bad/cross-tenant row that would be selected by the
            # old item-only lookup, but must be excluded by app_user_id.
            return foreign_capture if "browser_capture.app_user_id" not in sql else None
        def commit(self): return None

    class Connector:
        def __init__(self): self.calls = []
        def fetch_meta(self, platform_id):
            self.calls.append(("meta", platform_id))
            return None
        def fetch_text(self, platform_id):
            self.calls.append(("text", platform_id))
            return NeedsASR()

    connector = Connector()
    monkeypatch.setattr("app.ingest.tasks._connector", lambda _url: connector)
    assert process_item(item.id, session_factory=lambda: DB()) == "needs_asr"
    assert connector.calls == [("meta", item.platform_id), ("text", item.platform_id)]


def test_browser_capture_with_captions_reuses_object_and_existing_worker(monkeypatch):
    item = type(
        "Item",
        (),
        {
            "id": 41,
            "user_id": 7,
            "platform": "ntu_kaltura",
            "platform_id": "123456_abCd",
            "url": "https://ntulearnvideo.ntu.edu.sg/media/123456_abCd",
            "chapters": [],
            "state": "pending",
            "raw_object_key": None,
            "raw_format": "json3",
            "content_hash": None,
            "text_source": "none",
            "lang": None,
            "fail_reason": None,
            "deleted_at": None,
            "purge_claimed_at": None,
        },
    )()
    capture = type(
        "Capture",
        (),
        {
            "caption_status": "available",
            "caption_source": "official_cc",
            "language": "en",
            "raw_object_key": "7/ntu_kaltura/123456_abCd/hash.capture.json",
        },
    )()
    added = []

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, model, _item_id):
            assert model is ContentItem
            return item
        def scalar(self, _statement): return capture
        def commit(self): return None
        def refresh(self, _value): return None
        def execute(self, _statement): return None
        def add(self, value): added.append(value)

    body = json.dumps(
        {
            "schema_version": "capture-transcript.v1",
            "cues": [{"start_sec": 0, "end_sec": 2, "text": "lecture text"}],
        }
    ).encode()

    class Store:
        def __init__(self): self.gets = []
        def get(self, key, *, max_bytes):
            self.gets.append((key, max_bytes))
            return body
        def put(self, *_args): pytest.fail("captured object must not be uploaded twice")

    class Embedder:
        def embed(self, values): return [[1.0, 0.0] for _ in values]

    store = Store()
    monkeypatch.setattr("app.ingest.tasks._connector", lambda _url: pytest.fail("captured content must not call a remote connector"))
    state = process_item(
        item.id,
        embedder=Embedder(),
        object_store=store,
        session_factory=lambda: DB(),
    )

    assert state == "ready"
    assert item.raw_object_key == capture.raw_object_key
    assert item.raw_format == "capture_v1"
    assert item.text_source == "official_cc"
    assert any(isinstance(value, Segment) for value in added)
    assert len(store.gets) == 1


def test_long_browser_capture_skips_per_cue_semantic_embedding(monkeypatch):
    item = type(
        "Item",
        (),
        {
            "id": 42,
            "user_id": 7,
            "platform": "ntu_kaltura",
            "platform_id": "123456_long",
            "url": "https://ntulearnvideo.ntu.edu.sg/media/123456_long",
            "chapters": [],
            "state": "pending",
            "raw_object_key": None,
            "raw_format": "json3",
            "content_hash": None,
            "text_source": "none",
            "lang": None,
            "fail_reason": None,
            "deleted_at": None,
            "purge_claimed_at": None,
        },
    )()
    capture = type(
        "Capture",
        (),
        {
            "caption_status": "available",
            "caption_source": "official_cc",
            "language": "en",
            "raw_object_key": "7/ntu_kaltura/123456_long/hash.capture.json",
        },
    )()

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, model, _item_id):
            assert model is ContentItem
            return item
        def scalar(self, _statement): return capture
        def commit(self): return None
        def refresh(self, _value): return None
        def execute(self, _statement): return None
        def add(self, _value): return None

    cue_count = 513
    body = json.dumps(
        {
            "schema_version": "capture-transcript.v1",
            "cues": [
                {"start_sec": index, "end_sec": index + 0.8, "text": f"cue {index}"}
                for index in range(cue_count)
            ],
        }
    ).encode()

    class Store:
        def get(self, _key, *, max_bytes):
            assert max_bytes > len(body)
            return body
        def put(self, *_args): pytest.fail("captured object must not be uploaded twice")

    class Embedder:
        def __init__(self): self.calls = []
        def embed(self, values):
            self.calls.append(list(values))
            return [[1.0, 0.0] for _ in values]

    embedder = Embedder()
    state = process_item(
        item.id,
        embedder=embedder,
        object_store=Store(),
        session_factory=lambda: DB(),
    )

    assert state == "ready"
    assert len(embedder.calls) == 1
    assert 0 < len(embedder.calls[0]) < cue_count


def test_long_connector_transcript_keeps_per_cue_semantic_embedding():
    cue_count = 513
    item = type(
        "Item",
        (),
        {
            "id": 43,
            "user_id": 7,
            "platform": "youtube",
            "platform_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "chapters": [],
            "state": "pending",
            "raw_object_key": None,
            "raw_format": "json3",
            "content_hash": None,
            "text_source": "none",
            "lang": None,
            "fail_reason": None,
            "deleted_at": None,
            "purge_claimed_at": None,
        },
    )()

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, model, _item_id):
            assert model is ContentItem
            return item
        def commit(self): return None
        def refresh(self, _value): return None
        def execute(self, _statement): return None
        def add(self, _value): return None

    cues = [Cue(index, index + 0.8, "word") for index in range(cue_count)]

    class Connector:
        platform = "youtube"

        def fetch_meta(self, _platform_id):
            return None

        def fetch_text(self, _platform_id):
            return TextResult(b"connector transcript", cues, "official_cc", "en")

    class Store:
        def put(self, *_args): return None

    class Embedder:
        def __init__(self): self.calls = []
        def embed(self, values):
            self.calls.append(list(values))
            return [[1.0, 0.0] for _ in values]

    embedder = Embedder()
    state = process_item(
        item.id,
        connector=Connector(),
        embedder=embedder,
        object_store=Store(),
        session_factory=lambda: DB(),
    )

    assert state == "ready"
    # Ordinary connector ingestion retains semantic-boundary quality even
    # when a transcript happens to exceed the browser-capture optimization
    # threshold: one per-cue call followed by final chunk vectors.
    assert len(embedder.calls) == 2
    assert len(embedder.calls[0]) == cue_count
    assert 0 < len(embedder.calls[1]) < cue_count


@pytest.mark.parametrize(
    "result",
    [
        TextResult(b"x" * 11, [Cue(0, 1, "ok")], "official_cc", "en"),
        TextResult(b"{}", [Cue(0, 1, "a"), Cue(1, 2, "b")], "official_cc", "en"),
        TextResult(b"{}", [Cue(0, 1, "toolong")], "official_cc", "en"),
    ],
)
def test_worker_rejects_oversized_transcript_before_storage_or_embedding(monkeypatch, result):
    class Item:
        id = 41
        user_id = 57
        platform = "youtube"
        platform_id = "dQw4w9WgXcQ"
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        chapters = None
        state = "pending"

    item = Item()

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, _model, _item_id): return item
        def commit(self): return None

    class Connector:
        def fetch_meta(self, _platform_id): return None
        def fetch_text(self, _platform_id): return result

    class Store:
        def put(self, *_args): pytest.fail("oversized content must not reach object storage")

    class Embedder:
        def embed(self, _texts): pytest.fail("oversized content must not reach the provider")

    limits = replace(
        Settings(),
        ingest_max_raw_transcript_bytes=10,
        ingest_max_cues_per_item=1,
        ingest_max_text_chars_per_item=5,
        ingest_max_segments_per_item=2,
        ingest_max_embedding_chars_per_item=10,
    )
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: limits)

    with pytest.raises(ValueError, match="ingest_too_large"):
        process_item(
            item.id,
            connector=Connector(),
            embedder=Embedder(),
            object_store=Store(),
            session_factory=lambda: DB(),
        )


def test_cli_ingestion_fetches_metadata_exactly_once():
    class Store:
        item = None

    store = Store()

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, model, object_id):
            if model is AppUser and object_id == 57:
                return object()
            if model is ContentItem and store.item is not None:
                return store.item
            return None

        def scalar(self, _statement):
            return None

        def add(self, value):
            if isinstance(value, ContentItem):
                value.id = 41
                store.item = value

        def commit(self):
            return None

    calls = []

    class Connector:
        platform = "youtube"

        def match(self, _url):
            return "dQw4w9WgXcQ"

        def fetch_meta(self, platform_id):
            calls.append(("meta", platform_id))
            return ItemMeta(
                platform_id=platform_id,
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                title="one fetch",
                author=None,
                published_at=None,
                duration_sec=None,
                lang=None,
                description=None,
                tags=None,
                chapters=None,
                cover_url=None,
            )

        def fetch_text(self, platform_id):
            calls.append(("text", platform_id))
            return NeedsASR()

    from app.ingest.tasks import ingest_url

    item_id, state = ingest_url(
        "https://youtu.be/dQw4w9WgXcQ",
        user_id=57,
        connector=Connector(),
        session_factory=lambda: DB(),
    )

    assert (item_id, state) == (41, "needs_asr")
    assert calls == [
        ("meta", "dQw4w9WgXcQ"),
        ("text", "dQw4w9WgXcQ"),
    ]
    assert store.item.title == "one fetch"
    assert store.item.public_id


def test_cli_resave_from_trash_clears_the_web_archive_marker(monkeypatch):
    deleted_at = datetime(2026, 8, 7, tzinfo=UTC)
    item = ContentItem(
        id=41,
        public_id="restored-public",
        user_id=57,
        platform="youtube",
        platform_id="dQw4w9WgXcQ",
        kind="video",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        state="ready",
        archived_at=datetime(2026, 8, 6, tzinfo=UTC),
        deleted_at=deleted_at,
    )
    scalar_results = [item, datetime(2026, 8, 8, tzinfo=UTC)]

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, model, object_id):
            if model is AppUser and object_id == 57:
                return object()
            return None

        def scalar(self, _statement):
            return scalar_results.pop(0)

        def commit(self):
            return None

    class Connector:
        platform = "youtube"

        def match(self, _url):
            return "dQw4w9WgXcQ"

    class SettingsProbe:
        trash_retention_days = 30

    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: SettingsProbe())

    restored_id = create_item(
        item.url,
        user_id=item.user_id,
        why_saved="restored",
        connector=Connector(),
        session_factory=lambda: DB(),
    )

    assert restored_id == item.id
    assert item.deleted_at is None
    assert item.archived_at is None
    assert item.why_saved == "restored"


def test_celery_task_passes_only_dispatch_and_current_task_id(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.ingest.tasks.process_dispatch",
        lambda dispatch_id, *, task_id: calls.append(
            (dispatch_id, task_id)
        ) or "ready",
    )
    fetch_text_task.push_request(id="celery-task-id")
    try:
        assert fetch_text_task.run(71) == "ready"
    finally:
        fetch_text_task.pop_request()
    assert calls == [(71, "celery-task-id")]


def test_synchronous_embedding_failure_marks_item_failed(monkeypatch):
    class Item:
        state = "fetching"
        fail_reason = None

    item = Item()

    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, model, item_id): return item
        def commit(self): pass

    monkeypatch.setattr("app.ingest.tasks.create_item", lambda *args, **kwargs: 41)
    monkeypatch.setattr(
        "app.ingest.tasks.process_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("embedding failed")),
    )
    from app.ingest.tasks import ingest_url

    with pytest.raises(RuntimeError, match="embedding failed"):
        ingest_url(
            "https://youtu.be/dQw4w9WgXcQ",
            user_id=1,
            connector=object(),
            session_factory=lambda: DB(),
        )
    assert item.state == "failed"
    assert item.fail_reason == "ingestion_failed"


class DispatchDB:
    def __init__(self, dispatch, item):
        self.dispatch = dispatch
        self.item = item

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, _statement):
        return self.dispatch

    def get(self, model, object_id):
        if model is ContentItem and object_id == self.item.id:
            return self.item
        return None

    def commit(self):
        return None


def test_dispatch_claim_retry_release_and_completion_are_conditional():
    dispatch = IngestDispatch(
        id=71,
        public_id="dispatch",
        item_id=41,
        request_key="request",
        attempt=1,
        state="pending",
    )
    item = ContentItem(
        id=41,
        user_id=57,
        platform="youtube",
        platform_id="dQw4w9WgXcQ",
        kind="video",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        state="pending",
    )
    factory = lambda: DispatchDB(dispatch, item)

    assert _claim_dispatch(71, "task-1", session_factory=factory) == 41
    assert dispatch.state == "running"
    assert dispatch.task_id == "task-1"
    assert _claim_dispatch(71, "task-2", session_factory=factory) is None

    _release_dispatch_for_retry(
        71, "task-1", session_factory=factory
    )
    assert dispatch.state == "enqueued"
    assert _claim_dispatch(71, "task-1", session_factory=factory) == 41

    item.state = "ready"
    _complete_dispatch(
        71,
        "task-1",
        process_state="ready",
        session_factory=factory,
    )
    assert dispatch.state == "completed"
    assert _claim_dispatch(71, "task-1", session_factory=factory) is None


def test_terminal_dispatch_hooks_do_not_publish_retired_completion_envelopes():
    for helper in (_claim_dispatch, _complete_dispatch, _mark_dispatch_failed):
        source = inspect.getsource(helper)
        assert "_publish_completion_event_best_effort(" not in source
        assert "publish_ingest_completion_event(" not in source


def test_publisher_sends_only_durable_dispatch_id(monkeypatch):
    calls = []

    class Result:
        id = "celery-task-id"

    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.connection_for_write",
        lambda **_kwargs: Connection("memory://"),
    )

    monkeypatch.setattr(
        fetch_text_task,
        "apply_async",
        lambda *, args, **kwargs: calls.append((args, kwargs)) or Result(),
    )

    task_id = publish_ingest_dispatch(71)

    assert task_id == "celery-task-id"
    assert calls[0][0] == [71]
    assert calls[0][1]["producer"].connection.connect_timeout > 0
    assert calls[0][1]["retry"] is True
    assert calls[0][1]["retry_policy"]["max_retries"] == 1
    assert calls[0][1]["retry_policy"]["max_retries"] < 10
    assert calls[0][1]["timeout"] > 0
    assert calls[0][1]["timeout"] < Settings().agent_timeout_seconds


def test_publish_bounds_clamp_to_agent_deadline():
    from app.ingest.tasks import _bounded_publish_options

    options = _bounded_publish_options(
        replace(
            Settings(),
            agent_timeout_seconds=45,
            agent_tool_timeout_seconds=2,
            broker_publish_timeout_seconds=20,
            broker_publish_max_retries=3,
        )
    )

    assert options["_total_timeout"] == 1.0
    assert options["retry_policy"]["max_retries"] == 3
    assert options["timeout"] > 0


def test_publisher_timeout_propagates_to_submission_service(monkeypatch):
    def timed_out(*_args, **_kwargs):
        raise TimeoutError("broker publish timed out")

    monkeypatch.setattr(fetch_text_task, "apply_async", timed_out)

    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.connection_for_write",
        lambda **_kwargs: Connection("memory://"),
    )

    with pytest.raises(TimeoutError, match="broker publish timed out"):
        publish_ingest_dispatch(71)


def test_publisher_bypasses_both_unbounded_shared_pools(monkeypatch):
    settings = replace(
        Settings(),
        broker_publish_timeout_seconds=0.2,
        broker_publish_max_retries=0,
        agent_timeout_seconds=2,
        agent_tool_timeout_seconds=1,
    )
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.producer_pool.acquire",
        lambda **_kwargs: pytest.fail("shared producer pool was used"),
    )
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.producer_pool.connections.acquire",
        lambda **_kwargs: pytest.fail("shared connection pool was used"),
    )
    connection_options = []
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.connection_for_write",
        lambda **kwargs: connection_options.append(kwargs)
        or Connection("memory://"),
    )
    published = []

    class Result:
        id = "task-id"

    monkeypatch.setattr(
        fetch_text_task,
        "apply_async",
        lambda **kwargs: published.append(kwargs) or Result(),
    )

    assert publish_ingest_dispatch(71) == "task-id"

    assert connection_options[0]["connect_timeout"] > 0
    assert connection_options[0]["transport_options"]["socket_timeout"] > 0
    assert published[0]["args"] == [71]
    assert published[0]["producer"] is not None


def test_worker_embedder_receives_verified_ca(monkeypatch):
    context = object()
    captured = {}
    monkeypatch.setattr(
        "app.ingest.tasks.configure_trusted_ca",
        lambda _configured: TrustedCA("/safe/ca.pem", context),
    )

    class Embedder:
        def __init__(self, api_key, **kwargs):
            captured["api_key"] = api_key
            captured.update(kwargs)

    monkeypatch.setattr("app.ingest.tasks.ZhipuEmbedder", Embedder)
    build_worker_embedder(
        replace(Settings(), zhipu_api_key="worker-key")
    )

    assert captured["api_key"] == "worker-key"
    assert captured["ssl_context"] is context
