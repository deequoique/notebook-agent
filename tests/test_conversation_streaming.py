from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.types import AgentAnswer
from app.agent.streaming import AgentStreamEvent
from app.agent.types import Citation
from app.config import Settings
from app.models import AppUser, ChannelIdentity, WebAuthChallenge, WebSession
from app.web.auth import CSRF_COOKIE_NAME
from app.web_api import build_web_auth_service, create_web_app
from app.web_auth import InMemoryEmailSender, InMemoryLoginRateLimiter, WebAuthService


ORIGIN = "https://streaming.example.test"


class _ChannelService:
    def __init__(self, answer: AgentAnswer | None = None):
        self.envelopes = []
        self.answer = answer or AgentAnswer(
            status="ok",
            text="这是一个分段传输的安全答案。",
        )

    async def handle(self, envelope):
        self.envelopes.append(envelope)
        return self.answer


class _BrokenChannel(_ChannelService):
    async def handle(self, envelope):
        self.envelopes.append(envelope)
        raise RuntimeError("provider payload PRIVATE_SENTINEL must not reach browser")


class _SlowChannel(_ChannelService):
    async def handle(self, envelope):
        self.envelopes.append(envelope)
        await asyncio.sleep(0.05)
        return self.answer


class _SectionStreamingChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        citation = Citation(
            item_id=7,
            segment_id=11,
            title="公开来源",
            excerpt="字幕依据",
            url="https://example.test/video?t=11",
            start_sec=11,
        )
        yield AgentStreamEvent(
            "activity", envelope.request_id, envelope.message_id, activity="planning_answer"
        )
        yield AgentStreamEvent(
            "section_started", envelope.request_id, envelope.message_id,
            section_id="section-1", status="grounded", citation_ids=(11,), citations=(citation,)
        )
        yield AgentStreamEvent(
            "text_delta", envelope.request_id, envelope.message_id,
            section_id="section-1", text="第一段"
        )
        yield AgentStreamEvent(
            "text_delta", envelope.request_id, envelope.message_id,
            section_id="section-1", text="第二段"
        )
        yield AgentStreamEvent(
            "section_completed", envelope.request_id, envelope.message_id,
            section_id="section-1", status="grounded"
        )
        yield AgentStreamEvent(
            "section_started", envelope.request_id, envelope.message_id,
            section_id="section-2", status="unsupported"
        )
        yield AgentStreamEvent(
            "text_delta", envelope.request_id, envelope.message_id,
            section_id="section-2", text="当前检索证据不足以确认该部分。"
        )
        yield AgentStreamEvent(
            "section_completed", envelope.request_id, envelope.message_id,
            section_id="section-2", status="unsupported"
        )
        yield AgentStreamEvent(
            "completed", envelope.request_id, envelope.message_id,
            answer=AgentAnswer(
                status="ok",
                text="第一段第二段\n\n当前检索证据不足以确认该部分。",
                citations=[citation],
                thread_id="thread-1",
            ),
            new_messages=(),
        )


class _CompletedOnlyChannel(_ChannelService):
    """Simulate a real channel whose provider only supports whole answers."""

    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield AgentStreamEvent(
            "completed",
            envelope.request_id,
            envelope.message_id,
            answer=self.answer,
        )


def _section_started(envelope, *, request_id: str | None = None):
    citation = Citation(
        item_id=7,
        segment_id=11,
        title="公开来源",
        excerpt="字幕依据",
        url="https://example.test/video?t=11",
        start_sec=11,
    )
    return AgentStreamEvent(
        "section_started",
        request_id or envelope.request_id,
        envelope.message_id,
        section_id="section-1",
        status="grounded",
        citation_ids=(11,),
        citations=(citation,),
    )


class _MismatchedCorrelationChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield _section_started(envelope)
        yield AgentStreamEvent(
            "text_delta",
            "wrong-request",
            envelope.message_id,
            section_id="section-1",
            text="不应公开",
        )


class _OpenSectionEofChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield _section_started(envelope)


class _OpenSectionSlowChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield _section_started(envelope)
        await asyncio.sleep(0.05)


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite://",
        "notebook_agent_env": "development",
        "web_auth_enabled": True,
        "web_public_origin": ORIGIN,
        "web_auth_secret": "x" * 32,
        "browser_companion_allowed_origins": (),
        "email_provider": None,
        "agent_streaming_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def _app(settings: Settings, channel: _ChannelService):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        AppUser.__table__,
        ChannelIdentity.__table__,
        WebAuthChallenge.__table__,
        WebSession.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    sender = InMemoryEmailSender()
    auth: WebAuthService = WebAuthService(
        factory, settings, sender, InMemoryLoginRateLimiter(settings)
    )
    return create_web_app(
        settings=settings,
        session_factory=factory,
        auth_service=auth,
        channel_service=channel,
    ), sender


async def _authenticated_client(settings: Settings, channel: _ChannelService):
    app, sender = _app(settings, channel)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url=ORIGIN)
    origin = {"Origin": ORIGIN}
    challenge = await client.post(
        "/api/v1/auth/challenges",
        json={"email": "streaming@example.test"},
        headers=origin,
    )
    assert challenge.status_code == 200
    verify = await client.post(
        "/api/v1/auth/verify",
        json={"email": "streaming@example.test", "code": sender.messages[-1].code},
        headers=origin,
    )
    assert verify.status_code == 200
    return client, origin, client.cookies.get(CSRF_COOKIE_NAME), channel


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_agent_streaming_enabled_accepts_true_environment_values(monkeypatch, value):
    monkeypatch.setenv("AGENT_STREAMING_ENABLED", value)
    assert Settings(database_url="sqlite://", notebook_agent_env="development").agent_streaming_enabled


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_agent_streaming_enabled_accepts_false_environment_values(monkeypatch, value):
    monkeypatch.setenv("AGENT_STREAMING_ENABLED", value)
    assert not Settings(database_url="sqlite://", notebook_agent_env="development").agent_streaming_enabled


