"""FastAPI composition for the same-origin Notebook Agent Web product."""

from __future__ import annotations

import hmac
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.auth_routes import build_auth_router
from app.api.browser_companion_routes import build_browser_companion_router
from app.api.conversation_routes import build_conversation_router
from app.api.email_auth_routes import build_email_auth_router
from app.api.library_routes import build_library_router
from app.api.library_schemas import ErrorResponse
from app.channels.types import UserScope
from app.ingest.submission import MAX_SAVE_BATCH_SIZE
from app.web.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    WebAuthError,
)
from app.web_auth import InvalidSession


logger = logging.getLogger(__name__)
_CHROME_EXTENSION_ORIGIN_RE = re.compile(r"^chrome-extension://[a-p]{32}$")
_BROWSER_COMPANION_STATUS_PATH_RE = re.compile(
    r"^/api/v1/browser-companion/extension/pairings/[a-f0-9]{32}$"
)
MAX_WEB_REQUEST_BODY_BYTES = 64 * 1024
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAFE_HTTP_CODES = frozenset(
    {
        "not_found",
        "validation_error",
        "invalid_lifecycle",
        "invalid_sort",
        "invalid_collection",
        "why_saved_too_long",
        "item_archived",
        "retry_unavailable",
        "quota_exceeded",
        "save_disabled",
        "empty_batch",
        "batch_too_large",
        "transcript_unavailable",
        "transcript_invalid",
        "transcript_too_large",
        "ingest_too_large",
        "session_invalid",
        "csrf_invalid",
        "request_too_large",
        "link_token_used",
        "link_token_expired",
        "link_channel_mismatch",
        "link_merge_busy",
        "link_account_disabled",
        "link_source_unbound",
        "link_merge_conflict",
        "link_token_invalid",
        "extension_origin_invalid",
        "extension_pairing_invalid",
        "extension_pairing_pending",
        "extension_pairing_expired",
        "extension_pairing_used",
        "extension_pairing_rate_limited",
        "extension_pairing_required",
        "extension_grant_expired",
        "extension_grant_revoked",
        "extension_account_disabled",
        "extension_scope_invalid",
        "extension_device_not_found",
        "capture_payload_invalid",
        "capture_content_hash_mismatch",
        "capture_protocol_unsupported",
        "capture_conflict",
        "capture_too_large",
        "capture_upload_failed",
        "queue_unavailable",
    }
)


def _extension_origin_allowed(origin: str, allowed_origins: tuple[str, ...]) -> bool:
    if origin in allowed_origins:
        return True
    return (
        "chrome-extension://*" in allowed_origins
        and _CHROME_EXTENSION_ORIGIN_RE.fullmatch(origin) is not None
    )


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class CapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_platforms: tuple[
        Literal["youtube", "bilibili", "ntu_kaltura"], ...
    ] = (
        "youtube",
        "bilibili",
        "ntu_kaltura",
    )
    browser_companion: bool = True
    web_login_channels: tuple[Literal["email", "telegram", "wechat"], ...] = (
        "telegram",
        "wechat",
    )
    save_enabled: bool = True
    max_save_batch_size: int = MAX_SAVE_BATCH_SIZE
    transcript_pagination: bool = True
    archive: bool = True
    summary_generation: bool = False
    chat: bool = True


@dataclass(frozen=True)
class WebApiServices:
    """Explicit application-service boundary used by the Web composition root."""

    web_auth: Any
    library: Any
    submission: Any
    transcript: Any
    browser_companion: Any | None = None
    browser_capture_submission: Any | None = None
    email_auth: Any | None = None
    trusted_proxy_hosts: str = ""
    # Retained conversation/link routes are mounted by this canonical app.
    # Optional values keep the OpenAPI exporter inert while allowing runtime
    # callers to inject their existing channel service and session factory.
    channel_service: Any | None = None
    session_resolver: Callable[[str], Any] | None = None
    # Converts either the canonical email session or the migration-era
    # channel session into a browser-safe external identity for compatibility
    # conversation/link routes.
    session_identity_resolver: Callable[[Any], Any] | None = None
    session_factory: Callable | None = None
    settings: Any | None = None
    include_conversation_routes: bool = True


