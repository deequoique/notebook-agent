"""Runtime configuration.

All values come from environment variables (optionally loaded from a local
`.env` file via python-dotenv). Nothing here is hardcoded — see
`.env.example` for the full list of variables and their defaults for local
docker-compose usage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
import ipaddress
import math
from urllib.parse import urlparse, urlsplit

from dotenv import load_dotenv

# Load .env once at import time. In production, real env vars should already
# be set and this is a no-op (load_dotenv does not override existing vars).
load_dotenv()


# The answer Composer allows one structured-output repair in a run. Keep this
# constant next to configuration validation so the provider cap and the
# post-response safety budget cannot drift apart.
COMPOSER_VALIDATION_REQUEST_LIMIT = 1


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_channels(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    channels = tuple(
        dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip())
    )
    unsupported = set(channels) - {"telegram", "wechat"}
    if not channels or unsupported:
        raise ValueError(f"{name} must contain only telegram and/or wechat")
    return channels


def _env_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    return tuple(
        dict.fromkeys(part.strip() for part in value.split(",") if part.strip())
    )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _validate_langbot_outbound_url(value: str) -> None:
    """Fail closed for an unsafe LangBot outbound endpoint."""

    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "LANGBOT_OUTBOUND_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "LANGBOT_OUTBOUND_BASE_URL must not contain credentials, query, or fragment"
        )
    host = parsed.hostname.lower().rstrip(".")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and parsed.scheme != "https":
        raise ValueError(
            "LANGBOT_OUTBOUND_BASE_URL must use HTTPS for non-loopback hosts"
        )


def _validate_youtube_proxy_url(value: str | None) -> None:
    """Restrict the temporary YouTube proxy contract to a local tunnel."""

    if value is None:
        return
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError(
            "YOUTUBE_PROXY_URL must be a credential-free loopback HTTP URL "
            "with an explicit port"
        )
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "YOUTUBE_PROXY_URL must be a credential-free loopback HTTP URL "
            "with an explicit port"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path
        or "?" in value
        or "#" in value
    ):
        raise ValueError(
            "YOUTUBE_PROXY_URL must be a credential-free loopback HTTP URL "
            "with an explicit port"
        )


@dataclass(frozen=True)
class Settings:
    # --- Private runtime diagnostics ---
    # The relative default intentionally resolves in the gateway working
    # directory. Production systemd sets this to /var/log/notebook-agent.
    notebook_agent_log_dir: str = field(
        default_factory=lambda: _env("NOTEBOOK_AGENT_LOG_DIR", ".runtime/logs")
        or ".runtime/logs"
    )
    notebook_agent_log_max_bytes: int = field(
        default_factory=lambda: _env_int("NOTEBOOK_AGENT_LOG_MAX_BYTES", 10 * 1024 * 1024)
    )
    notebook_agent_log_backup_count: int = field(
        default_factory=lambda: _env_int("NOTEBOOK_AGENT_LOG_BACKUP_COUNT", 5)
    )
    notebook_agent_env: str = field(
        default_factory=lambda: _env("NOTEBOOK_AGENT_ENV", "production") or "production"
    )
    notebook_agent_log_retrieval_content: bool = field(
        default_factory=lambda: _env_bool("NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT", False)
    )
    # --- Outbound TLS ---
    # Optional explicit CA bundle.  If unset, application composition uses
    # SSL_CERT_FILE/REQUESTS_CA_BUNDLE or certifi for the current interpreter.
    tls_ca_bundle: str | None = field(default_factory=lambda: _env("TLS_CA_BUNDLE"))

    # --- Zhipu Embedding-3 ---
    zhipu_api_key: str | None = field(default_factory=lambda: _env("ZHIPU_API_KEY"))
    embedding_model: str = field(
        default_factory=lambda: _env("EMBEDDING_MODEL", "embedding-3")
        or "embedding-3"
    )
    embedding_endpoint: str = field(
        default_factory=lambda: _env(
            "EMBEDDING_ENDPOINT",
            "https://open.bigmodel.cn/api/paas/v4/embeddings",
        )
        or "https://open.bigmodel.cn/api/paas/v4/embeddings"
    )
    embedding_dimensions: int = field(
        default_factory=lambda: _env_int("EMBEDDING_DIMENSIONS", 1536)
    )
    embedding_batch_size: int = field(
        default_factory=lambda: _env_int("EMBEDDING_BATCH_SIZE", 64)
    )

    # --- Postgres / SQLAlchemy ---
    database_url: str = field(default_factory=lambda: _build_database_url())

    # --- Redis (Celery broker + result backend) ---
    redis_url: str = field(default_factory=lambda: _build_redis_url())
    # The channel/Agent request must never wait indefinitely for the broker.
    # This is a total publish budget; the ingestion worker's retry policy is
    # intentionally separate and is not controlled by these values.
    broker_publish_timeout_seconds: float = field(
        default_factory=lambda: _env_float("BROKER_PUBLISH_TIMEOUT_SECONDS", 5.0)
    )
    broker_publish_max_retries: int = field(
        default_factory=lambda: _env_int("BROKER_PUBLISH_MAX_RETRIES", 1)
    )
    # Tenant-level cost guardrails apply to both Web and channel saves.  They
    # bound durable queue work and long-term storage; per-request batch limits
    # alone are not sufficient because a caller can submit many batches.
    ingest_max_active_per_user: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_ACTIVE_PER_USER", 10)
    )
    ingest_daily_new_item_limit: int = field(
        default_factory=lambda: _env_int("INGEST_DAILY_NEW_ITEM_LIMIT", 50)
    )
    ingest_max_items_per_user: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_ITEMS_PER_USER", 1000)
    )
    ingest_max_active_global: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_ACTIVE_GLOBAL", 100)
    )
    ingest_daily_new_item_limit_global: int = field(
        default_factory=lambda: _env_int(
            "INGEST_DAILY_NEW_ITEM_LIMIT_GLOBAL", 300
        )
    )
    ingest_daily_dispatch_limit_per_user: int = field(
        default_factory=lambda: _env_int(
            "INGEST_DAILY_DISPATCH_LIMIT_PER_USER", 100
        )
    )
    ingest_daily_dispatch_limit_global: int = field(
        default_factory=lambda: _env_int(
            "INGEST_DAILY_DISPATCH_LIMIT_GLOBAL", 1000
        )
    )
    ingest_max_raw_transcript_bytes: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_RAW_TRANSCRIPT_BYTES", 5_000_000)
    )
    ingest_max_cues_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_CUES_PER_ITEM", 50_000)
    )
    ingest_max_text_chars_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_TEXT_CHARS_PER_ITEM", 1_000_000)
    )
    ingest_max_segments_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_SEGMENTS_PER_ITEM", 5_000)
    )
    ingest_max_embedding_chars_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_EMBEDDING_CHARS_PER_ITEM", 2_000_000)
    )
    youtube_fetch_timeout_seconds: float = field(
        default_factory=lambda: _env_float("YOUTUBE_FETCH_TIMEOUT_SECONDS", 30.0)
    )
    youtube_proxy_url: str | None = field(
        default_factory=lambda: _env("YOUTUBE_PROXY_URL")
    )

    # --- MinIO (S3-compatible object storage) ---
    minio_endpoint_url: str = field(default_factory=lambda: _env("MINIO_ENDPOINT_URL", "http://localhost:9000"))
    minio_access_key: str | None = field(default_factory=lambda: _env("MINIO_ROOT_USER"))
    minio_secret_key: str | None = field(default_factory=lambda: _env("MINIO_ROOT_PASSWORD"))
    minio_bucket: str = field(default_factory=lambda: _env("MINIO_BUCKET", "kb-raw") or "kb-raw")

    # --- Knowledge retrieval Agent ---
    agent_model: str = field(
        default_factory=lambda: _env("AGENT_MODEL", "openai:gpt-5-mini")
        or "openai:gpt-5-mini"
    )
    agent_api_key: str | None = field(default_factory=lambda: _env("AGENT_API_KEY"))
    agent_base_url: str | None = field(default_factory=lambda: _env("AGENT_BASE_URL"))
    agent_timeout_seconds: float = field(
        default_factory=lambda: _env_float("AGENT_TIMEOUT_SECONDS", 45.0)
    )
    agent_tool_timeout_seconds: float = field(
        default_factory=lambda: _env_float(
            "AGENT_TOOL_TIMEOUT_SECONDS", 15.0
        )
    )
    agent_request_limit: int = field(
        default_factory=lambda: _env_int("AGENT_REQUEST_LIMIT", 8)
    )
    agent_tool_calls_limit: int = field(
        default_factory=lambda: _env_int("AGENT_TOOL_CALLS_LIMIT", 10)
    )
    agent_output_token_limit: int = field(
        default_factory=lambda: _env_int("AGENT_OUTPUT_TOKEN_LIMIT", 2000)
    )
    agent_composer_max_tokens: int = field(
        default_factory=lambda: _env_int("AGENT_COMPOSER_MAX_TOKENS", 1000)
    )
    trash_retention_days: int = field(
        default_factory=lambda: _env_int("TRASH_RETENTION_DAYS", 30)
    )
    trash_purge_interval_seconds: int = field(
        default_factory=lambda: _env_int("TRASH_PURGE_INTERVAL_SECONDS", 3600)
    )
    trash_purge_batch_size: int = field(
        default_factory=lambda: _env_int("TRASH_PURGE_BATCH_SIZE", 20)
    )
    trash_purge_claim_timeout_seconds: int = field(
        default_factory=lambda: _env_int("TRASH_PURGE_CLAIM_TIMEOUT_SECONDS", 1800)
    )
    trash_purge_max_duration_seconds: float = field(
        default_factory=lambda: _env_float("TRASH_PURGE_MAX_DURATION_SECONDS", 30.0)
    )
    trash_purge_object_timeout_seconds: float = field(
        default_factory=lambda: _env_float("TRASH_PURGE_OBJECT_TIMEOUT_SECONDS", 10.0)
    )
    # Ingestion completion outbox repair is deliberately separate from the
    # recycle-bin purge sweep.  Both run on the maintenance queue, but the
    # completion settings have their own bounded claim and wall-clock budget.
    ingest_completion_interval_seconds: int = field(
        default_factory=lambda: _env_int(
            "INGEST_COMPLETION_INTERVAL_SECONDS",
            _env_int("INGEST_COMPLETION_PUBLISH_INTERVAL_SECONDS", 60),
        )
    )
    ingest_completion_batch_size: int = field(
        default_factory=lambda: _env_int("INGEST_COMPLETION_BATCH_SIZE", 20)
    )
    ingest_completion_claim_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            "INGEST_COMPLETION_CLAIM_TIMEOUT_SECONDS", 300
        )
    )
    ingest_completion_max_duration_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_COMPLETION_MAX_DURATION_SECONDS", 30.0
        )
    )
    # PostgreSQL-backed source-channel completion notification poller.  The
    # legacy ingest-completion publisher settings above remain for rollback
    # compatibility but are no longer scheduled by the worker.
    ingest_notification_interval_seconds: int = field(
        default_factory=lambda: _env_int("INGEST_NOTIFICATION_INTERVAL_SECONDS", 10)
    )
    ingest_notification_batch_size: int = field(
        default_factory=lambda: _env_int("INGEST_NOTIFICATION_BATCH_SIZE", 20)
    )
    ingest_notification_claim_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            "INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS", 300
        )
    )
    ingest_notification_max_duration_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_NOTIFICATION_MAX_DURATION_SECONDS", 8.0
        )
    )
    ingest_notification_max_attempts: int = field(
        default_factory=lambda: _env_int("INGEST_NOTIFICATION_MAX_ATTEMPTS", 5)
    )
    ingest_notification_retry_base_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_NOTIFICATION_RETRY_BASE_SECONDS", 5.0
        )
    )
    ingest_notification_retry_max_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_NOTIFICATION_RETRY_MAX_SECONDS", 300.0
        )
    )
    langbot_outbound_base_url: str = field(
        default_factory=lambda: _env(
            "LANGBOT_OUTBOUND_BASE_URL", "http://127.0.0.1:5300"
        )
        or "http://127.0.0.1:5300"
    )
    langbot_outbound_api_key: str | None = field(
        default_factory=lambda: _env("LANGBOT_OUTBOUND_API_KEY")
    )
    langbot_outbound_timeout_seconds: float = field(
        default_factory=lambda: _env_float("LANGBOT_OUTBOUND_TIMEOUT_SECONDS", 10.0)
    )
    context_max_turns: int = field(
        default_factory=lambda: _env_int("CONTEXT_MAX_TURNS", 8)
    )
    context_token_budget: int = field(
        default_factory=lambda: _env_int("CONTEXT_TOKEN_BUDGET", 6000)
    )
    channel_link_ttl_seconds: int = field(
        default_factory=lambda: _env_int("CHANNEL_LINK_TTL_SECONDS", 600)
    )
    channel_gateway_secret: str | None = field(
        default_factory=lambda: _env("CHANNEL_GATEWAY_SECRET")
    )
    channel_gateway_host: str = field(
        default_factory=lambda: _env("CHANNEL_GATEWAY_HOST", "127.0.0.1")
        or "127.0.0.1"
    )
    channel_gateway_port: int = field(
        default_factory=lambda: _env_int("CHANNEL_GATEWAY_PORT", 8765)
    )

    # --- MCP transport ---
    # These settings are intentionally independent from the private LangBot
    # bridge.  MCP startup never requires CHANNEL_GATEWAY_SECRET.
    mcp_host: str = field(
        default_factory=lambda: _env("MCP_HOST", "127.0.0.1") or "127.0.0.1"
    )
    mcp_port: int = field(default_factory=lambda: _env_int("MCP_PORT", 8000))
    mcp_path: str = field(
        default_factory=lambda: _env("MCP_PATH", "/mcp") or "/mcp"
    )
    mcp_url_token_mode: bool = field(
        default_factory=lambda: _env_bool("MCP_URL_TOKEN_MODE", False)
    )

    # --- Same-origin Web auth ---
    web_auth_secret: str | None = field(
        default_factory=lambda: _env("WEB_AUTH_SECRET")
    )
    web_auth_challenge_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_CHALLENGE_TTL_SECONDS", 600)
    )
    web_auth_session_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_SESSION_TTL_SECONDS", 2592000)
    )
    web_auth_attempt_limit: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_ATTEMPT_LIMIT", 5)
    )
    web_auth_rate_window_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_RATE_WINDOW_SECONDS", 60)
    )
    web_auth_rate_limit_per_requester: int = field(
        default_factory=lambda: _env_int(
            "WEB_AUTH_RATE_LIMIT_PER_REQUESTER", 5
        )
    )
    web_auth_global_rate_limit: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_GLOBAL_RATE_LIMIT", 100)
    )
    web_auth_active_challenge_limit: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_ACTIVE_CHALLENGE_LIMIT", 3)
    )
    web_auth_challenge_retention_seconds: int = field(
        default_factory=lambda: _env_int(
            "WEB_AUTH_CHALLENGE_RETENTION_SECONDS", 86400
        )
    )
    web_auth_session_retention_seconds: int = field(
        default_factory=lambda: _env_int(
            "WEB_AUTH_SESSION_RETENTION_SECONDS", 604800
        )
    )
    web_cookie_secure: bool = field(
        default_factory=lambda: _env_bool("WEB_COOKIE_SECURE", True)
    )
    web_origin: str | None = field(default_factory=lambda: _env("WEB_ORIGIN"))
    web_login_channels: tuple[str, ...] = field(
        default_factory=lambda: _env_channels(
            "WEB_LOGIN_CHANNELS", ("telegram", "wechat")
        )
    )
    web_host: str = field(
        default_factory=lambda: _env("WEB_HOST", "127.0.0.1") or "127.0.0.1"
    )
    web_port: int = field(
        default_factory=lambda: _env_int("WEB_PORT", 8000)
    )
    web_serve_static: bool = field(
        default_factory=lambda: _env_bool("WEB_SERVE_STATIC", True)
    )
    web_static_dir: str = field(
        default_factory=lambda: _env("WEB_STATIC_DIR", "web/dist") or "web/dist"
    )
    web_publish_budget_seconds: float = field(
        default_factory=lambda: _env_float("WEB_PUBLISH_BUDGET_SECONDS", 5.0)
    )
    web_forwarded_allow_ips: str = field(
        default_factory=lambda: _env("WEB_FORWARDED_ALLOW_IPS", "127.0.0.1")
        or "127.0.0.1"
    )

    # --- Same-origin Web API / email login ---
    # Disabled by default so existing channel-only deployments do not acquire
    # an email-provider dependency merely by upgrading the application.
    web_auth_enabled: bool = field(
        default_factory=lambda: _env_bool("WEB_AUTH_ENABLED", False)
    )
    web_api_prefix: str = field(
        default_factory=lambda: _env("WEB_API_PREFIX", "/api/v1") or "/api/v1"
    )
    web_public_origin: str | None = field(
        default_factory=lambda: _env("WEB_PUBLIC_ORIGIN")
    )
    browser_companion_allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: _env_csv("BROWSER_COMPANION_ALLOWED_ORIGINS")
    )
    browser_companion_pairing_ttl_seconds: int = field(
        default_factory=lambda: _env_int(
            "BROWSER_COMPANION_PAIRING_TTL_SECONDS", 600
        )
    )
    browser_companion_grant_ttl_seconds: int = field(
        default_factory=lambda: _env_int(
            "BROWSER_COMPANION_GRANT_TTL_SECONDS", 90 * 24 * 60 * 60
        )
    )
    browser_companion_max_request_bytes: int = field(
        default_factory=lambda: _env_int(
            "BROWSER_COMPANION_MAX_REQUEST_BYTES", 5_500_000
        )
    )
    web_session_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_SESSION_TTL_SECONDS", 30 * 24 * 60 * 60)
    )
    web_auth_code_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_CODE_TTL_SECONDS", 600)
    )
    web_auth_max_attempts: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_MAX_ATTEMPTS", 5)
    )
    web_auth_resend_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_RESEND_SECONDS", 60)
    )
    web_auth_email_window_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_EMAIL_WINDOW_SECONDS", 900)
    )
    web_auth_email_max_sends: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_EMAIL_MAX_SENDS", 3)
    )
    web_auth_ip_window_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_IP_WINDOW_SECONDS", 3600)
    )
    web_auth_ip_max_sends: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_IP_MAX_SENDS", 10)
    )
    # An unset provider deliberately keeps development/test mail in-process.
    # Production Web authentication must select an explicit network provider.
    email_provider: str | None = field(default_factory=lambda: _env("EMAIL_PROVIDER"))
    resend_api_key: str | None = field(default_factory=lambda: _env("RESEND_API_KEY"))
    resend_from_email: str | None = field(default_factory=lambda: _env("RESEND_FROM_EMAIL"))
    resend_timeout_seconds: float = field(
        default_factory=lambda: _env_float("RESEND_TIMEOUT_SECONDS", 10.0)
    )
    smtp_host: str | None = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    smtp_username: str | None = field(default_factory=lambda: _env("SMTP_USERNAME"))
    smtp_password: str | None = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_from_email: str | None = field(default_factory=lambda: _env("SMTP_FROM_EMAIL"))
    smtp_starttls: bool = field(
        default_factory=lambda: _env_bool("SMTP_STARTTLS", True)
    )
    smtp_timeout_seconds: float = field(
        default_factory=lambda: _env_float("SMTP_TIMEOUT_SECONDS", 10.0)
    )
    web_trusted_proxy_hosts: str = field(
        default_factory=lambda: _env("WEB_TRUSTED_PROXY_HOSTS", "") or ""
    )

    def __post_init__(self) -> None:
        if self.notebook_agent_env not in {"development", "production"}:
            raise ValueError("NOTEBOOK_AGENT_ENV must be development or production")
        if (
            self.notebook_agent_log_retrieval_content
            and self.notebook_agent_env != "development"
        ):
            raise ValueError(
                "retrieval content logging requires NOTEBOOK_AGENT_ENV=development"
            )
        if not self.mcp_host.strip():
            raise ValueError("MCP_HOST must not be empty")
        if self.mcp_port < 1 or self.mcp_port > 65535:
            raise ValueError("MCP_PORT must be between 1 and 65535")
        if (
            not self.mcp_path.startswith("/")
            or self.mcp_path == "/"
            or "?" in self.mcp_path
            or "#" in self.mcp_path
        ):
            raise ValueError(
                "MCP_PATH must be an absolute path without query or fragment"
            )
        if self.mcp_path != "/" and self.mcp_path.endswith("/"):
            raise ValueError("MCP_PATH must not have a trailing slash")
        if self.web_api_prefix != "/api/v1":
            raise ValueError("WEB_API_PREFIX is fixed at /api/v1")
        positive_web_values = (
            self.web_session_ttl_seconds,
            self.web_auth_code_ttl_seconds,
            self.web_auth_max_attempts,
            self.web_auth_resend_seconds,
            self.web_auth_email_window_seconds,
            self.web_auth_email_max_sends,
            self.web_auth_ip_window_seconds,
            self.web_auth_ip_max_sends,
            self.browser_companion_pairing_ttl_seconds,
            self.browser_companion_grant_ttl_seconds,
            self.browser_companion_max_request_bytes,
        )
        if (
            any(value <= 0 for value in positive_web_values)
            or self.resend_timeout_seconds <= 0
            or self.smtp_timeout_seconds <= 0
        ):
            raise ValueError("Web authentication durations and limits must be positive")
        for extension_origin in self.browser_companion_allowed_origins:
            if extension_origin == "chrome-extension://*":
                if (
                    self.notebook_agent_env != "development"
                    or self.web_host not in {"127.0.0.1", "localhost", "::1"}
                ):
                    raise ValueError(
                        "wildcard browser companion origins require a loopback-only "
                        "development Web server"
                    )
                continue
            parsed_extension = urlsplit(extension_origin)
            extension_id = parsed_extension.netloc
            if (
                parsed_extension.scheme != "chrome-extension"
                or len(extension_id) != 32
                or any(character not in "abcdefghijklmnop" for character in extension_id)
                or parsed_extension.path
                or parsed_extension.query
                or parsed_extension.fragment
                or extension_origin.endswith("/")
            ):
                raise ValueError(
                    "BROWSER_COMPANION_ALLOWED_ORIGINS must contain exact "
                    "chrome-extension origins, or the development-only "
                    "chrome-extension://* wildcard"
                )
        if self.web_auth_enabled:
            parsed_origin = urlsplit(self.web_public_origin or "")
            if (
                parsed_origin.scheme != "https"
                or not parsed_origin.netloc
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError("WEB_PUBLIC_ORIGIN must be an HTTPS origin when Web auth is enabled")
            if self.web_public_origin.rstrip("/") != self.web_public_origin:
                raise ValueError("WEB_PUBLIC_ORIGIN must not have a trailing slash")
            if not self.web_auth_secret or len(self.web_auth_secret) < 32:
                raise ValueError("WEB_AUTH_SECRET must be at least 32 characters")
            provider = (self.email_provider or "").strip().lower()
            if provider not in {"", "resend", "smtp"}:
                raise ValueError("EMAIL_PROVIDER must be resend or smtp")
            if self.notebook_agent_env == "production" and not provider:
                raise ValueError("production Web auth requires EMAIL_PROVIDER")
            if provider == "resend" and (
                not self.resend_api_key or not self.resend_from_email
            ):
                raise ValueError("EMAIL_PROVIDER=resend requires RESEND_API_KEY and RESEND_FROM_EMAIL")
            if provider == "smtp":
                if not all((
                    self.smtp_host,
                    self.smtp_username,
                    self.smtp_password,
                    self.smtp_from_email,
                )):
                    raise ValueError(
                        "EMAIL_PROVIDER=smtp requires SMTP_HOST, SMTP_USERNAME, "
                        "SMTP_PASSWORD, and SMTP_FROM_EMAIL"
                    )
                if not 1 <= self.smtp_port <= 65535:
                    raise ValueError("SMTP_PORT must be between 1 and 65535")
                smtp_header_values = (
                    self.smtp_host,
                    self.smtp_username,
                    self.smtp_from_email,
                )
                if any(
                    "\n" in value or "\r" in value for value in smtp_header_values
                ):
                    raise ValueError("SMTP settings must not contain line breaks")
        if self.agent_composer_max_tokens <= 0:
            raise ValueError("AGENT_COMPOSER_MAX_TOKENS must be positive")
        if self.trash_retention_days <= 0:
            raise ValueError("TRASH_RETENTION_DAYS must be positive")
        if self.trash_purge_interval_seconds <= 0:
            raise ValueError("TRASH_PURGE_INTERVAL_SECONDS must be positive")
        if self.trash_purge_batch_size <= 0 or self.trash_purge_batch_size > 100:
            raise ValueError("TRASH_PURGE_BATCH_SIZE must be between 1 and 100")
        if self.trash_purge_claim_timeout_seconds <= 0:
            raise ValueError("TRASH_PURGE_CLAIM_TIMEOUT_SECONDS must be positive")
        if self.trash_purge_max_duration_seconds <= 0:
            raise ValueError("TRASH_PURGE_MAX_DURATION_SECONDS must be positive")
        if self.trash_purge_object_timeout_seconds <= 0:
            raise ValueError("TRASH_PURGE_OBJECT_TIMEOUT_SECONDS must be positive")
        if self.ingest_completion_interval_seconds <= 0:
            raise ValueError("INGEST_COMPLETION_INTERVAL_SECONDS must be positive")
        if (
            self.ingest_completion_batch_size <= 0
            or self.ingest_completion_batch_size > 100
        ):
            raise ValueError("INGEST_COMPLETION_BATCH_SIZE must be between 1 and 100")
        if self.ingest_completion_claim_timeout_seconds <= 0:
            raise ValueError(
                "INGEST_COMPLETION_CLAIM_TIMEOUT_SECONDS must be positive"
            )
        if self.ingest_completion_max_duration_seconds <= 0:
            raise ValueError(
                "INGEST_COMPLETION_MAX_DURATION_SECONDS must be positive"
            )
        if min(
            self.ingest_max_raw_transcript_bytes,
            self.ingest_max_cues_per_item,
            self.ingest_max_text_chars_per_item,
            self.ingest_max_segments_per_item,
            self.ingest_max_embedding_chars_per_item,
        ) <= 0:
            raise ValueError("INGEST_CONTENT_LIMITS must be positive")
        if self.youtube_fetch_timeout_seconds <= 0:
            raise ValueError("YOUTUBE_FETCH_TIMEOUT_SECONDS must be positive")
        _validate_youtube_proxy_url(self.youtube_proxy_url)
        if self.ingest_notification_interval_seconds <= 0:
            raise ValueError("INGEST_NOTIFICATION_INTERVAL_SECONDS must be positive")
        if (
            self.ingest_notification_batch_size <= 0
            or self.ingest_notification_batch_size > 100
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_BATCH_SIZE must be between 1 and 100"
            )
        if self.ingest_notification_claim_timeout_seconds <= 0:
            raise ValueError(
                "INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS must be positive"
            )
        if (
            not math.isfinite(self.ingest_notification_max_duration_seconds)
            or self.ingest_notification_max_duration_seconds <= 0
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_MAX_DURATION_SECONDS must be positive"
            )
        if (
            self.ingest_notification_max_duration_seconds
            >= self.ingest_notification_interval_seconds
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_MAX_DURATION_SECONDS must be less than "
                "INGEST_NOTIFICATION_INTERVAL_SECONDS"
            )
        if self.ingest_notification_max_attempts <= 0:
            raise ValueError("INGEST_NOTIFICATION_MAX_ATTEMPTS must be positive")
        if (
            not math.isfinite(self.ingest_notification_retry_base_seconds)
            or self.ingest_notification_retry_base_seconds <= 0
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_RETRY_BASE_SECONDS must be positive"
            )
        if (
            not math.isfinite(self.ingest_notification_retry_max_seconds)
            or self.ingest_notification_retry_max_seconds <= 0
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_RETRY_MAX_SECONDS must be positive"
            )
        if (
            self.ingest_notification_retry_base_seconds
            > self.ingest_notification_retry_max_seconds
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_RETRY_BASE_SECONDS must not exceed "
                "INGEST_NOTIFICATION_RETRY_MAX_SECONDS"
            )
        if (
            not math.isfinite(self.langbot_outbound_timeout_seconds)
            or self.langbot_outbound_timeout_seconds <= 0
        ):
            raise ValueError("LANGBOT_OUTBOUND_TIMEOUT_SECONDS must be positive")
        _validate_langbot_outbound_url(self.langbot_outbound_base_url)
        if (
            self.agent_composer_max_tokens * COMPOSER_VALIDATION_REQUEST_LIMIT
            > self.agent_output_token_limit
        ):
            raise ValueError(
                "AGENT_COMPOSER_MAX_TOKENS multiplied by the Composer request "
                "limit must not exceed AGENT_OUTPUT_TOKEN_LIMIT"
            )

    def validate_web_auth(self) -> None:
        if self.web_auth_secret is None or len(self.web_auth_secret) < 32:
            raise ValueError("WEB_AUTH_SECRET must contain at least 32 characters")
        if self.web_auth_challenge_ttl_seconds <= 0:
            raise ValueError("WEB_AUTH_CHALLENGE_TTL_SECONDS must be positive")
        if self.web_auth_session_ttl_seconds <= 0:
            raise ValueError("WEB_AUTH_SESSION_TTL_SECONDS must be positive")
        if self.web_auth_attempt_limit <= 0:
            raise ValueError("WEB_AUTH_ATTEMPT_LIMIT must be positive")
        if min(
            self.web_auth_rate_window_seconds,
            self.web_auth_rate_limit_per_requester,
            self.web_auth_global_rate_limit,
            self.web_auth_active_challenge_limit,
            self.web_auth_challenge_retention_seconds,
            self.web_auth_session_retention_seconds,
        ) <= 0:
            raise ValueError("Web auth rate and retention limits must be positive")
        if (
            self.web_auth_challenge_retention_seconds
            < self.web_auth_rate_window_seconds
        ):
            raise ValueError(
                "WEB_AUTH_CHALLENGE_RETENTION_SECONDS must cover the rate window"
            )
        if not self.web_host.strip():
            raise ValueError("WEB_HOST must not be blank")
        if not (1 <= self.web_port <= 65535):
            raise ValueError("WEB_PORT must be between 1 and 65535")
        if not self.web_static_dir.strip():
            raise ValueError("WEB_STATIC_DIR must not be blank")
        if self.web_publish_budget_seconds <= 0:
            raise ValueError("WEB_PUBLISH_BUDGET_SECONDS must be positive")
        if min(
            self.ingest_max_active_per_user,
            self.ingest_daily_new_item_limit,
            self.ingest_max_items_per_user,
            self.ingest_max_active_global,
            self.ingest_daily_new_item_limit_global,
            self.ingest_daily_dispatch_limit_per_user,
            self.ingest_daily_dispatch_limit_global,
        ) <= 0:
            raise ValueError("Ingest tenant limits must be positive")
        forwarded_sources = {
            value.strip()
            for value in self.web_forwarded_allow_ips.split(",")
            if value.strip()
        }
        if not forwarded_sources or "*" in forwarded_sources:
            raise ValueError(
                "WEB_FORWARDED_ALLOW_IPS must list explicit trusted proxies"
            )
        if self.web_origin is not None:
            origin = self.web_origin.strip()
            parsed = urlsplit(origin)
            loopback_http = (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
            if (
                (parsed.scheme != "https" and not loopback_http)
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("WEB_ORIGIN must be HTTPS or a loopback HTTP origin")
            if origin.endswith("/"):
                raise ValueError("WEB_ORIGIN must not include a trailing slash")

    @property
    def ingest_completion_publish_interval_seconds(self) -> int:
        """Compatibility spelling used by deployment operators."""

        return self.ingest_completion_interval_seconds

    @property
    def ingest_notification_retry_ceiling(self) -> int:
        """Compatibility alias for the configured notification attempt cap."""

        return self.ingest_notification_max_attempts


def _build_database_url() -> str:
    explicit = _env("DATABASE_URL")
    if explicit:
        return explicit
    user = _env("POSTGRES_USER", "postgres")
    password = _require("POSTGRES_PASSWORD")
    host = _env("POSTGRES_HOST", "localhost")
    port = _env("POSTGRES_PORT", "5432")
    db = _env("POSTGRES_DB", "kb")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def _build_redis_url() -> str:
    explicit = _env("REDIS_URL")
    if explicit:
        return explicit
    host = _env("REDIS_HOST", "localhost")
    port = _env("REDIS_PORT", "6379")
    db = _env("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
