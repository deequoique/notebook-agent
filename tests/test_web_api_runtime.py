from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.runtime import build_web_app
from app import web_runtime


def settings(**overrides):
    values = {
        "validate_web_auth": Mock(),
        "web_auth_secret": "x" * 32,
        "web_auth_challenge_ttl_seconds": 600,
        "web_auth_session_ttl_seconds": 2592000,
        "web_auth_attempt_limit": 5,
        "web_auth_rate_window_seconds": 60,
        "web_auth_rate_limit_per_requester": 5,
        "web_auth_global_rate_limit": 100,
        "web_auth_active_challenge_limit": 3,
        "web_auth_challenge_retention_seconds": 86400,
        "web_auth_session_retention_seconds": 604800,
        "web_login_channels": ("telegram", "wechat"),
        "web_origin": "https://kb.example.test",
        "web_cookie_secure": True,
        "web_publish_budget_seconds": 1.5,
        "web_serve_static": True,
        "web_static_dir": "web/dist",
        "trash_retention_days": 30,
        "ingest_max_active_per_user": 10,
        "ingest_daily_new_item_limit": 50,
        "ingest_max_items_per_user": 1000,
        "ingest_max_active_global": 100,
        "ingest_daily_new_item_limit_global": 300,
        "ingest_daily_dispatch_limit_per_user": 100,
        "ingest_daily_dispatch_limit_global": 1000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_runtime_composes_auth_library_submission_and_transcript_without_io():
    config = settings()

    app = build_web_app(
        config,
        session_factory=lambda: None,
        publisher=lambda _dispatch_id: "task",
        object_store=object(),
        mount_static=False,
    )

    paths = app.openapi()["paths"]
    assert "/api/v1/auth/challenges" in paths
    assert "/api/v1/library/items" in paths
    assert "/api/v1/library/items/{item_public_id}/transcript" in paths
    config.validate_web_auth.assert_called_once_with()


def test_runtime_email_composition_is_canonical_and_mounts_compatibility_routes():
    class EmailAuth:
        def resolve_session(self, _token):
            raise AssertionError("OpenAPI construction must not resolve sessions")

    config = settings(
        web_auth_enabled=True,
        web_public_origin="https://kb.example.test",
    )
    app = build_web_app(
        config,
        session_factory=lambda: None,
        publisher=lambda _dispatch_id: "task",
        object_store=object(),
        email_auth=EmailAuth(),
        mount_static=False,
    )

    with TestClient(app, base_url="https://kb.example.test") as client:
        capabilities = client.get("/api/v1/capabilities")

    paths = app.openapi()["paths"]
    assert capabilities.json()["web_login_channels"] == ["email"]
    assert "/api/v1/auth/verify" in paths
    assert "/api/v1/conversations/{conversation_id}/messages" in paths
    assert "/api/v1/link-tokens/consume" in paths


def test_runtime_exposes_only_the_enabled_login_channels():
    app = build_web_app(
        settings(web_login_channels=("wechat",)),
        session_factory=lambda: None,
        publisher=lambda _dispatch_id: "task",
        object_store=object(),
        mount_static=False,
    )

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["web_login_channels"] == ["wechat"]


def test_runtime_always_enables_web_save_capability():
    app = build_web_app(
        settings(),
        session_factory=lambda: None,
        publisher=lambda _dispatch_id: "task",
        object_store=object(),
        mount_static=False,
    )

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["save_enabled"] is True


def test_runtime_can_run_api_only_without_a_static_build(tmp_path):
    missing_build = tmp_path / "missing-web-dist"

    app = build_web_app(
        settings(
            web_serve_static=False,
            web_static_dir=str(missing_build),
        ),
        session_factory=lambda: None,
        publisher=lambda _dispatch_id: "task",
        object_store=object(),
    )

    with TestClient(app, base_url="https://testserver") as client:
        health = client.get("/api/v1/health")
        root = client.get("/")

    assert health.status_code == 200
    assert root.status_code == 404


@pytest.mark.parametrize(
    "override",
    [
        {"web_origin": None},
        {"web_cookie_secure": False},
    ],
)
def test_runtime_rejects_an_origin_or_cookie_mode_that_breaks_host_cookie_security(override):
    with pytest.raises(ValueError):
        build_web_app(
            settings(**override),
            session_factory=lambda: None,
            publisher=lambda _dispatch_id: "task",
            object_store=object(),
            mount_static=False,
        )


@pytest.mark.asyncio
async def test_production_combined_dispatcher_keeps_cookie_and_bearer_boundaries(
    monkeypatch,
):
    class RecordingApp:
        def __init__(self, label):
            self.label = label
            self.requests = []

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return
            headers = {
                key.decode("latin1"): value.decode("latin1")
                for key, value in scope.get("headers", ())
            }
            self.requests.append((scope["path"], headers))
            body = self.label.encode("ascii")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

    web_app = RecordingApp("web")
    mcp_app = RecordingApp("mcp")
    captured = {}

    def fake_build_web_app(*, channel_service=None, **kwargs):
        captured["channel_service"] = channel_service
        captured["build_kwargs"] = kwargs
        return web_app

    monkeypatch.setattr(web_runtime, "build_web_app", fake_build_web_app)
    monkeypatch.setattr(
        web_runtime,
        "create_streamable_http_app",
        lambda **_kwargs: mcp_app,
    )
    channel_service = object()
    combined = web_runtime.create_combined_asgi_app(
        settings=SimpleNamespace(mcp_path="/mcp"),
        session_factory=lambda: None,
        channel_service=channel_service,
        auth_service=object(),
        mcp_server=object(),
        grant_service=object(),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=combined),
        base_url="https://app.example.test",
    ) as client:
        cookie_only = await client.get(
            "/mcp",
            headers={"Cookie": "__Host-kb_session=browser-cookie"},
        )
        bearer_only = await client.get(
            "/api/v1/capabilities",
            headers={"Authorization": "Bearer mcp-token"},
        )

    assert captured["channel_service"] is channel_service
    assert cookie_only.status_code == bearer_only.status_code == 200
    assert cookie_only.text == "mcp"
    assert bearer_only.text == "web"
    assert len(mcp_app.requests) == 1
    assert len(web_app.requests) == 1
    assert mcp_app.requests[0][0] == "/mcp"
    assert mcp_app.requests[0][1]["cookie"] == "__Host-kb_session=browser-cookie"
    assert "authorization" not in mcp_app.requests[0][1]
    assert web_app.requests[0][0] == "/api/v1/capabilities"
    assert web_app.requests[0][1]["authorization"] == "Bearer mcp-token"
    assert "cookie" not in web_app.requests[0][1]
