import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.app import WebApiServices, create_app
from app.browser_capture_submission import BrowserCaptureResult
from app.channels.types import UserScope
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, ResolvedWebSession


WEB_ORIGIN = "https://kb.example.test"
EXTENSION_ORIGIN = "chrome-extension://omogodipchfidpikpeebgmlplpkjnpfm"


class Auth:
    def resolve_session(self, token):
        if token != "web-session":
            raise RuntimeError("invalid")
        return ResolvedWebSession(7, "session-public", "telegram", datetime.now(UTC) + timedelta(hours=1))

    def validate_csrf(self, session_token, csrf_token):
        if session_token != "web-session" or csrf_token != "csrf":
            raise RuntimeError("invalid")


class Companion:
    def __init__(self):
        self.resolved = []

    def resolve_grant(self, token):
        self.resolved.append(token)
        return UserScope(7)

    def approve(self, scope, pairing_id):
        assert scope.app_user_id == 7
        return SimpleNamespace(public_id=pairing_id, expires_at=datetime.now(UTC) + timedelta(minutes=5))

    def revoke_token(self, token):
        self.resolved.append(f"revoked:{token}")


class Submission:
    def __init__(self):
        self.calls = []

    def submit(self, scope, payload, **kwargs):
        self.calls.append((scope, payload, kwargs))
        return BrowserCaptureResult("capture", "item", payload.platform, "queued", "queued")


def payload():
    cues = []
    return {
        "protocol_version": "capture.v1",
        "client_version": "0.1.0",
        "platform": "youtube",
        "platform_id": "dQw4w9WgXcQ",
        "canonical_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "page_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "metadata": {"title": "Video", "tags": [], "chapters": []},
        "caption": {"status": "unavailable", "source": None, "language": None, "cues": cues},
        "content_hash": hashlib.sha256(b"").hexdigest(),
    }


def client():
    auth = Auth()
    companion = Companion()
    submission = Submission()
    placeholder = object()
    app = create_app(
        services=WebApiServices(
            web_auth=auth,
            library=placeholder,
            submission=placeholder,
            transcript=placeholder,
            browser_companion=companion,
            browser_capture_submission=submission,
            settings=SimpleNamespace(
                browser_companion_allowed_origins=(EXTENSION_ORIGIN,),
                browser_companion_max_request_bytes=5_500_000,
            ),
        ),
        expected_origin=WEB_ORIGIN,
        publish_budget_seconds=1,
    )
    return TestClient(app, base_url=WEB_ORIGIN), companion, submission


def test_capture_requires_exact_extension_origin_and_capture_bearer():
    web, companion, submission = client()
    path = "/api/v1/browser-companion/extension/captures"
    common = {"Idempotency-Key": "one", "Authorization": "Bearer capture-token"}

    rejected = web.post(path, headers={**common, "Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, json=payload())
    accepted = web.post(path, headers={**common, "Origin": EXTENSION_ORIGIN}, json=payload())

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert companion.resolved == ["capture-token"]
    assert submission.calls[0][0].app_user_id == 7


def test_web_cookie_cannot_replace_capture_bearer_and_web_approval_keeps_csrf():
    web, _companion, _submission = client()
    web.cookies.set(SESSION_COOKIE_NAME, "web-session", domain="kb.example.test", path="/")
    web.cookies.set(CSRF_COOKIE_NAME, "csrf", domain="kb.example.test", path="/")

    capture = web.post(
        "/api/v1/browser-companion/extension/captures",
        headers={"Origin": EXTENSION_ORIGIN, "Idempotency-Key": "one"},
        json=payload(),
    )
    approval_without_csrf = web.post(
        "/api/v1/browser-companion/pairings/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:approve",
        headers={"Origin": WEB_ORIGIN},
    )
    approval = web.post(
        "/api/v1/browser-companion/pairings/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:approve",
        headers={"Origin": WEB_ORIGIN, "X-CSRF-Token": "csrf"},
    )

    assert capture.status_code == 401
    assert capture.json()["code"] == "extension_pairing_required"
    assert approval_without_csrf.status_code == 403
    assert approval.status_code == 200


def test_extension_can_revoke_only_its_own_capture_token():
    web, companion, _submission = client()
    response = web.delete(
        "/api/v1/browser-companion/extension/grant",
        headers={"Origin": EXTENSION_ORIGIN, "Authorization": "Bearer capture-token"},
    )

    assert response.status_code == 204
    assert companion.resolved == ["revoked:capture-token"]


def test_incompatible_capture_protocol_has_an_actionable_safe_error():
    web, _companion, _submission = client()
    incompatible = payload()
    incompatible["protocol_version"] = "capture.v2"
    response = web.post(
        "/api/v1/browser-companion/extension/captures",
        headers={
            "Origin": EXTENSION_ORIGIN,
            "Authorization": "Bearer capture-token",
            "Idempotency-Key": "one",
        },
        json=incompatible,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "capture_protocol_unsupported"


def test_extension_capture_rejects_compressed_request_bodies():
    web, companion, submission = client()
    response = web.post(
        "/api/v1/browser-companion/extension/captures",
        headers={
            "Origin": EXTENSION_ORIGIN,
            "Content-Encoding": "gzip",
            "Authorization": "Bearer capture-token",
            "Idempotency-Key": "one",
        },
        content=b"not-even-inflated",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "capture_payload_invalid"
    assert companion.resolved == []
    assert submission.calls == []
