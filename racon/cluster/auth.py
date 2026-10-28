"""API key issuance and verification. Only SHA-256 digests are stored on disk."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

KEY_PREFIX = "rk_"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ApiKeyStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text(encoding="utf-8")).get("keys", [])

    def _write(self, keys: List[Dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"keys": keys}, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def create(self, name: str) -> str:
        """Create a key and return the plaintext token. It cannot be recovered later."""
        token = KEY_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            keys = self._read()
            keys.append({
                "id": uuid.uuid4().hex[:12],
                "name": name,
                "sha256": _digest(token),
                "created_at": time.time(),
                "revoked": False,
            })
            self._write(keys)
        return token

    def list(self) -> List[Dict[str, Any]]:
        return [{k: v for k, v in entry.items() if k != "sha256"} for entry in self._read()]

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            keys = self._read()
            found = False
            for entry in keys:
                if entry["id"] == key_id and not entry["revoked"]:
                    entry["revoked"] = True
                    found = True
            if found:
                self._write(keys)
            return found

    def verify(self, token: str) -> bool:
        if not token or not token.startswith(KEY_PREFIX):
            return False
        digest = _digest(token)
        valid = False
        for entry in self._read():
            if not entry.get("revoked") and hmac.compare_digest(entry["sha256"], digest):
                valid = True
        return valid
