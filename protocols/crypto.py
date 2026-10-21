"""Cryptographic utilities for RACoN protocols.

Standard Ed25519 signing and verification with SHA-256 payload hashing.
Uses the well-reviewed `cryptography` library (no custom crypto).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_keypair() -> Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Generate a new Ed25519 private/public keypair."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def private_key_to_bytes(private_key: ed25519.Ed25519PrivateKey) -> bytes:
    """Export raw 32-byte private key representation."""
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_key_to_bytes(public_key: ed25519.Ed25519PublicKey) -> bytes:
    """Export raw 32-byte public key representation."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_to_hex(public_key: ed25519.Ed25519PublicKey) -> str:
    """Export public key as 64-char hexadecimal string."""
    return public_key_to_bytes(public_key).hex()


def load_private_key_from_bytes(data: bytes) -> ed25519.Ed25519PrivateKey:
    """Load an Ed25519 private key from raw 32 bytes."""
    if len(data) != 32:
        raise ValueError(f"Invalid Ed25519 private key length: expected 32 bytes, got {len(data)}")
    return ed25519.Ed25519PrivateKey.from_private_bytes(data)


def load_public_key_from_hex(hex_str: str) -> ed25519.Ed25519PublicKey:
    """Load an Ed25519 public key from a 64-character hex string."""
    raw = bytes.fromhex(hex_str)
    if len(raw) != 32:
        raise ValueError(f"Invalid Ed25519 public key length: expected 32 bytes, got {len(raw)}")
    return ed25519.Ed25519PublicKey.from_public_bytes(raw)


def canonicalize_json(data: Any) -> bytes:
    """Format JSON deterministically (sorted keys, compact separators, UTF-8 encoded)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hash_payload(data: bytes) -> str:
    """Compute SHA-256 digest of binary payload as hex string."""
    return hashlib.sha256(data).hexdigest()


def sign_message(private_key: ed25519.Ed25519PrivateKey, message: bytes) -> str:
    """Sign a message and return the 64-byte signature as hex."""
    signature = private_key.sign(message)
    return signature.hex()


def verify_signature(public_key: ed25519.Ed25519PublicKey, message: bytes, signature_hex: str) -> bool:
    """Verify an Ed25519 signature against a message. Returns True if valid, False otherwise."""
    try:
        sig_bytes = bytes.fromhex(signature_hex)
        public_key.verify(sig_bytes, message)
        return True
    except (InvalidSignature, ValueError):
        return False
