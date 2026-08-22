"""Application composition root for CLI and channel gateway processes."""

from __future__ import annotations

from datetime import timedelta

from app.agent.actions import AgentActionServices
from app.agent.provider import build_model, model_supports_streaming
from app.agent.runtime import KnowledgeAgent
from app.agent.services import KnowledgeServices
from app.agent.management import KnowledgeItemManagementService
from app.channels.service import ChannelService
from app.channels.pending_actions import PendingConfirmationService
from app.config import Settings, get_settings
from app.db import get_session_factory
from app.ingest.embed import EmbeddingProvider, ZhipuEmbedder
from app.ingest.submission import build_ingest_submission_service
from app.ingest.tasks import publish_ingest_dispatch
from app.tls import TrustedCA, configure_trusted_ca
from app.web.auth import WebAuthService


def build_embedding_provider(
    settings: Settings, *, trusted_ca: TrustedCA | None = None
) -> EmbeddingProvider | None:
    """Build the only embedding configuration used by CLI and channel requests."""

    trusted_ca = trusted_ca or configure_trusted_ca(settings.tls_ca_bundle)
    if settings.embedding_dimensions < 1:
        raise ValueError("embedding dimensions must be positive")
    if not settings.zhipu_api_key:
        return None
    return ZhipuEmbedder(
        settings.zhipu_api_key,
        model=settings.embedding_model,
        endpoint=settings.embedding_endpoint,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        ssl_context=trusted_ca.ssl_context,
    )


def build_knowledge_agent(
    settings: Settings,
    *,
    session_factory=None,
) -> KnowledgeAgent:
    factory = session_factory or get_session_factory()
    trusted_ca = configure_trusted_ca(settings.tls_ca_bundle)
    embedder = build_embedding_provider(settings, trusted_ca=trusted_ca)

    def service_factory(request):
        return KnowledgeServices(request.tenant, factory, embedder=embedder)

    action_services = AgentActionServices(
        submission=build_ingest_submission_service(
            factory, publish_ingest_dispatch, settings
        ),
        pending=PendingConfirmationService(factory),
        management=KnowledgeItemManagementService(
            factory, retention_days=settings.trash_retention_days
        ),
    )

    def action_factory(_request):
        return action_services

    # ``configure_trusted_ca`` exports the resolved bundle through the standard
    # Python TLS environment before provider construction.  Do not create a
    # per-model httpx client here: the provider owns its default client and its
    # lifecycle, while the embedding client receives the same context directly.
    model = build_model(settings)
    # Provider-level streaming is opt-in by capability. String model names are
    # kept on the one-delta compatibility path because capability cannot be
    # established without making a provider request.
    stream_model = model if model_supports_streaming(model) else None
    return KnowledgeAgent(
        model,
        settings,
        service_factory,
        action_factory=action_factory,
        stream_model=stream_model,
    )


def build_channel_service(
    settings: Settings | None = None,
    *,
    web_auth: WebAuthService | None = None,
    session_factory=None,
) -> ChannelService:
    settings = settings or get_settings()
    factory = session_factory or get_session_factory()
    agent = build_knowledge_agent(settings, session_factory=factory)
    if web_auth is None and settings.web_auth_secret:
        settings.validate_web_auth()
        web_auth = WebAuthService(
            factory,
            secret=settings.web_auth_secret,
            challenge_ttl=timedelta(
                seconds=settings.web_auth_challenge_ttl_seconds
            ),
            session_ttl=timedelta(
                seconds=settings.web_auth_session_ttl_seconds
            ),
            attempt_limit=settings.web_auth_attempt_limit,
            enabled_channels=settings.web_login_channels,
            challenge_rate_window=timedelta(
                seconds=settings.web_auth_rate_window_seconds
            ),
            challenge_rate_limit_per_requester=(
                settings.web_auth_rate_limit_per_requester
            ),
            challenge_global_rate_limit=settings.web_auth_global_rate_limit,
            challenge_active_limit_per_requester=(
                settings.web_auth_active_challenge_limit
            ),
            challenge_retention=timedelta(
                seconds=settings.web_auth_challenge_retention_seconds
            ),
            session_retention=timedelta(
                seconds=settings.web_auth_session_retention_seconds
            ),
        )
    return ChannelService(factory, agent, settings, web_auth=web_auth)


def build_mcp_server(
    settings: Settings | None = None,
    *,
    scope: str = "full",
    token: str | None = None,
):
    """Build the MCP adapter without selecting a process-wide AppUser.

    ``token`` is useful for a local stdio process whose operator has already
    selected one grant.  Streamable HTTP resolves the bearer per request in
    ``McpAuthMiddleware``; this helper never accepts an application-user id.
    """

    from app.mcp_grants import McpGrantService
    from app.mcp_server import McpToolFacade, create_mcp_server

    settings = settings or get_settings()
    factory = get_session_factory()
    grant_service = McpGrantService(factory)
    resolved = None
    if token:
        try:
            resolved = grant_service.resolve(token.strip())
        except Exception:
            raise RuntimeError("MCP token is invalid or unavailable") from None
        # A local stdio token is authoritative for discovery; do not build a
        # full mutation profile and hope invocation-time checks hide tools.
        scope = resolved.scope
    return create_mcp_server(
        scope=scope,
        facade=McpToolFacade(
            settings=settings,
            grant_service=grant_service,
            grant=resolved,
        ),
    )
