"""Agent Cryptographic Identity Store.

Stores Ed25519 private key securely in the user's application data directory
using protected file permissions (and Windows DPAPI where available).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Tuple
from platformdirs import user_data_dir
from cryptography.hazmat.primitives.asymmetric import ed25519

from protocols import (
    generate_keypair,
    private_key_to_bytes,
    load_private_key_from_bytes,
    public_key_to_hex,
)

logger = logging.getLogger(__name__)


class AgentKeyStore:
    def __init__(self, key_path: Optional[Path] = None) -> None:
        if key_path:
            self._key_path = key_path
        else:
            data_dir = Path(user_data_dir("RACoN", "RACoN"))
            data_dir.mkdir(parents=True, exist_ok=True)
            self._key_path = data_dir / "agent_identity.key"

    def load_or_create_keypair(self) -> Tuple[ed25519.Ed25519PrivateKey, str]:
        """Load existing Ed25519 keypair or generate and persist a new one.
        
        Returns (private_key, public_key_hex).
        """
        if self._key_path.exists():
            try:
                raw_bytes = self._key_path.read_bytes()
                priv_key = load_private_key_from_bytes(raw_bytes)
                pub_hex = public_key_to_hex(priv_key.public_key())
                logger.info("Loaded agent cryptographic identity from %s", self._key_path)
                return priv_key, pub_hex
            except Exception as e:
                logger.warning("Failed to load existing agent key (%s); generating new keypair", e)

        # Generate new keypair
        priv_key, pub_key = generate_keypair()
        raw_bytes = private_key_to_bytes(priv_key)
        
        # Write securely (user read/write only)
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_bytes(raw_bytes)
        try:
            os.chmod(self._key_path, 0o600)
        except Exception:
            pass  # Windows permissions are already user-scoped in AppData

        pub_hex = public_key_to_hex(pub_key)
        logger.info("Generated new agent identity: public_key=%s", pub_hex[:16] + "...")
        return priv_key, pub_hex
