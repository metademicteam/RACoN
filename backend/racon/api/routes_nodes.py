"""Worker and local node management routes."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .coordinator_state import get_coordinator_state
from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/nodes", tags=["Nodes"])

ONLINE_WINDOW_SECONDS = 30.0


def _apply_live_status(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark remembered nodes offline unless the coordinator sees a fresh heartbeat."""
    try:
        live = {record.node_id: record for record in get_coordinator_state().registry.list_nodes()}
    except Exception:
        live = {}
    now = time.time()
    for node in nodes:
        if node.get("status") == "unavailable":
            node["online"] = False
            continue
        record = live.get(node["id"])
        if record is not None:
            age = max(0.0, now - float(record.last_heartbeat or 0.0))
            approved = record.trust_state.value == "approved"
            online = approved and age <= ONLINE_WINDOW_SECONDS
            node["status"] = "available" if online else "offline"
            node["online"] = online
        elif node.get("is_local"):
            node["status"] = "available"
            node["online"] = True
        else:
            node["status"] = "offline"
            node["online"] = False
    return nodes


class NodeRegisterRequest(BaseModel):
    id: str
    name: str
    is_local: bool = False
    device_type: str = "cpu"
    ram_total_mb: float = 16384.0
    ram_available_mb: float = 8192.0
    processing_speed_tps: Optional[float] = 25.0
    network_latency_ms: Optional[float] = 5.0
    workload_pct: float = 0.0
    capabilities: dict[str, Any] = {}


@router.get("")
def list_nodes():
    """List all registered worker nodes and the local compute node."""
    db = get_database()
    nodes = _apply_live_status(db.get_nodes())
    for n in nodes:
        if "cpu_cores" not in n:
            n["cpu_cores"] = n.get("capabilities", {}).get("cpu_cores", 4)
    return nodes


@router.get("/{node_id}")
def get_node_by_id(node_id: str):
    """Retrieve detailed telemetry and metadata for a specific node."""
    db = get_database()
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    node = _apply_live_status([node])[0]
    if "cpu_cores" not in node:
        node["cpu_cores"] = node.get("capabilities", {}).get("cpu_cores", 4)
    return node


@router.post("/{node_id}/start")
def start_node_session(node_id: str):
    """Start worker session on node (transition status to available)."""
    db = get_database()
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    db.update_node_status(node_id, "available")
    db.log_audit("NODE_START", f"Worker session started on node {node_id}")
    return {"status": "started", "node_id": node_id, "node_status": "available"}


@router.post("/{node_id}/stop")
def stop_node_session(node_id: str):
    """Stop worker session on node (transition status to unavailable)."""
    db = get_database()
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    db.update_node_status(node_id, "unavailable")
    db.log_audit("NODE_STOP", f"Worker session stopped on node {node_id}")
    return {"status": "stopped", "node_id": node_id, "node_status": "unavailable"}
