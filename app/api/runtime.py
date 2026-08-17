"""Production composition for the same-origin Web API and static SPA."""

from __future__ import annotations

from datetime import timedelta

from app.api.app import WebApiServices, create_app
from app.api.conversation_routes import resolve_browser_session_identity
from app.api.email_auth_routes import EmailWebAuthAdapter
from app.config import Settings, get_settings
from app.browser_capture_submission import BrowserCaptureSubmissionService
from app.browser_companion import BrowserCompanionService
from app.db import get_session_factory
from app.ingest.submission import build_ingest_submission_service
from app.object_store import RawObjectStore
from app.web.auth import WebAuthService
from app.web_auth import build_email_auth_service
from app.web.library import ContentLibraryService
from app.web.transcript import TranscriptService


class _LazyChannelService:
    """Defer Agent/provider construction until a compatibility route is used."""

    def __init__(self, settings, session_factory) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._service = None

    def _get(self):
        if self._service is None:
            from app.bootstrap import build_channel_service

            self._service = build_channel_service(
                self._settings,
                session_factory=self._session_factory,
            )
        return self._service

    async def handle(self, envelope):
        return await self._get().handle(envelope)


def build_web_app(
    settings: Settings | None = None,
    *,
    session_factory=None,
    publisher=None,
    object_store=None,
    email_auth=None,
    channel_service=None,
    mount_static: bool | None = None,
):
    """Wire concrete services while keeping test doubles explicit and local."""

    settings = settings or get_settings()
    if not settings.web_cookie_secure:
        raise ValueError("WEB_COOKIE_SECURE must stay enabled for __Host- cookies")
    factory = session_factory or get_session_factory()
    if publisher is None:
        from app.ingest.tasks import publish_ingest_dispatch

        publisher = publish_ingest_dispatch
    store = object_store or RawObjectStore()
    submission = build_ingest_submission_service(factory, publisher, settings)
    browser_companion = BrowserCompanionService(
        factory,
        pairing_ttl=timedelta(
            seconds=getattr(settings, "browser_companion_pairing_ttl_seconds", 600)
        ),
        grant_ttl=timedelta(
            seconds=getattr(
                settings,
                "browser_companion_grant_ttl_seconds",
                90 * 24 * 60 * 60,
            )
        ),
    )
    browser_capture_submission = BrowserCaptureSubmissionService(
        factory,
        publisher,
        store,
        quota_policy=submission.quota_policy,
        max_raw_bytes=getattr(
            settings, "ingest_max_raw_transcript_bytes", 5_000_000
        ),
        max_cues=getattr(settings, "ingest_max_cues_per_item", 50_000),
        max_text_chars=getattr(
            settings, "ingest_max_text_chars_per_item", 1_000_000
        ),
        trash_retention_days=getattr(settings, "trash_retention_days", 30),
    )
    email_enabled = bool(getattr(settings, "web_auth_enabled", False))
    if email_enabled:
        # Tests and embedders may inject the deterministic in-memory service;
        # production still builds the configured provider-backed service here.
        email_auth = email_auth or build_email_auth_service(settings, factory)
        web_auth = EmailWebAuthAdapter(email_auth)
        expected_origin = getattr(settings, "web_public_origin", None) or ""
        public_login_channels = ("email",)
    else:
        # The old channel-approved service remains injectable for existing
        # embedders and migration-era tests, but the deployed runtime enables
        # email OTP and never registers this as its public login flow.
        if getattr(settings, "notebook_agent_env", "development") == "production":
            raise ValueError(
                "WEB_AUTH_ENABLED must stay enabled for the production Web server"
            )
        settings.validate_web_auth()
        if not settings.web_origin:
            raise ValueError("WEB_ORIGIN is required for the Web server")
        email_auth = None
        web_auth = WebAuthService(
            factory,
            secret=settings.web_auth_secret,
            challenge_ttl=timedelta(seconds=settings.web_auth_challenge_ttl_seconds),
            session_ttl=timedelta(seconds=settings.web_auth_session_ttl_seconds),
            attempt_limit=settings.web_auth_attempt_limit,
            enabled_channels=settings.web_login_channels,
            challenge_rate_window=timedelta(seconds=settings.web_auth_rate_window_seconds),
            challenge_rate_limit_per_requester=settings.web_auth_rate_limit_per_requester,
            challenge_global_rate_limit=settings.web_auth_global_rate_limit,
            challenge_active_limit_per_requester=settings.web_auth_active_challenge_limit,
            challenge_retention=timedelta(seconds=settings.web_auth_challenge_retention_seconds),
            session_retention=timedelta(seconds=settings.web_auth_session_retention_seconds),
        )
        expected_origin = settings.web_origin
        public_login_channels = settings.web_login_channels
    services = WebApiServices(
        web_auth=web_auth,
        email_auth=email_auth,
        channel_service=channel_service or _LazyChannelService(settings, factory),
        session_resolver=(email_auth or web_auth).resolve_session,
        session_identity_resolver=lambda session: resolve_browser_session_identity(
            session, factory
        ),
        session_factory=factory,
        settings=settings,
        trusted_proxy_hosts=getattr(settings, "web_trusted_proxy_hosts", ""),
        library=ContentLibraryService(
            factory,
            publisher,
            quota_policy=submission.quota_policy,
            save_enabled=True,
        ),
        submission=submission,
        transcript=TranscriptService(factory, store),
        browser_companion=browser_companion,
        browser_capture_submission=browser_capture_submission,
    )
    serve_static = (
        settings.web_serve_static
        if mount_static is None
        else mount_static
    )
    return create_app(
        services=services,
        expected_origin=expected_origin,
        cookie_secure=True,
        publish_budget_seconds=settings.web_publish_budget_seconds,
        save_enabled=True,
        web_login_channels=public_login_channels,
        static_dir=settings.web_static_dir if serve_static else None,
    )
