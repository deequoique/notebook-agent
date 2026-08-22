"""Tenant-safe pairing and Bearer grants for the browser companion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.channels.types import UserScope
from app.models import (
    AppUser,
    BrowserCompanionGrant,
    BrowserCompanionPairing,
)


_PKCE_VALUE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class BrowserCompanionError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True)
class PairingReference:
    public_id: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedBrowserGrant:
    device_id: str
    raw_token: str
    expires_at: datetime


@dataclass(frozen=True)
class BrowserDevice:
    device_id: str
    client_label: str
    client_version: str
    expires_at: datetime
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(UTC)
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    if not _PKCE_VALUE_RE.fullmatch(str(verifier)):
        raise BrowserCompanionError("extension_pairing_invalid")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class BrowserCompanionService:
    def __init__(
        self,
        session_factory,
        *,
        pairing_ttl: timedelta = timedelta(minutes=10),
        grant_ttl: timedelta = timedelta(days=90),
        max_active_pairings: int = 1_000,
    ) -> None:
        if pairing_ttl <= timedelta(0) or grant_ttl <= timedelta(0):
            raise ValueError("browser companion TTLs must be positive")
        if max_active_pairings <= 0:
            raise ValueError("max_active_pairings must be positive")
        self._session_factory = session_factory
        self._pairing_ttl = pairing_ttl
        self._grant_ttl = grant_ttl
        self._max_active_pairings = max_active_pairings

    def create_pairing(
        self,
        challenge: str,
        *,
        client_label: str,
        client_version: str,
        now: datetime | None = None,
    ) -> PairingReference:
        challenge = str(challenge).strip()
        if not _PKCE_VALUE_RE.fullmatch(challenge):
            raise BrowserCompanionError("extension_pairing_invalid")
        label = str(client_label).strip()
        version = str(client_version).strip()
        if not label or len(label) > 200 or not version or len(version) > 64:
            raise BrowserCompanionError("extension_pairing_invalid")
        current = _utc(now)
        expires_at = current + self._pairing_ttl
        challenge_hash = _digest(challenge)
        with self._session_factory() as db:
            active = db.scalar(
                select(func.count(BrowserCompanionPairing.id)).where(
                    BrowserCompanionPairing.expires_at > current,
                    BrowserCompanionPairing.consumed_at.is_(None),
                )
            ) or 0
            if active >= self._max_active_pairings:
                raise BrowserCompanionError("extension_pairing_rate_limited")
            existing = db.scalar(
                select(BrowserCompanionPairing).where(
                    BrowserCompanionPairing.challenge_hash == challenge_hash
                )
            )
            if existing is not None:
                if _utc(existing.expires_at) <= current or existing.consumed_at is not None:
                    raise BrowserCompanionError("extension_pairing_invalid")
                return PairingReference(existing.public_id, _utc(existing.expires_at))
            pairing = BrowserCompanionPairing(
                public_id=uuid4().hex,
                challenge_hash=challenge_hash,
                client_label=label,
                client_version=version,
                expires_at=expires_at,
                created_at=current,
            )
            db.add(pairing)
            db.commit()
            return PairingReference(pairing.public_id, expires_at)

    def approve(
        self,
        scope: UserScope,
        public_id: str,
        *,
        now: datetime | None = None,
    ) -> PairingReference:
        current = _utc(now)
        with self._session_factory() as db:
            pairing = db.scalar(
                select(BrowserCompanionPairing)
                .where(BrowserCompanionPairing.public_id == str(public_id))
                .with_for_update()
            )
            owner = db.get(AppUser, scope.app_user_id)
            if owner is None or owner.disabled_at is not None:
                raise BrowserCompanionError("extension_account_disabled")
            if pairing is None:
                raise BrowserCompanionError("extension_pairing_invalid")
            if _utc(pairing.expires_at) <= current:
                raise BrowserCompanionError("extension_pairing_expired")
            if pairing.consumed_at is not None:
                raise BrowserCompanionError("extension_pairing_used")
            if pairing.app_user_id not in {None, scope.app_user_id}:
                raise BrowserCompanionError("extension_pairing_invalid")
            pairing.app_user_id = scope.app_user_id
            pairing.approved_at = pairing.approved_at or current
            db.commit()
            return PairingReference(pairing.public_id, _utc(pairing.expires_at))

    def pairing_status(
        self, public_id: str, *, now: datetime | None = None
    ) -> str:
        current = _utc(now)
        with self._session_factory() as db:
            pairing = db.scalar(
                select(BrowserCompanionPairing).where(
                    BrowserCompanionPairing.public_id == str(public_id)
                )
            )
            if pairing is None:
                raise BrowserCompanionError("extension_pairing_invalid")
            if _utc(pairing.expires_at) <= current:
                return "expired"
            if pairing.consumed_at is not None:
                return "used"
            return "approved" if pairing.approved_at is not None else "pending"

    def exchange(
        self,
        public_id: str,
        verifier: str,
        *,
        now: datetime | None = None,
    ) -> IssuedBrowserGrant:
        current = _utc(now)
        challenge_hash = _digest(pkce_challenge(str(verifier)))
        with self._session_factory() as db:
            pairing = db.scalar(
                select(BrowserCompanionPairing)
                .where(BrowserCompanionPairing.public_id == str(public_id))
                .with_for_update()
            )
            if pairing is None or not hmac.compare_digest(
                pairing.challenge_hash, challenge_hash
            ):
                raise BrowserCompanionError("extension_pairing_invalid")
            if _utc(pairing.expires_at) <= current:
                raise BrowserCompanionError("extension_pairing_expired")
            if pairing.consumed_at is not None:
                raise BrowserCompanionError("extension_pairing_used")
            if pairing.approved_at is None or pairing.app_user_id is None:
                raise BrowserCompanionError("extension_pairing_pending")
            owner = db.get(AppUser, pairing.app_user_id)
            if owner is None or owner.disabled_at is not None:
                raise BrowserCompanionError("extension_account_disabled")
            raw_token = secrets.token_urlsafe(48)
            expires_at = current + self._grant_ttl
            grant = BrowserCompanionGrant(
                device_id=uuid4().hex,
                app_user_id=pairing.app_user_id,
                token_hash=_digest(raw_token),
                scope="capture:write",
                client_label=pairing.client_label,
                client_version=pairing.client_version,
                expires_at=expires_at,
                created_at=current,
            )
            pairing.consumed_at = current
            db.add(grant)
            db.commit()
            return IssuedBrowserGrant(grant.device_id, raw_token, expires_at)

    def resolve_grant(
        self, raw_token: str, *, now: datetime | None = None
    ) -> UserScope:
        token = str(raw_token).strip()
        if not token:
            raise BrowserCompanionError("extension_pairing_required")
        current = _utc(now)
        token_hash = _digest(token)
        with self._session_factory() as db:
            grant = db.scalar(
                select(BrowserCompanionGrant).where(
                    BrowserCompanionGrant.token_hash == token_hash
                )
            )
            if grant is None or not hmac.compare_digest(grant.token_hash, token_hash):
                raise BrowserCompanionError("extension_pairing_required")
            owner = db.get(AppUser, grant.app_user_id)
            if grant.revoked_at is not None:
                raise BrowserCompanionError("extension_grant_revoked")
            if (
                grant.disabled_at is not None
                or owner is None
                or owner.disabled_at is not None
            ):
                raise BrowserCompanionError("extension_account_disabled")
            if _utc(grant.expires_at) <= current:
                raise BrowserCompanionError("extension_grant_expired")
            if grant.scope != "capture:write":
                raise BrowserCompanionError("extension_scope_invalid")
            grant.last_used_at = current
            db.commit()
            return UserScope(grant.app_user_id)

    def list_devices(self, scope: UserScope) -> tuple[BrowserDevice, ...]:
        with self._session_factory() as db:
            grants = db.scalars(
                select(BrowserCompanionGrant)
                .where(BrowserCompanionGrant.app_user_id == scope.app_user_id)
                .order_by(BrowserCompanionGrant.created_at.desc())
            ).all()
            return tuple(
                BrowserDevice(
                    grant.device_id,
                    grant.client_label,
                    grant.client_version,
                    _utc(grant.expires_at),
                    _utc(grant.created_at),
                    _utc(grant.last_used_at) if grant.last_used_at else None,
                    _utc(grant.revoked_at) if grant.revoked_at else None,
                )
                for grant in grants
            )

    def revoke_token(
        self, raw_token: str, *, now: datetime | None = None
    ) -> None:
        token_hash = _digest(str(raw_token).strip())
        with self._session_factory() as db:
            grant = db.scalar(
                select(BrowserCompanionGrant)
                .where(BrowserCompanionGrant.token_hash == token_hash)
                .with_for_update()
            )
            if grant is None or not hmac.compare_digest(grant.token_hash, token_hash):
                raise BrowserCompanionError("extension_pairing_required")
            grant.revoked_at = grant.revoked_at or _utc(now)
            db.commit()

    def revoke_device(
        self,
        scope: UserScope,
        device_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        with self._session_factory() as db:
            grant = db.scalar(
                select(BrowserCompanionGrant)
                .where(
                    BrowserCompanionGrant.device_id == str(device_id),
                    BrowserCompanionGrant.app_user_id == scope.app_user_id,
                )
                .with_for_update()
            )
            if grant is None:
                raise BrowserCompanionError("extension_device_not_found")
            grant.revoked_at = grant.revoked_at or _utc(now)
            db.commit()
