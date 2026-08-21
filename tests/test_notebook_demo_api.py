from __future__ import annotations

from http.client import HTTPResponse
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from demo.notebook_demo_api import create_server


def _read(response: HTTPResponse) -> dict[str, Any] | None:
    payload = response.read()
    return json.loads(payload) if payload else None


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=2) as response:  # noqa: S310 - loopback fixture
            return response.status, _read(response)
    except HTTPError as error:
        return error.code, _read(error)


@pytest.fixture
def demo_api() -> str:
    server: ThreadingHTTPServer = create_server(port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_seeded_conversations_and_citations_are_available(demo_api: str) -> None:
    status, capabilities = _request(demo_api, "/api/v1/capabilities")
    assert status == 200
    assert capabilities is not None and capabilities["chat"] is True

    status, history = _request(demo_api, "/api/v1/conversations?limit=30")
    assert status == 200
    assert history is not None and len(history["items"]) == 3
    for item in history["items"]:
        status, turns = _request(
            demo_api,
            f"/api/v1/conversations/{item['thread_id']}/turns",
        )
        assert status == 200
        assert turns is not None and len(turns["turns"]) == 1
        assert len(turns["turns"][0]["citations"]) == 3
        assert all("youtube.com/watch" in citation["url"] for citation in turns["turns"][0]["citations"])


def test_each_video_has_its_own_timestamped_transcript(demo_api: str) -> None:
    public_ids = (
        "demo-how-to-talk-to-users",
        "demo-neural-network",
        "demo-effective-practice",
    )
    transcript_texts = []
    for public_id in public_ids:
        status, transcript = _request(
            demo_api,
            f"/api/v1/library/items/{public_id}/transcript?limit=50",
        )
        assert status == 200
        assert transcript is not None and len(transcript["blocks"]) == 3
        assert all(block["source_url"].endswith(str(block["start_sec"])) for block in transcript["blocks"])
        transcript_texts.append(" ".join(block["text"] for block in transcript["blocks"]))
    assert len(set(transcript_texts)) == 3
    assert "784" in transcript_texts[1]
    assert "练习" in transcript_texts[2]


def test_create_send_read_and_delete_conversation_contract(demo_api: str) -> None:
    status, created = _request(
        demo_api,
        "/api/v1/conversations/browser-conversation/reset",
        method="POST",
    )
    assert status == 200
    assert created is not None and created["thread_id"]
    thread_id = created["thread_id"]

    status, answer = _request(
        demo_api,
        "/api/v1/conversations/browser-conversation/messages",
        method="POST",
        payload={"message_id": "message-1", "text": "神经网络怎样识别手写数字？"},
    )
    assert status == 200
    assert answer is not None and answer["status"] == "ok"
    assert len(answer["citations"]) == 3

    status, turns = _request(demo_api, f"/api/v1/conversations/{thread_id}/turns")
    assert status == 200
    assert turns is not None and turns["turns"][0]["status"] == "ok"

    status, payload = _request(
        demo_api,
        f"/api/v1/conversations/{thread_id}",
        method="DELETE",
    )
    assert status == 204 and payload is None
    status, error = _request(demo_api, f"/api/v1/conversations/{thread_id}/turns")
    assert status == 404
    assert error is not None and error["code"] == "not_found"


def test_unsupported_question_fails_closed_without_citations(demo_api: str) -> None:
    _, created = _request(
        demo_api,
        "/api/v1/conversations/unsupported/reset",
        method="POST",
    )
    assert created is not None
    status, answer = _request(
        demo_api,
        "/api/v1/conversations/unsupported/messages",
        method="POST",
        payload={"message_id": "message-2", "text": "量子计算的产业规模是多少？"},
    )
    assert status == 200
    assert answer is not None and answer["status"] == "not_found"
    assert answer["citations"] == []


def test_demo_supports_the_advertised_saved_context_edit(demo_api: str) -> None:
    path = "/api/v1/library/items/demo-how-to-talk-to-users"
    status, item = _request(
        demo_api,
        path,
        method="PATCH",
        payload={"why_saved": "复赛演示 #产品调研"},
    )
    assert status == 200
    assert item is not None and item["why_saved"] == "复赛演示 #产品调研"
    _, persisted = _request(demo_api, path)
    assert persisted is not None and persisted["why_saved"] == "复赛演示 #产品调研"