def test_agent_streaming_enabled_defaults_on_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("AGENT_STREAMING_ENABLED", raising=False)
    assert Settings(database_url="sqlite://", notebook_agent_env="development").agent_streaming_enabled
    monkeypatch.setenv("AGENT_STREAMING_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="AGENT_STREAMING_ENABLED"):
        Settings(database_url="sqlite://", notebook_agent_env="development")


@pytest.mark.asyncio
async def test_stream_emits_ordered_safe_sse_events_and_keeps_json_route():
    settings = _settings()
    channel = _ChannelService()
    client, origin, csrf, channel = await _authenticated_client(settings, channel)
    headers = {**origin, "X-CSRF-Token": csrf}
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-1", "text": "hello"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        records = [
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        events = [json.loads(record) for record in records]
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "activity",
            "text_delta",
            "completed",
        ]
        assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
        assert len({event["request_id"] for event in events}) == 1
        assert events[-1]["response"]["text"] == "这是一个分段传输的安全答案。"
        assert events[1]["activity"] == "retrieving"
        assert "PRIVATE" not in response.text

        json_response = await client.post(
            "/api/v1/conversations/browser-thread/messages",
            json={"message_id": "message-2", "text": "hello"},
            headers=headers,
        )
        assert json_response.status_code == 200
        assert json_response.json()["text"] == "这是一个分段传输的安全答案。"
        assert len(channel.envelopes) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_projects_section_lifecycle_and_citations_before_deltas():
    settings = _settings()
    channel = _SectionStreamingChannel()
    client, origin, csrf, channel = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-sections", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "activity",
            "section_started",
            "text_delta",
            "text_delta",
            "section_completed",
            "section_started",
            "text_delta",
            "section_completed",
            "completed",
        ]
        first_delta = next(index for index, event in enumerate(events) if event["type"] == "text_delta")
        assert events[first_delta - 1]["type"] == "section_started"
        assert events[first_delta - 1]["citation_ids"] == [11]
        assert events[first_delta - 1]["citations"][0]["title"] == "公开来源"
        assert events[first_delta]["section_id"] == "section-1"
        assert events[first_delta + 1]["text"] == "第二段"
        unsupported = next(event for event in events if event.get("section_id") == "section-2" and event["type"] == "section_started")
        assert unsupported["status"] == "unsupported"
        assert unsupported["citation_ids"] == []
        assert events[-1]["response"]["text"] == "第一段第二段\n\n当前检索证据不足以确认该部分。"
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert len(channel.envelopes) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_provider_fallback_keeps_one_delta_with_handle_stream():
    settings = _settings()
    channel = _CompletedOnlyChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-fallback", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "text_delta",
            "completed",
        ]
        assert events[-2]["text"] == "这是一个分段传输的安全答案。"
        assert len(channel.envelopes) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_rejects_internal_correlation_mismatch_after_aborting_section():
    settings = _settings()
    channel = _MismatchedCorrelationChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-correlation", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "section_started",
            "section_aborted",
            "error",
        ]
        assert events[-2]["reason"] == "provider_failure"
        assert events[-1]["error_code"] == "request_failed"
        assert "不应公开" not in response.text
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_eof_after_open_section_aborts_before_error():
    settings = _settings()
    channel = _OpenSectionEofChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-eof", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-2]["type"] == "section_aborted"
        assert events[-1]["type"] == "error"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_timeout_after_open_section_aborts_before_error(monkeypatch):
    import app.api.conversation_routes as conversation_routes

    monkeypatch.setattr(
        conversation_routes,
        "_web_agent_transport_timeout",
        lambda _settings: 0.01,
    )
    settings = _settings()
    channel = _OpenSectionSlowChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-timeout-section", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-2]["type"] == "section_aborted"
        assert events[-2]["reason"] == "timeout"
        assert events[-1]["type"] == "error"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_disabled_returns_safe_negotiation_error_without_invoking_service():
    settings = _settings(agent_streaming_enabled=False)
    channel = _ChannelService()
    client, origin, csrf, channel = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-disabled", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 406
        assert response.json()["code"] == "streaming_disabled"
        assert channel.envelopes == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_cancelled_answer_has_terminal_cancelled_event():
    class CancelledChannel(_ChannelService):
        async def handle(self, envelope):
            self.envelopes.append(envelope)
            raise asyncio.CancelledError

    settings = _settings()
    channel = CancelledChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-cancelled", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert events[-1]["type"] == "cancelled"
        assert events[-1]["error_code"] == "cancelled"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_provider_failure_is_safe_and_terminal():
    settings = _settings()
    channel = _BrokenChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-provider-failed", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == "request_failed"
        assert "PRIVATE_SENTINEL" not in response.text
        assert events[-1]["response"]["text"] == "请求无法完成，请稍后重试。"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_projects_unknown_answer_error_code_to_safe_value():
    settings = _settings()
    channel = _ChannelService(
        answer=AgentAnswer(
            status="failed",
            text="请求无法完成，请稍后重试。",
            error_code="PRIVATE_ERROR_CODE",
        )
    )
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-private-error", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert "PRIVATE_ERROR_CODE" not in response.text
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == "request_failed"
        assert events[-1]["response"]["error_code"] == "request_failed"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_timeout_is_safe_and_terminal_without_partial_answer():
    settings = _settings(agent_timeout_seconds=0.001)
    channel = _SlowChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-timeout", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == "timeout"
        assert events[-1]["response"]["text"] == "请求超时，请稍后重试。"
        assert all(event["type"] != "completed" for event in events)
    finally:
        await client.aclose()
