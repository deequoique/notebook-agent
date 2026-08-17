from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.browser_companion import (
    BrowserCompanionError,
    BrowserCompanionService,
    pkce_challenge,
)
from app.channels.types import UserScope


DDL = """
CREATE TABLE app_user (id INTEGER PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, disabled_at DATETIME);
CREATE TABLE browser_companion_pairing (id INTEGER PRIMARY KEY, public_id TEXT UNIQUE NOT NULL, challenge_hash TEXT UNIQUE NOT NULL, app_user_id INTEGER, client_label TEXT NOT NULL, client_version TEXT NOT NULL, expires_at DATETIME NOT NULL, approved_at DATETIME, consumed_at DATETIME, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE browser_companion_grant (id INTEGER PRIMARY KEY, device_id TEXT UNIQUE NOT NULL, app_user_id INTEGER NOT NULL, token_hash TEXT UNIQUE NOT NULL, scope TEXT NOT NULL DEFAULT 'capture:write', client_label TEXT NOT NULL, client_version TEXT NOT NULL, expires_at DATETIME NOT NULL, revoked_at DATETIME, disabled_at DATETIME, last_used_at DATETIME, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as connection:
        for statement in DDL.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        connection.exec_driver_sql("INSERT INTO app_user (id) VALUES (7), (8)")
    return lambda: Session(engine, expire_on_commit=False)


def test_pairing_is_explicit_single_use_and_grant_is_revocable():
    sessions = factory()
    service = BrowserCompanionService(sessions)
    now = datetime(2026, 8, 14, tzinfo=UTC)
    verifier = "v" * 43
    pairing = service.create_pairing(pkce_challenge(verifier), client_label="Chrome", client_version="0.1", now=now)

    assert service.pairing_status(pairing.public_id, now=now) == "pending"
    service.approve(UserScope(7), pairing.public_id, now=now)
    assert service.pairing_status(pairing.public_id, now=now) == "approved"
    grant = service.exchange(pairing.public_id, verifier, now=now)
    assert service.resolve_grant(grant.raw_token, now=now).app_user_id == 7
    assert service.list_devices(UserScope(7))[0].device_id == grant.device_id

    with pytest.raises(BrowserCompanionError, match="extension_pairing_used"):
        service.exchange(pairing.public_id, verifier, now=now)
    service.revoke_device(UserScope(7), grant.device_id, now=now + timedelta(seconds=1))
    with pytest.raises(BrowserCompanionError, match="extension_grant_revoked"):
        service.resolve_grant(grant.raw_token, now=now + timedelta(seconds=2))


def test_pairing_cannot_be_claimed_by_a_second_tenant():
    sessions = factory()
    service = BrowserCompanionService(sessions)
    verifier = "x" * 43
    pairing = service.create_pairing(pkce_challenge(verifier), client_label="Chrome", client_version="0.1")
    service.approve(UserScope(7), pairing.public_id)

    with pytest.raises(BrowserCompanionError, match="extension_pairing_invalid"):
        service.approve(UserScope(8), pairing.public_id)
