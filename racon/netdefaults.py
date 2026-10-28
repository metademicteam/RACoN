"""Network defaults that let an installed app join and use RACoN with no manual setup.

Every value is overridable by an environment variable so an operator can point a deployment
elsewhere without rebuilding the installer.
"""
from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


DEFAULT_COORDINATOR_URL = _env("RACON_COORDINATOR_URL", "https://desktop-b7l73cl.tail6cb521.ts.net")
DEFAULT_JOIN_CODE = _env("RACON_JOIN_CODE", "RCN-PUBLIC-JOIN")
DEFAULT_GATEWAY_URL = _env("RACON_GATEWAY_URL_DEFAULT", "https://racon.metademic.org")


def rpc_port() -> int:
    try:
        return int(os.environ.get("RACON_RPC_PORT", "50052"))
    except ValueError:
        return 50052
