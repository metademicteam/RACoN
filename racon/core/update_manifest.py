"""Secure Update Manifest & Verification Logic.

Implements Section 10 Update Verification:
Validates HTTPS-hosted update manifests against the pinned coordinator Ed25519 root authority.
Verifies SHA-256 installer checksum before execution.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional
from pydantic import BaseModel, Field

from protocols import verify_signature, load_public_key_from_hex


class UpdateManifest(BaseModel):
    version: str = Field(..., max_length=32)
    release_date: str = Field(..., max_length=32)
    min_protocol_version: str = Field(..., max_length=16)
    installer_url: str = Field(..., max_length=512)
    installer_sha256: str = Field(..., min_length=64, max_length=64)
    installer_size_bytes: int = Field(..., gt=0)
    release_notes: str = Field(..., max_length=8192)
    signature_hex: str = Field(..., min_length=128, max_length=128)

    def canonical_bytes(self) -> bytes:
        data = {
            "version": self.version,
            "release_date": self.release_date,
            "min_protocol_version": self.min_protocol_version,
            "installer_url": self.installer_url,
            "installer_sha256": self.installer_sha256,
            "installer_size_bytes": self.installer_size_bytes,
            "release_notes": self.release_notes,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_update_manifest(manifest: UpdateManifest, trusted_public_key_hex: str) -> bool:
    """Verify that the manifest has been signed by the trusted release authority."""
    try:
        pub_key = load_public_key_from_hex(trusted_public_key_hex)
        return verify_signature(pub_key, manifest.canonical_bytes(), manifest.signature_hex)
    except Exception:
        return False


def verify_installer_checksum(installer_data: bytes, expected_sha256: str) -> bool:
    """Verify downloaded installer binary against the expected SHA-256 hash."""
    actual_hash = hashlib.sha256(installer_data).hexdigest()
    return actual_hash.lower() == expected_sha256.lower()
