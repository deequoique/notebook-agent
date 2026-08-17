from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.types import AgentAnswer
from app.config import Settings
from app.models import AppUser, ChannelIdentity, WebAuthChallenge, WebSession
from app.web_api import SESSION_COOKIE_NAME, build_web_auth_service, create_web_app
from app.web.auth import CSRF_COOKIE_NAME
from app.web_auth import InMemoryEmailSender, InMemoryLoginRateLimiter, ResendEmailSender, SmtpEmailSender, WebAuthService


class _ChannelService:
    def __init__(self):
        self.envelopes = []

    async def handle(self, envelope):
        self.envelopes.append(envelope)
        return AgentAnswer(status="ok", text="ok")


@pytest.mark.asyncio
async def test_web_api_sets_secure_cookie_rejects_origin_and_builds_trusted_envelope():
    settings = Settings(
        database_url="sqlite://", notebook_agent_env="development", web_auth_enabled=True,
        web_public_origin="https://app.example.test", web_auth_secret="x" * 32,
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for table in (AppUser.__table__, ChannelIdentity.__table__, WebAuthChallenge.__table__, WebSession.__table__):
        table.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    sender = InMemoryEmailSender()
    auth = WebAuthService(factory, settings, sender, InMemoryLoginRateLimiter(settings))
    channels = _ChannelService()
    transport = httpx.ASGITransport(
        app=create_web_app(settings=settings, auth_service=auth, channel_service=channels)
    )
    origin = {"Origin": settings.web_public_origin}
    async with httpx.AsyncClient(
        transport=transport, base_url="https://app.example.test"
    ) as client:
        assert (
            await client.post("/api/v1/auth/challenges", json={"email": "web@example.test"})
        ).status_code == 403
        assert (
            await client.post(
                "/api/v1/auth/challenges", json={"email": "web@example.test"}, headers=origin
            )
        ).json() == {"status": "accepted"}
        response = await client.post(
            "/api/v1/auth/verify",
            json={"email": "web@example.test", "code": sender.messages[-1].code},
            headers=origin,
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        assert SESSION_COOKIE_NAME in client.cookies
        csrf = client.cookies.get(CSRF_COOKIE_NAME)
        assert csrf
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages",
            json={"message_id": "m1", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert channels.envelopes[-1].channel == "web"
        assert channels.envelopes[-1].external_user_id == "web@example.test"
        assert "access-control-allow-origin" not in response.headers
        response = await client.delete(
            "/api/v1/auth/session",
            headers={**origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 204
        assert SESSION_COOKIE_NAME in response.headers["set-cookie"]
        assert (await client.get("/api/v1/auth/session")).status_code == 401


def _email_settings(**changes) -> Settings:
    values = {
        "database_url": "sqlite://",
        "notebook_agent_env": "development",
        "web_auth_enabled": True,
        "web_public_origin": "https://app.example.test",
        "web_auth_secret": "x" * 32,
        # Keep provider-selection tests independent of any developer .env.
        "email_provider": None,
        "resend_api_key": None,
        "resend_from_email": None,
        "smtp_host": None,
        "smtp_username": None,
        "smtp_password": None,
        "smtp_from_email": None,
    }
    values.update(changes)
    return Settings(**values)


def test_email_provider_selector_keeps_development_without_provider_in_memory():
    service = build_web_auth_service(_email_settings(), session_factory=lambda: None)
    assert isinstance(service._sender, InMemoryEmailSender)


@pytest.mark.parametrize(
    ("provider", "sender_type", "settings"),
    [
        (
            "resend",
            ResendEmailSender,
            {"resend_api_key": "test-resend-key", "resend_from_email": "sender@example.test"},
        ),
        (
            "smtp",
            SmtpEmailSender,
            {
                "smtp_host": "smtp.example.test",
                "smtp_username": "smtp-user",
                "smtp_password": "smtp-password",
                "smtp_from_email": "sender@example.test",
            },
        ),
    ],
)
def test_email_provider_selector_uses_explicit_provider(provider, sender_type, settings):
    service = build_web_auth_service(
        _email_settings(email_provider=provider, **settings), session_factory=lambda: None
    )
    assert isinstance(service._sender, sender_type)


def test_production_web_auth_requires_explicit_valid_provider():
    common = {
        "database_url": "sqlite://",
        "notebook_agent_env": "production",
        "notebook_agent_log_retrieval_content": False,
        "web_auth_enabled": True,
        "web_public_origin": "https://app.example.test",
        "web_auth_secret": "x" * 32,
    }
    with pytest.raises(ValueError, match="EMAIL_PROVIDER"):
        Settings(**common, email_provider=None)
    with pytest.raises(ValueError, match="EMAIL_PROVIDER"):
        Settings(**common, email_provider="unknown")


def test_smtp_config_does_not_require_resend_and_validates_its_own_fields():
    settings = _email_settings(
        notebook_agent_env="production",
        notebook_agent_log_retrieval_content=False,
        email_provider="smtp",
        smtp_host="smtp.example.test",
        smtp_username="smtp-user",
        smtp_password="smtp-password",
        smtp_from_email="sender@example.test",
    )
    assert settings.email_provider == "smtp"
    with pytest.raises(ValueError, match="SMTP_PASSWORD"):
        _email_settings(
            email_provider="smtp",
            smtp_host="smtp.example.test",
            smtp_username="smtp-user",
            smtp_from_email="sender@example.test",
        )
