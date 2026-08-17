"""Compatibility import surface for the canonical browser application.

Historically this module built a second FastAPI application containing Web
conversation, link-token, and authentication routes.  The production browser
application now lives exclusively in :mod:`app.api.app`; these helpers only
preserve old import spellings while delegating composition to that app.
"""

from __future__ import annotations

from app.api.app import WebApiServices, create_app
from app.api.conversation_routes import resolve_browser_session_identity
from app.api.email_auth_routes import EmailWebAuthAdapter
from app.config import Settings, get_settings
from app.web.auth import SESSION_COOKIE_NAME
from app.web_auth import WebAuthService, build_email_auth_service


def build_web_auth_service(settings: Settings, session_factory=None) -> WebAuthService:
    """Compatibility alias for the canonical email auth service factory."""

    return build_email_auth_service(settings, session_factory)


def create_web_api(
    *,
    settings: Settings | None = None,
    auth_service: WebAuthService | None = None,
    channel_service=None,
):
    """Return the canonical FastAPI browser app.

    Conversation/link compatibility routes are mounted by ``create_app`` and
    receive the same session resolver, cookie, Origin, and CSRF middleware as
    the library routes.  This shim owns no route handlers or auth semantics.
    """

    settings = settings or get_settings()
    auth_service = auth_service or build_web_auth_service(settings)
    session_factory = getattr(auth_service, "_session_factory", None)
    origin = getattr(settings, "web_public_origin", None) or getattr(
        settings, "web_origin", ""
    )
    if not origin:
        raise ValueError("WEB_PUBLIC_ORIGIN or WEB_ORIGIN is required")
    return create_app(
        services=WebApiServices(
            web_auth=EmailWebAuthAdapter(auth_service),
            library=object(),
            submission=object(),
            transcript=object(),
            email_auth=auth_service,
            channel_service=channel_service,
            session_resolver=auth_service.resolve_session,
            session_identity_resolver=lambda session: resolve_browser_session_identity(
                session, session_factory
            ),
            session_factory=session_factory,
            settings=settings,
            trusted_proxy_hosts=getattr(settings, "web_trusted_proxy_hosts", ""),
        ),
        expected_origin=origin,
        cookie_secure=True,
        publish_budget_seconds=float(
            getattr(settings, "web_publish_budget_seconds", 5.0)
        ),
        save_enabled=True,
        web_login_channels=("email",),
        static_dir=None,
    )


class CombinedASGIApp:
    """Compatibility dispatcher that keeps MCP bearer auth separate."""

    def __init__(self, web_app, mcp_app, mcp_path: str) -> None:
        self.web_app, self.mcp_app, self.mcp_path = web_app, mcp_app, mcp_path

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await self.mcp_app(scope, receive, send)
            return
        path = scope.get("path", "")
        target = (
            self.mcp_app
            if path == self.mcp_path or path.startswith(self.mcp_path + "/")
            else self.web_app
        )
        await target(scope, receive, send)


def create_combined_asgi_app(
    *, mcp_app, settings: Settings | None = None, web_app=None
):
    settings = settings or get_settings()
    return CombinedASGIApp(
        web_app or create_web_api(settings=settings),
        mcp_app,
        settings.mcp_path,
    )


def create_web_app(
    *,
    settings: Settings | None = None,
    session_factory=None,
    channel_service=None,
    auth_service: WebAuthService | None = None,
):
    """Legacy factory spelling delegating to :func:`create_web_api`."""

    settings = settings or get_settings()
    auth_service = auth_service or build_web_auth_service(settings, session_factory)
    return create_web_api(
        settings=settings,
        auth_service=auth_service,
        channel_service=channel_service,
    )


__all__ = [
    "CombinedASGIApp",
    "SESSION_COOKIE_NAME",
    "build_web_auth_service",
    "create_combined_asgi_app",
    "create_web_api",
    "create_web_app",
]
