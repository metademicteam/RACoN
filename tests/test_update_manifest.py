"""Tests for secure update manifest verification."""
import hashlib
import json
import pytest

from protocols import generate_keypair, public_key_to_hex, sign_message
from racon.core.update_manifest import (
    UpdateManifest,
    verify_update_manifest,
    verify_installer_checksum,
)


def test_update_manifest_verification_and_tamper_detection():
    priv, pub = generate_keypair()
    pub_hex = public_key_to_hex(pub)

    installer_content = b"Simulated Inno Setup Installer Executable Binary Data"
    installer_hash = hashlib.sha256(installer_content).hexdigest()

    manifest_data = {
        "version": "0.2.0",
        "release_date": "2026-11-01",
        "min_protocol_version": "1.0.0",
        "installer_url": "https://releases.racon.network/v0.2.0/RACoN_Setup.exe",
        "installer_sha256": installer_hash,
        "installer_size_bytes": len(installer_content),
        "release_notes": "Adds remote worker execution and Tailscale Funnel ingress.",
    }

    # Canonicalize and sign
    canonical_bytes = json.dumps(manifest_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig_hex = sign_message(priv, canonical_bytes)

    manifest = UpdateManifest(
        version=manifest_data["version"],
        release_date=manifest_data["release_date"],
        min_protocol_version=manifest_data["min_protocol_version"],
        installer_url=manifest_data["installer_url"],
        installer_sha256=manifest_data["installer_sha256"],
        installer_size_bytes=manifest_data["installer_size_bytes"],
        release_notes=manifest_data["release_notes"],
        signature_hex=sig_hex,
    )

    # 1. Valid signature verifies
    assert verify_update_manifest(manifest, pub_hex) is True

    # 2. Checksum verifies installer binary
    assert verify_installer_checksum(installer_content, installer_hash) is True

    # 3. Corrupted installer binary fails checksum
    assert verify_installer_checksum(b"Tampered executable data", installer_hash) is False

    # 4. Tampered manifest field fails signature verification
    tampered_manifest = manifest.model_copy(update={"version": "0.3.0"})
    assert verify_update_manifest(tampered_manifest, pub_hex) is False
