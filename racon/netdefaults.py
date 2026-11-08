"""Network defaults that let an installed app join and use RACoN with no manual setup.

Every value is overridable by an environment variable so an operator can point a deployment
elsewhere without rebuilding the installer.
"""
from __future__ import annotations

import os
import socket


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


# Installed clients join the public coordinator by default. A machine that runs the
# coordinator itself (Docker container or `python -m backend.racon.api`) is detected at
# startup and uses the loopback endpoint instead. Override either case with
#   RACON_COORDINATOR_URL=http://<host>:8765
PUBLIC_COORDINATOR_URL = _env(
    "RACON_COORDINATOR_URL", "https://desktop-b7l73cl.tail6cb521.ts.net"
)
DEFAULT_COORDINATOR_URL = PUBLIC_COORDINATOR_URL
DEFAULT_JOIN_CODE = _env("RACON_JOIN_CODE", "RCN-PUBLIC-JOIN")
DEFAULT_GATEWAY_URL = _env("RACON_GATEWAY_URL_DEFAULT", "https://racon.metademic.org")


def _local_coordinator_available(timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=timeout):
            return True
    except OSError:
        return False


def default_coordinator_url() -> str:
    """Pick the coordinator this machine should join.

    An explicit ``RACON_COORDINATOR_URL`` wins. A co-located coordinator is used when the
    local endpoint answers, otherwise the public server baked into the app is used.
    """
    explicit = os.environ.get("RACON_COORDINATOR_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    if _local_coordinator_available():
        return "http://127.0.0.1:8765"
    return PUBLIC_COORDINATOR_URL.rstrip("/")


def coordinator_url(explicit: str | None = None) -> str:
    """Resolve the coordinator URL from an argument, the environment, or the default."""
    if explicit and explicit.strip():
        return explicit.strip().rstrip("/")
    return default_coordinator_url()


def rpc_port() -> int:
    try:
        return int(os.environ.get("RACON_RPC_PORT", "50052"))
    except ValueError:
        return 50052