_SAFE_MESSAGES = {
    "not_found": "未找到请求的资源",
    "validation_error": "请求参数无效",
    "invalid_lifecycle": "状态筛选无效",
    "invalid_sort": "排序方式无效",
    "invalid_collection": "收藏夹筛选无效",
    "why_saved_too_long": "保存说明过长",
    "item_archived": "请先恢复该视频",
    "retry_unavailable": "当前状态不能重试",
    "quota_exceeded": "已达到当前保存额度，请稍后重试",
    "save_disabled": "资料库当前为只读模式，暂时不能添加或重新整理视频",
    "empty_batch": "请至少添加一个链接",
    "batch_too_large": "一次最多添加 10 个链接",
    "transcript_unavailable": "全文暂不可用",
    "transcript_invalid": "全文分页已失效，请重新加载",
    "transcript_too_large": "全文数据过大",
    "ingest_too_large": "视频字幕数据超过当前处理上限",
    "session_invalid": "登录已失效，请重新登录",
    "csrf_invalid": "请求验证失败，请刷新后重试",
    "request_too_large": "请求内容过大",
    "request_failed": "请求无法完成",
    "link_token_used": "该绑定码已使用，请重新生成",
    "link_token_expired": "该绑定码已过期，请重新生成",
    "link_channel_mismatch": "请在绑定码指定的目标渠道中使用",
    "link_merge_busy": "目标账户仍有内容正在处理，请稍后重试",
    "link_account_disabled": "账户不可用，无法绑定",
    "link_source_unbound": "请先在当前来源渠道完成注册",
    "link_merge_conflict": "账户状态发生变化，请稍后重试",
    "link_token_invalid": "绑定码无效，请重新生成",
    "extension_origin_invalid": "浏览器伴侣来源无效",
    "extension_pairing_invalid": "浏览器伴侣配对请求无效",
    "extension_pairing_pending": "请先在 Notebook Agent 中批准配对",
    "extension_pairing_expired": "浏览器伴侣配对已过期，请重新开始",
    "extension_pairing_used": "浏览器伴侣配对已使用，请重新开始",
    "extension_pairing_rate_limited": "配对请求过多，请稍后重试",
    "extension_pairing_required": "请先连接浏览器伴侣",
    "extension_grant_expired": "浏览器伴侣连接已过期，请重新连接",
    "extension_grant_revoked": "浏览器伴侣连接已断开，请重新连接",
    "extension_account_disabled": "账户不可用",
    "extension_scope_invalid": "浏览器伴侣权限无效",
    "extension_device_not_found": "未找到该浏览器伴侣",
    "capture_payload_invalid": "字幕数据无效",
    "capture_content_hash_mismatch": "字幕数据校验失败",
    "capture_protocol_unsupported": "浏览器伴侣版本不兼容，请升级",
    "capture_conflict": "该视频当前已有保存任务，请稍后重试",
    "capture_too_large": "字幕数据超过当前处理上限",
    "capture_upload_failed": "字幕上传失败，请稍后重试",
    "queue_unavailable": "保存任务暂时无法排队，请稍后重试",
}


class RequestBodyLimitMiddleware:
    """Bound both fixed-length and chunked request bodies before parsing."""

    def __init__(
        self, app: ASGIApp, *, max_bytes: int, capture_max_bytes: int
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.capture_max_bytes = capture_max_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", ()))
        path = str(scope.get("path", ""))
        max_bytes = (
            self.capture_max_bytes
            if path == "/api/v1/browser-companion/extension/captures"
            else self.max_bytes
        )
        raw_length = headers.get(b"content-length", b"")
        try:
            declared_length = int(raw_length) if raw_length else None
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            await _error_response("request_too_large", 413)(scope, receive, send)
            return

        received = 0
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, receive, send)
                return
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > max_bytes:
                await _error_response("request_too_large", 413)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": b"".join(chunks),
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay_receive, send)


