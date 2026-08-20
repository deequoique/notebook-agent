import json
from types import SimpleNamespace

import pytest

from app.object_store import ObjectNotFound, ObjectTooLarge, RawObjectStore
from app.web.transcript import TranscriptError, TranscriptService


def json3(*events):
    return json.dumps({"events": list(events)}).encode()


def cue(start_ms, duration_ms, text):
    return {"tStartMs": start_ms, "dDurationMs": duration_ms, "segs": [{"utf8": text}]}


class DB:
    def __init__(self, item):
        self.item = item
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, statement):
        self.statements.append(statement)
        return self.item


class Store:
    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.calls = []

    def get(self, key, *, max_bytes):
        self.calls.append((key, max_bytes))
        if self.error:
            raise self.error
        return self.body


def content(**overrides):
    values = {
        "public_id": "item-public",
        "user_id": 7,
        "state": "ready",
        "raw_object_key": "7/youtube/video/hash.json3",
        "content_hash": "content-v1",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "deleted_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def service(item, store):
    db = DB(item)
    return TranscriptService(lambda: db, store), db


def test_tenant_validation_happens_before_object_io():
    store = Store(b"unused")
    transcript, db = service(None, store)

    with pytest.raises(TranscriptError) as caught:
        transcript.get(SimpleNamespace(app_user_id=7), "other-tenant")

    assert caught.value.error_code == "not_found"
    assert store.calls == []
    assert "content_item.user_id" in str(db.statements[0])
    assert "content_item.deleted_at IS NULL" in str(db.statements[0])


def test_raw_key_must_have_authenticated_user_prefix_before_io():
    store = Store(b"unused")
    transcript, _ = service(content(raw_object_key="8/youtube/private.json3"), store)

    with pytest.raises(TranscriptError) as caught:
        transcript.get(SimpleNamespace(app_user_id=7), "item-public")

    assert caught.value.error_code == "transcript_unavailable"
    assert store.calls == []


def test_original_json3_is_used_and_blocks_are_non_overlapping_and_deduplicated():
    body = json3(
        cue(0, 2000, " Hello   world "),
        cue(1000, 2500, "Hello world"),
        cue(3000, 2000, "Next sentence"),
    )
    store = Store(body)
    transcript, _ = service(content(), store)

    page = transcript.get(SimpleNamespace(app_user_id=7), "item-public", limit=10)

    assert store.calls == [("7/youtube/video/hash.json3", transcript.max_object_bytes)]
    assert [block.text for block in page.blocks] == ["Hello world Next sentence"]
    assert page.blocks[0].start_sec == 0
    assert page.blocks[0].end_sec == 5
    assert page.blocks[0].source_url.endswith("&t=0")
    assert page.next_cursor is None


def test_bilibili_srt_is_read_through_the_same_transcript_contract():
    body = b"1\n00:00:01,000 --> 00:00:03,000\nhello from Bilibili\n"
    store = Store(body)
    transcript, _ = service(
        content(
            platform="bilibili",
            raw_format="srt",
            raw_object_key="7/bilibili/BV1xx411c7mD/hash.srt",
            url="https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        store,
    )

    page = transcript.get(SimpleNamespace(app_user_id=7), "item-public", limit=10)

    assert [block.text for block in page.blocks] == ["hello from Bilibili"]
    assert page.blocks[0].start_sec == 1
    assert page.blocks[0].source_url == (
        "https://www.bilibili.com/video/BV1xx411c7mD?t=1"
    )


def test_cursor_is_revision_bound_and_pages_do_not_overlap():
    body = json3(cue(0, 1000, "one"), cue(60000, 1000, "two"), cue(120000, 1000, "three"))
    transcript, _ = service(content(), Store(body))

    first = transcript.get(SimpleNamespace(app_user_id=7), "item-public", limit=1)
    second = transcript.get(SimpleNamespace(app_user_id=7), "item-public", limit=1, cursor=first.next_cursor)

    assert [block.text for block in first.blocks] == ["one"]
    assert [block.text for block in second.blocks] == ["two"]
    assert second.blocks[0].start_sec >= first.blocks[0].end_sec

    changed_text_hash, _ = service(content(content_hash="content-v2"), Store(body))
    same_raw_page = changed_text_hash.get(
        SimpleNamespace(app_user_id=7),
        "item-public",
        cursor=first.next_cursor,
    )
    assert [block.text for block in same_raw_page.blocks] == ["two", "three"]


def test_cursor_revision_uses_raw_json3_when_text_hash_is_unchanged():
    first_body = json3(cue(0, 1000, "same"), cue(60000, 1000, "text"))
    changed_timing = json3(cue(5000, 1000, "same"), cue(65000, 1000, "text"))
    store = Store(first_body)
    transcript, _ = service(content(content_hash="same-normalized-text"), store)

    first = transcript.get(SimpleNamespace(app_user_id=7), "item-public", limit=1)
    assert first.next_cursor is not None

    store.body = changed_timing
    with pytest.raises(TranscriptError) as caught:
        transcript.get(
            SimpleNamespace(app_user_id=7),
            "item-public",
            cursor=first.next_cursor,
        )

    assert caught.value.error_code == "transcript_invalid"


@pytest.mark.parametrize(
    ("store", "expected"),
    [
        (Store(error=ObjectNotFound("private")), "transcript_unavailable"),
        (Store(error=ObjectTooLarge("private")), "transcript_too_large"),
        (Store(b"not-json"), "transcript_invalid"),
    ],
)
def test_store_and_parse_failures_are_safe(store, expected):
    transcript, _ = service(content(), store)

    with pytest.raises(TranscriptError) as caught:
        transcript.get(SimpleNamespace(app_user_id=7), "item-public")

    assert caught.value.error_code == expected
    assert "private" not in repr(caught.value)


def test_unexpected_store_programming_error_escapes_the_service_boundary():
    transcript, _ = service(
        content(),
        Store(error=RuntimeError("private programming error")),
    )

    with pytest.raises(RuntimeError, match="private programming error"):
        transcript.get(SimpleNamespace(app_user_id=7), "item-public")


def test_injected_object_store_reader_does_not_load_global_settings(monkeypatch):
    class Client:
        def head_object(self, **_kwargs):
            return {"ContentLength": 4}

        def get_object(self, **_kwargs):
            return {"Body": SimpleNamespace(read=lambda _limit: b"data", close=lambda: None)}

    monkeypatch.setattr(
        "app.object_store.get_settings",
        lambda: pytest.fail("injected adapter loaded global settings"),
    )

    store = RawObjectStore(client=Client(), bucket="raw")

    assert store.get("7/item.json3", max_bytes=4) == b"data"


def test_object_store_rejects_content_length_before_body_read():
    class Client:
        body_reads = 0

        def head_object(self, **_kwargs):
            return {"ContentLength": 5}

        def get_object(self, **_kwargs):
            self.body_reads += 1
            return {"Body": SimpleNamespace(read=lambda _limit: b"data", close=lambda: None)}

    client = Client()
    store = RawObjectStore(client=client, bucket="raw")

    with pytest.raises(ObjectTooLarge):
        store.get("7/item.json3", max_bytes=4)

    assert client.body_reads == 0
