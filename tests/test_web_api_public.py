import importlib

import pytest
from fastapi.testclient import TestClient


def _load_create_app():
    try:
        module = importlib.import_module("app.api.app")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Web API composition boundary is missing: {exc}")
    return module.create_app


def test_public_health_capabilities_and_openapi_are_safe():
    app = _load_create_app()()

    with TestClient(app, base_url="https://testserver") as client:
        health = client.get("/api/v1/health")
        capabilities = client.get("/api/v1/capabilities")
        openapi = client.get("/api/v1/openapi.json")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["referrer-policy"] == "no-referrer"
    assert health.headers["cache-control"] == "no-store"

    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "supported_platforms": ["youtube", "bilibili", "ntu_kaltura"],
        "browser_companion": True,
        "web_login_channels": ["telegram", "wechat"],
        "save_enabled": True,
        "max_save_batch_size": 10,
        "transcript_pagination": True,
        "archive": True,
        "summary_generation": False,
        "chat": True,
    }

    assert openapi.status_code == 200
    document = openapi.json()
    assert document["info"]["title"] == "Notebook Agent Web API"
    serialized = str(document)
    for forbidden in (
        "user_id",
        "app_user_id",
        "channel_identity_id",
        "external_user_id",
        "account_id",
    ):
        assert forbidden not in serialized


def test_capabilities_only_advertise_configured_login_channels():
    app = _load_create_app()(web_login_channels=("telegram",))

    with TestClient(app, base_url="https://testserver") as client:
        capabilities = client.get("/api/v1/capabilities")

    assert capabilities.status_code == 200
    assert capabilities.json()["web_login_channels"] == ["telegram"]


def test_capabilities_can_advertise_email_as_the_only_login_route():
    app = _load_create_app()(web_login_channels=("email",))

    with TestClient(app, base_url="https://testserver") as client:
        capabilities = client.get("/api/v1/capabilities")

    assert capabilities.status_code == 200
    assert capabilities.json()["web_login_channels"] == ["email"]


def test_capabilities_advertise_read_only_mode_without_hiding_library_reads():
    app = _load_create_app()(save_enabled=False)

    with TestClient(app, base_url="https://testserver") as client:
        capabilities = client.get("/api/v1/capabilities")

    assert capabilities.status_code == 200
    assert capabilities.json()["save_enabled"] is False


@pytest.mark.parametrize(
    "channels",
    [(), ("telegram", "signal"), ("telegram", "telegram")],
)
def test_app_rejects_invalid_login_channel_capabilities(channels):
    with pytest.raises(ValueError):
        _load_create_app()(web_login_channels=channels)


def test_unknown_api_route_returns_a_json_error_not_spa_html():
    app = _load_create_app()()

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "code": "not_found",
        "message": "未找到请求的资源",
    }
