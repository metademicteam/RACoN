"""Process-wide coordinator state for the unified RACoN server.

The desktop app and the headless server run the same FastAPI application. When the
application is reachable by workers it must also expose the worker ingress routes
(``/enroll``, ``/heartbeat``, ``/tasks/*``). Those routes need one shared, persistent
coordinator identity, node registry, invite store and task manager.

Keeping all of that in this module means the Docker container, ``python -m
backend.racon.api`` and the desktop shell all behave the same way. The coordinator private
key is written under ``RACON_DATA_DIR`` (``/data`` in the container) so restarts keep the
same identity.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from protocols.crypto import (
    generate_keypair,
    load_private_key_from_bytes,
    private_key_to_bytes,
    public_key_to_hex,
)
from racon import netdefaults
from racon.cluster.config import default_keys_path
from racon.config import get_data_dir
from racon.coordinator.ingress import TailscaleFunnelIngress
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry
from racon.coordinator.tasks import TaskManager

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_LOCK = threading.Lock()
_STATE: Optional["CoordinatorState"] = None


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in _TRUE


@dataclass
class CoordinatorState:
    """Everything the worker ingress and scheduler need."""

    registry: NodeRegistry
    invites: InviteManager
    tasks: TaskManager
    ingress: TailscaleFunnelIngress
    public_key_hex: str
    auto_approve: bool
    auto_join_code: str
    gateway_keys_path: Path
    created_at: float

    @property
    def session_ttl_seconds(self) -> float:
        return 15 * 60.0

    def ensure_initial_invite(self) -> Optional[str]:
        """Create a reusable invite once so the server banner always has a code."""
        try:
            if not self.invites.list_invites():
                return self.invites.create_invite(max_uses=50, expiry_hours=24 * 30)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not create the initial invite: %s", exc)
        return None


def _load_or_create_key() -> tuple[object, str]:
    key_dir = get_data_dir()
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "coordinator_key.bin"
    if key_file.exists():
        try:
            private = load_private_key_from_bytes(key_file.read_bytes())
            return private, public_key_to_hex(private.public_key())
        except Exception as exc:
            logger.warning("Coordinator key at %s is unreadable (%s); generating a new one", key_file, exc)
    private, _public = generate_keypair()
    try:
        key_file.write_bytes(private_key_to_bytes(private))
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.warning("Could not persist the coordinator key: %s", exc)
    return private, public_key_to_hex(private.public_key())


def get_coordinator_state() -> CoordinatorState:
    """Return the process-wide coordinator state, creating it on first use."""
    global _STATE
    if _STATE is not None:
        return _STATE

    with _LOCK:
        if _STATE is not None:
            return _STATE

        private, public_hex = _load_or_create_key()
        invites_path = get_data_dir() / "coordinator" / "invites.json"
        state = CoordinatorState(
            registry=NodeRegistry(coordinator_public_key_hex=public_hex),
            invites=InviteManager(store_path=invites_path),
            tasks=TaskManager(coordinator_private_key=private),  # type: ignore[arg-type]
            ingress=TailscaleFunnelIngress(),
            public_key_hex=public_hex,
            auto_approve=_env_bool("RACON_AUTO_APPROVE_WORKERS", True),
            auto_join_code=os.environ.get("RACON_JOIN_CODE", netdefaults.DEFAULT_JOIN_CODE),
            gateway_keys_path=default_keys_path(),
            created_at=time.time(),
        )
        state.ensure_initial_invite()
        _STATE = state
        logger.info(
            "Coordinator ready: identity=%s… auto_approve=%s auto_join=%s",
            public_hex[:16],
            state.auto_approve,
            bool(state.auto_join_code),
        )
        return state


def reset_coordinator_state() -> None:
    """Drop the singleton (tests only)."""
    global _STATE
    with _LOCK:
        _STATE = None