def create_app(
    *,
    services: WebApiServices | None = None,
    expected_origin: str | None = None,
    cookie_secure: bool = True,
    publish_budget_seconds: float = 5.0,
    save_enabled: bool = True,
    web_login_channels: tuple[str, ...] = ("telegram", "wechat"),
    static_dir: str | Path | None = None,
) -> FastAPI:
    """Build public routes, optional authenticated services, and SPA hosting."""

    if services is not None:
        if not expected_origin or expected_origin.endswith("/"):
            raise ValueError("expected_origin is required without a trailing slash")
        if publish_budget_seconds <= 0:
            raise ValueError("publish budget must be positive")
    if (
        not web_login_channels
        or len(set(web_login_channels)) != len(web_login_channels)
        or any(
            channel not in {"email", "telegram", "wechat"}
            for channel in web_login_channels
        )
    ):
        raise ValueError("web login channels must be unique telegram/wechat values")
    public_login_channels = tuple(web_login_channels)
    app = FastAPI(
        title="Notebook Agent Web API",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=MAX_WEB_REQUEST_BODY_BYTES,
        capture_max_bytes=int(
            getattr(
                getattr(services, "settings", None),
                "browser_companion_max_request_bytes",
                5_500_000,
            )
        ),
    )

    @app.middleware("http")
    async def secure_web_boundary(request: Request, call_next):
        request_id = uuid4().hex
        request.state.request_id = request_id
        extension_prefix = "/api/v1/browser-companion/extension/"
        extension_request = request.url.path.startswith(extension_prefix)
        extension_origin = request.headers.get("origin", "")
        allowed_extension_origins = tuple(
            getattr(
                getattr(services, "settings", None),
                "browser_companion_allowed_origins",
                (),
            )
        )
        origin_optional_status_read = (
            request.method == "GET"
            and not extension_origin
            and _BROWSER_COMPANION_STATUS_PATH_RE.fullmatch(request.url.path)
            is not None
        )
        try:
            if extension_request:
                if not origin_optional_status_read and not _extension_origin_allowed(
                    extension_origin, allowed_extension_origins
                ):
                    logger.warning(
                        "browser_companion_origin_rejected request_id=%s origin=%s",
                        request_id,
                        extension_origin or "<missing>",
                    )
                    response = _error_response("extension_origin_invalid", 403)
                elif request.headers.get("content-encoding", "").lower() not in {
                    "",
                    "identity",
                }:
                    response = _error_response("capture_payload_invalid", 422)
                elif request.method == "OPTIONS":
                    response = Response(status_code=204)
                else:
                    response = await call_next(request)
            elif (
                services is not None
                and request.method in _UNSAFE_METHODS
                and request.url.path.startswith("/api/v1/")
                and not request.url.path.startswith("/api/v1/auth/")
            ):
                error = _validate_protected_mutation(
                    request,
                    services.web_auth,
                    expected_origin or "",
                )
                if error is not None:
                    response = error
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception as exc:
            logger.error(
                "web_api_failed request_id=%s exception_type=%s",
                request_id,
                type(exc).__name__,
            )
            response = _error_response("request_failed", 500)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if extension_request and _extension_origin_allowed(
            extension_origin, allowed_extension_origins
        ):
            response.headers["Access-Control-Allow-Origin"] = extension_origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, Idempotency-Key"
            )
            response.headers["Vary"] = "Origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' https://i.ytimg.com https://img.youtube.com "
            "https://ntulearnvideo.ntu.edu.sg data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        if expected_origin and expected_origin.startswith("https://"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path.startswith("/api/") or response.headers.get(
            "content-type", ""
        ).startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def safe_http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        raw_code = exc.detail if isinstance(exc.detail, str) else "request_failed"
        code = raw_code if raw_code in _SAFE_HTTP_CODES else (
            "not_found" if exc.status_code == 404 else "request_failed"
        )
        return _error_response(code, exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if (
            request.url.path
            == "/api/v1/browser-companion/extension/captures"
            and any(error.get("loc", ())[-1:] == ("protocol_version",) for error in exc.errors())
        ):
            return _error_response("capture_protocol_unsupported", 422)
        return _error_response("validation_error", 422)

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["public"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get(
        "/api/v1/capabilities",
        response_model=CapabilitiesResponse,
        tags=["public"],
    )
    def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(
            web_login_channels=public_login_channels,
            save_enabled=save_enabled,
        )

    if services is not None:
        if services.email_auth is None:
            # Kept only as a construction-level compatibility boundary for
            # callers that supply the legacy service.  The production runtime
            # always provides ``email_auth`` and therefore exposes email OTP.
            app.include_router(
                build_auth_router(
                    services.web_auth,
                    expected_origin=expected_origin or "",
                    cookie_secure=cookie_secure,
                )
            )
        else:
            app.include_router(
                build_email_auth_router(
                    services.email_auth,
                    expected_origin=expected_origin or "",
                    cookie_secure=cookie_secure,
                    trusted_proxy_hosts=services.trusted_proxy_hosts,
                )
            )

        def authenticated_session(request: Request):
            resolver = services.session_resolver
            if resolver is None:
                raise HTTPException(status_code=503, detail="request_failed")
            raw_token = request.cookies.get(SESSION_COOKIE_NAME, "")
            if not raw_token:
                raise HTTPException(
                    status_code=401,
                    detail="session_invalid",
                    headers={"WWW-Authenticate": "Session"},
                )
            try:
                return resolver(raw_token)
            except (WebAuthError, InvalidSession) as exc:
                raise HTTPException(
                    status_code=401,
                    detail="session_invalid",
                    headers={"WWW-Authenticate": "Session"},
                ) from exc
            except Exception:
                # Unrelated provider/database errors reach the app-level safe
                # 500 handler rather than being mistaken for an expired login.
                raise

        def authenticated_scope(request: Request) -> UserScope:
            if services.session_resolver is not None:
                session = authenticated_session(request)
                tenant = getattr(session, "tenant", None)
                app_user_id = getattr(tenant, "app_user_id", None)
                if app_user_id is not None:
                    return UserScope(app_user_id)
            raw_token = request.cookies.get(SESSION_COOKIE_NAME, "")
            try:
                session = services.web_auth.resolve_session(raw_token)
            except WebAuthError as exc:
                raise HTTPException(
                    status_code=401,
                    detail="session_invalid",
                    headers={"WWW-Authenticate": "Session"},
                ) from exc
            return UserScope(session.app_user_id)

        app.include_router(
            build_library_router(
                services.library,
                services.submission,
                services.transcript,
                publish_budget_seconds=publish_budget_seconds,
                save_enabled=save_enabled,
                scope_dependency=authenticated_scope,
            )
        )
        if (
            services.browser_companion is not None
            and services.browser_capture_submission is not None
        ):
            app.include_router(
                build_browser_companion_router(
                    services.browser_companion,
                    services.browser_capture_submission,
                    scope_dependency=authenticated_scope,
                    web_origin=expected_origin or "",
                    publish_budget_seconds=publish_budget_seconds,
                )
            )

        if services.include_conversation_routes:
            app.include_router(
                build_conversation_router(
                    channel_service=services.channel_service,
                    session_dependency=authenticated_session,
                    session_factory=services.session_factory,
                    settings=services.settings,
                    session_identity_resolver=services.session_identity_resolver,
                )
            )

    if static_dir is not None:
        _mount_spa(app, Path(static_dir))

    return app


def _validate_protected_mutation(
    request: Request,
    web_auth: Any,
    expected_origin: str,
) -> JSONResponse | None:
    if (
        request.headers.get("origin") != expected_origin
    ):
        return _error_response("csrf_invalid", 403)
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    cookie_csrf = request.cookies.get(CSRF_COOKIE_NAME, "")
    header_csrf = request.headers.get("x-csrf-token", "")
    if (
        not session_token
        or not cookie_csrf
        or not header_csrf
        or not hmac.compare_digest(cookie_csrf, header_csrf)
    ):
        return _error_response("csrf_invalid", 403)
    try:
        web_auth.resolve_session(session_token)
        web_auth.validate_csrf(session_token, header_csrf)
    except WebAuthError as exc:
        status = 401 if exc.code == "session_invalid" else 403
        code = "session_invalid" if status == 401 else "csrf_invalid"
        return _error_response(code, status)
    return None


def _mount_spa(app: FastAPI, directory: Path) -> None:
    root = directory.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise ValueError(f"Web build is missing index.html: {root}")
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="web-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(index, media_type="text/html")


def _error_response(
    code: str,
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    safe_code = code if code in _SAFE_MESSAGES else "request_failed"
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            code=safe_code,
            message=_SAFE_MESSAGES[safe_code],
        ).model_dump(),
        headers=headers,
    )
