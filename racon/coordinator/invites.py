"""Coordinator Invite Code Management.

Manages cryptographic invite codes hashed with SHA-256.
Enforces expiration, maximum usage caps, and instant revocation (ADR-009).
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Dict, Optional
from pydantic import BaseModel, Field


class InviteRecord(BaseModel):
    code_hash: str
    code_prefix: str
    max_uses: int = 1
    uses_count: int = 0
    expires_at: float
    is_revoked: bool = False
    created_at: float = Field(default_factory=time.time)
    created_by: str = "operator"


class InviteManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._invites: Dict[str, InviteRecord] = {}  # code_hash -> InviteRecord

    def create_invite(
        self,
        max_uses: int = 1,
        expiry_hours: float = 24.0,
        prefix: str = "RCN",
        created_by: str = "operator",
    ) -> str:
        """Generate a secure, single-use or limited-use invite code. Returns the plaintext code."""
        random_suffix = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].upper()
        plaintext_code = f"{prefix}-{random_suffix}"
        code_hash = hashlib.sha256(plaintext_code.encode("utf-8")).hexdigest()

        record = InviteRecord(
            code_hash=code_hash,
            code_prefix=plaintext_code[:7] + "...",
            max_uses=max_uses,
            uses_count=0,
            expires_at=time.time() + (expiry_hours * 3600),
            is_revoked=False,
            created_by=created_by,
        )

        with self._lock:
            self._invites[code_hash] = record

        return plaintext_code

    def validate_and_consume(self, plaintext_code: str) -> bool:
        """Validate and increment usage of an invite code. Returns True if valid, False otherwise."""
        code_hash = hashlib.sha256(plaintext_code.strip().encode("utf-8")).hexdigest()
        now = time.time()

        with self._lock:
            record = self._invites.get(code_hash)
            if not record:
                return False
            if record.is_revoked:
                return False
            if now > record.expires_at:
                return False
            if record.uses_count >= record.max_uses:
                return False

            record.uses_count += 1
            return True

    def revoke_invite(self, plaintext_or_hash: str) -> bool:
        """Revoke an invite code immediately by plaintext code or hash."""
        target_hash = (
            plaintext_or_hash
            if len(plaintext_or_hash) == 64
            else hashlib.sha256(plaintext_or_hash.strip().encode("utf-8")).hexdigest()
        )

        with self._lock:
            record = self._invites.get(target_hash)
            if record:
                record.is_revoked = True
                return True
            return False

    def list_invites(self) -> list[dict]:
        """List active and historic invite codes (redacted prefixes only)."""
        with self._lock:
            return [
                {
                    "code_prefix": inv.code_prefix,
                    "code_hash": inv.code_hash,
                    "uses_count": inv.uses_count,
                    "max_uses": inv.max_uses,
                    "expires_at": inv.expires_at,
                    "is_expired": time.time() > inv.expires_at,
                    "is_revoked": inv.is_revoked,
                    "is_valid": (not inv.is_revoked) and (time.time() <= inv.expires_at) and (inv.uses_count < inv.max_uses),
                }
                for inv in self._invites.values()
            ]
