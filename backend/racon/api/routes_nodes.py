"""Worker and local node management routes."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/nodes", tags=["Nodes"])


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
    nodes = db.get_nodes()
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
