"""Discover approved RPC workers from the local coordinator's admin registry.

Runs on the head PC. Worker agents report their rpc_endpoint during enrollment, so the head can
rebuild its worker list without anyone editing cluster.json.
"""
from __future__ import annotations

import logging
from typing import List

import httpx

from racon.cluster.config import WorkerEndpoint

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_URL = "http://127.0.0.1:8766"
_LOOPBACK = {"127.0.0.1", "localhost", "0.0.0.0", "::", ""}


def discover_workers(admin_url: str = DEFAULT_ADMIN_URL, timeout: float = 3.0) -> List[WorkerEndpoint]:
    """Return approved workers that advertised a usable RPC endpoint."""
    try:
        resp = httpx.get(f"{admin_url.rstrip('/')}/admin/nodes", timeout=timeout)
        if resp.status_code != 200:
            logger.debug("Coordinator admin API returned %s", resp.status_code)
            return []
        nodes = resp.json()
    except Exception as exc:
        logger.debug("Coordinator admin API unavailable: %s", exc)
        return []

    workers: List[WorkerEndpoint] = []
    for node in nodes:
        if str(node.get("trust_state", "")).lower() != "approved":
            continue
        endpoint = (node.get("capabilities") or {}).get("rpc_endpoint")
        if not endpoint:
            continue
        try:
            host, port_text = endpoint.rsplit(":", 1)
            port = int(port_text)
        except (ValueError, AttributeError):
            continue
        if host in _LOOPBACK or not 1 <= port <= 65535:
            continue
        workers.append(WorkerEndpoint(name=str(node.get("node_id", host)), host=host, port=port))

    return workers
