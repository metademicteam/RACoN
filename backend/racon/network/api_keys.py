"""Database-backed RACoN API keys.

Only SHA-256 digests are persisted. The plaintext token is returned exactly once at
creation and cannot be recovered. Keys are scoped; v1 issues ``inference`` keys only.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.racon.storage.db import session_scope
from backend.racon.storage.models import ApiKey

KEY_PREFIX = "rk_"
DEFAULT_SCOPES = ("inference",)
LAST_USED_THROTTLE_SECONDS = 60.0


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    name: str
    prefix: str
    scopes: frozenset[str]
    rate_limit_per_min: int
    created_at: Optional[datetime]
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def to_dict(self, *, include_key: Optional[str] = None) -> dict:
        payload = {
            "id": self.id,
            "name": self.name,
            "prefix": self.prefix,
            "scopes": sorted(self.scopes),
            "rate_limit_per_min": self.rate_limit_per_min,
            "created_at": _iso(self.created_at),
            "last_used_at": _iso(self.last_used_at),
            "revoked": self.revoked,
            "revoked_at": _iso(self.revoked_at),
        }
        if include_key is not None:
            payload["key"] = include_key
        return payload


def _record(model: ApiKey) -> ApiKeyRecord:
    scopes = frozenset(
        item.strip() for item in (model.scopes or "").split(",") if item.strip()
    )
    return ApiKeyRecord(
        id=model.id,
        name=model.name,
        prefix=model.prefix,
        scopes=scopes or frozenset(DEFAULT_SCOPES),
        rate_limit_per_min=int(model.rate_limit_per_min or 60),
        created_at=model.created_at,
        last_used_at=model.last_used_at,
        revoked_at=model.revoked_at,
    )


class ApiKeyService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_touch: dict[str, float] = {}

    def create(
        self,
        name: str,
        *,
        scopes: tuple[str, ...] | list[str] = DEFAULT_SCOPES,
        rate_limit_per_min: int = 60,
    ) -> tuple[ApiKeyRecord, str]:
        token = KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = f"key_{uuid.uuid4().hex[:12]}"
        scope_text = ",".join(sorted({str(item).strip() for item in scopes if str(item).strip()}))
        with session_scope() as session:
            model = ApiKey(
                id=key_id,
                name=name.strip()[:128] or "client",
                prefix=token[:12],
                key_hash=hash_key(token),
                scopes=scope_text or "inference",
                rate_limit_per_min=max(1, min(int(rate_limit_per_min), 10000)),
            )
            session.add(model)
            session.flush()
            record = _record(model)
        return record, token

    def list(self) -> list[ApiKeyRecord]:
        with session_scope() as session:
            rows = session.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
            return [_record(row) for row in rows]

    def revoke(self, key_id: str) -> bool:
        with session_scope() as session:
            model = session.get(ApiKey, key_id)
            if model is None or model.revoked_at is not None:
                return False
            model.revoked_at = datetime.now(timezone.utc)
            return True

    def verify(self, token: str, *, required_scope: str = "inference") -> Optional[ApiKeyRecord]:
        if not token or not token.startswith(KEY_PREFIX):
            return None
        digest = hash_key(token)
        with session_scope() as session:
            model = session.query(ApiKey).filter(ApiKey.key_hash == digest).one_or_none()
            if model is None or model.revoked_at is not None:
                return None
            if not hmac.compare_digest(model.key_hash, digest):
                return None
            record = _record(model)
            if not record.has_scope(required_scope):
                return None
            self._touch(model)
            return record

    def _touch(self, model: ApiKey) -> None:
        now = time.monotonic()
        with self._lock:
            previous = self._last_touch.get(model.id, 0.0)
            if now - previous < LAST_USED_THROTTLE_SECONDS:
                return
            self._last_touch[model.id] = now
        try:
            model.last_used_at = datetime.now(timezone.utc)
        except Exception:
            # Auditing must never reject a valid key.
            pass


_SERVICE: Optional[ApiKeyService] = None
_SERVICE_LOCK = threading.Lock()


def get_api_key_service() -> ApiKeyService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = ApiKeyService()
    return _SERVICE


def reset_api_key_service() -> None:
    """Test helper: drop the singleton (the DB rows stay)."""
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None
