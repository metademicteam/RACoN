"""Discover approved RPC workers from the local coordinator's admin registry.

Runs on the head PC. Worker agents report their rpc_endpoint during enrollment, so the head can
rebuild its worker list without anyone editing cluster.json.

The head PC enrolls as a contributor too and runs its own rpc-server. That endpoint must never
enter the split: offloading layers to the head's own rpc-server halves the model onto two
processes on one machine, and its loopback traffic still pays RPC serialization. Endpoints that
resolve to one of this PC's own addresses are ignored.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Iterable, List, Optional, Set

import httpx

from racon.cluster.config import WorkerEndpoint

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_URL = "http://127.0.0.1:8766"
_LOOPBACK = {"127.0.0.1", "localhost", "0.0.0.0", "::", ""}
_LOCAL_CACHE: Optional[Set[str]] = None


def local_ip_addresses() -> Set[str]:
    """Every IPv4 address on this PC, including Tailscale, so self-endpoints can be excluded."""
    global _LOCAL_CACHE
    if _LOCAL_CACHE is not None:
        return set(_LOCAL_CACHE)

    addrs: Set[str] = {"127.0.0.1", "localhost"}
    try:
        import psutil

        for iface in psutil.net_if_addrs().values():
            for addr in iface:
                if addr.family == socket.AF_INET and addr.address:
                    addrs.add(addr.address)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    _LOCAL_CACHE = addrs
    return set(addrs)


def is_local_endpoint(host: str, local_hosts: Optional[Iterable[str]] = None) -> bool:
    """True when the endpoint belongs to this PC. Hostnames are resolved once."""
    key = (host or "").strip().lower()
    if key in _LOOPBACK:
        return True
    local = set(local_hosts) if local_hosts is not None else local_ip_addresses()
    if key in {h.lower() for h in local}:
        return True
    try:
        ip = ipaddress.ip_address(key)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(key))
        except (OSError, ValueError):
            return False
    return ip.is_loopback or str(ip) in local


def discover_workers(
    admin_url: str = DEFAULT_ADMIN_URL,
    timeout: float = 3.0,
    local_hosts: Optional[Iterable[str]] = None,
) -> List[WorkerEndpoint]:
    """Return approved remote workers that advertised a usable RPC endpoint."""
    try:
        resp = httpx.get(f"{admin_url.rstrip('/')}/admin/nodes", timeout=timeout)
        if resp.status_code != 200:
            logger.debug("Coordinator admin API returned %s", resp.status_code)
            return []
        nodes = resp.json()
    except Exception as exc:
        logger.debug("Coordinator admin API unavailable: %s", exc)
        return []

    local = set(local_hosts) if local_hosts is not None else local_ip_addresses()
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
        if not 1 <= port <= 65535:
            continue
        if is_local_endpoint(host, local):
            logger.info(
                "Ignoring RPC endpoint %s from node %s: it is this PC, not a remote worker.",
                endpoint, node.get("node_id", "?"),
            )
            continue
        workers.append(WorkerEndpoint(name=str(node.get("node_id", host)), host=host, port=port))

    return workers
