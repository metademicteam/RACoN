"""Operator admin API for the RACoN server.

Everything under ``/api/v1/admin`` is protected by the same per-install token as the rest
of the API. It powers the standalone admin dashboard served at ``/admin``:

* live worker inventory with hardware details and heartbeat freshness
* the node-to-node request log (who asked whom for work, through this server)
* agentic scheduler state: jobs, task DAGs, assigned workers, outcomes
* model download progress and invite management
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.racon.security import get_auth_token
from protocols import TaskType
from racon.config import VERSION
from racon.runtime.distributed_gguf import find_llama_tool
from racon.services.engine_service import get_engine
from racon.settings import get_settings

from .coordinator_state import get_coordinator_state
from .routes_models import _PULL_STATUS
from ..storage.db import database_info, get_database, get_engine as get_db_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])

ONLINE_WINDOW_SECONDS = 30.0


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _merge_nodes() -> list[dict[str, Any]]:
    """Merge the live coordinator registry with the persisted node table."""
    state = get_coordinator_state()
    persisted = {node["id"]: node for node in get_database().get_nodes()}
    merged: dict[str, dict[str, Any]] = {}
    now = time.time()

    for record in state.registry.list_nodes():
        caps = record.capabilities
        age = max(0.0, now - float(record.last_heartbeat or 0.0))
        node = persisted.get(record.node_id, {})
        merged[record.node_id] = {
            "id": record.node_id,
            "name": node.get("name") or f"{caps.os_name or 'Worker'} · {record.node_id[:12]}",
            "is_local": bool(node.get("is_local", False)),
            "origin": "registered",
            "trust_state": record.trust_state.value,
            "status": (
                "available"
                if record.trust_state.value == "approved" and age <= ONLINE_WINDOW_SECONDS
                else "offline"
            ),
            "online": age <= ONLINE_WINDOW_SECONDS,
            "role": record.role.value,
            "registered_at": _iso(record.registered_at),
            "last_heartbeat": _iso(record.last_heartbeat),
            "heartbeat_age_s": round(age, 1),
            "host": node.get("host"),
            "cpu": {
                "name": caps.cpu_name,
                "cores": caps.cpu_cores,
                "utilization_pct": round(float(record.cpu_util_pct or 0.0), 1),
            },
            "ram_total_mb": round(float(caps.ram_total_mb or 0.0), 1),
            "ram_available_mb": round(float(record.ram_available_mb or caps.ram_available_mb or 0.0), 1),
            "gpu": {
                "name": caps.gpu_name,
                "vram_total_mb": caps.gpu_vram_mb,
            },
            "runtimes": list(caps.runtimes or []),
            "rpc_endpoint": caps.rpc_endpoint,
            "os": caps.os_name,
            "models": list(record.models or []),
            "features": list(record.features or []),
            "active_tasks": list(record.active_tasks or []),
            "simulated": bool(caps.is_simulated),
        }

    for node_id, node in persisted.items():
        if node_id in merged:
            continue
        caps = node.get("capabilities") or {}
        gpu = caps.get("gpu") if isinstance(caps.get("gpu"), dict) else {}
        merged[node_id] = {
            "id": node_id,
            "name": node.get("name", node_id),
            "is_local": bool(node.get("is_local", False)),
            "origin": "local" if node.get("is_local") else "remembered",
            "trust_state": "approved" if node.get("is_local") else "offline",
            "status": "available" if node.get("is_local") else "offline",
            "online": bool(node.get("is_local", False)),
            "role": "head" if node.get("is_local") else "contributor",
            "registered_at": node.get("registered_at"),
            "last_heartbeat": node.get("last_heartbeat"),
            "heartbeat_age_s": None,
            "host": node.get("host"),
            "cpu": {
                "name": caps.get("cpu_name") or node.get("cpu_name") or "Unknown",
                "cores": node.get("cpu_cores") or caps.get("cpu_logical_count"),
                "utilization_pct": node.get("workload_pct"),
            },
            "ram_total_mb": node.get("ram_total_mb") or caps.get("ram_total_mb"),
            "ram_available_mb": node.get("ram_available_mb") or caps.get("ram_available_mb"),
            "gpu": {
                "name": caps.get("gpu_name") or gpu.get("name"),
                "vram_total_mb": caps.get("gpu_vram_mb"),
            },
            "runtimes": caps.get("runtimes", []),
            "rpc_endpoint": caps.get("rpc_endpoint"),
            "os": caps.get("os"),
            "models": caps.get("models", []),
            "features": caps.get("features", []),
            "active_tasks": [],
            "simulated": False,
        }

    return sorted(
        merged.values(),
        key=lambda item: (0 if item.get("is_local") else 1, item.get("id", "")),
    )


@router.get("/overview")
def admin_overview() -> dict[str, Any]:
    """One-screen summary for the dashboard header and health cards."""
    state = get_coordinator_state()
    db = get_database()
    nodes = _merge_nodes()
    jobs = db.list_orchestration_jobs(limit=500)
    routing = db.list_routing_events(limit=1)

    running = sum(1 for job in jobs if job.get("status") in ("queued", "planning", "running", "synthesizing"))
    completed = sum(1 for job in jobs if job.get("status") == "completed")
    failed = sum(1 for job in jobs if job.get("status") in ("failed", "cancelled"))

    return {
        "server": {
            "version": VERSION,
            "uptime_seconds": round(time.time() - state.created_at, 1),
            "started_at": _iso(state.created_at),
            "database": database_info(),
            "coordinator_public_key": state.public_key_hex,
            "auto_approve": state.auto_approve,
            "auto_join_enabled": bool(state.auto_join_code),
        },
        "nodes": {
            "total": len(nodes),
            "online": sum(1 for node in nodes if node.get("online")),
            "approved": sum(1 for node in nodes if node.get("trust_state") == "approved"),
            "pending": sum(1 for node in nodes if node.get("trust_state") == "pending"),
            "offline": sum(1 for node in nodes if not node.get("online")),
        },
        "scheduler": {
            "jobs_total": len(jobs),
            "jobs_running": running,
            "jobs_completed": completed,
            "jobs_failed": failed,
        },
        "routing": {
            "last_event_at": routing[-1]["timestamp"] if routing else None,
            "last_event": routing[-1] if routing else None,
        },
        "downloads": {
            "active": sum(
                1
                for entry in _PULL_STATUS.values()
                if entry.get("status") in ("downloading", "pulling")
            ),
            "models": list(_PULL_STATUS.values())[:10],
        },
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/nodes")
def admin_nodes() -> list[dict[str, Any]]:
    """Every known worker, with hardware details and live heartbeat state."""
    return _merge_nodes()


@router.get("/events")
def admin_events(
    limit: int = Query(200, ge=1, le=1000),
    after_id: int = Query(0, ge=0),
    event_type: Optional[str] = Query(None, max_length=48),
    node: Optional[str] = Query(None, max_length=128),
) -> dict[str, Any]:
    """Node-to-node routing log, oldest first within the requested window."""
    events = get_database().list_routing_events(
        limit=limit, after_id=after_id, event_type=event_type, node=node
    )
    return {
        "events": events,
        "count": len(events),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/scheduler")
def admin_scheduler(
    limit: int = Query(50, ge=1, le=200),
    include_tasks: bool = Query(True),
) -> dict[str, Any]:
    """Agentic scheduler view: jobs, plan mode, workers and per-task outcomes."""
    db = get_database()
    jobs = db.list_orchestration_jobs(limit=limit)
    if include_tasks:
        for job in jobs:
            job["tasks"] = db.list_orchestration_tasks(job["id"])
    return {
        "jobs": jobs,
        "count": len(jobs),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/jobs/{job_id}")
def admin_job(job_id: str) -> dict[str, Any]:
    job = get_database().get_orchestration_job(job_id, include_tasks=True)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    job["events"] = get_database().list_job_events(job_id, limit=500)
    return job


@router.post("/workers/{node_id}/approve")
def admin_approve_worker(node_id: str) -> dict[str, Any]:
    state = get_coordinator_state()
    if not state.registry.approve_node(node_id):
        raise HTTPException(status_code=404, detail="Node not found or not pending")
    get_database().update_node_status(node_id, "available")
    get_database().log_routing_event(
        "worker_approved",
        source="admin",
        target=node_id,
        state="approved",
        message=f"Operator approved {node_id}",
    )
    return {"status": "approved", "node_id": node_id}


@router.post("/workers/{node_id}/revoke")
def admin_revoke_worker(node_id: str) -> dict[str, Any]:
    state = get_coordinator_state()
    if not state.registry.revoke_node(node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    get_database().update_node_status(node_id, "revoked")
    get_database().log_routing_event(
        "worker_revoked",
        source="admin",
        target=node_id,
        state="revoked",
        message=f"Operator revoked {node_id}",
    )
    return {"status": "revoked", "node_id": node_id}


class DispatchRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=128)
    model_name: str = Field(default="qwen2.5:0.5b", max_length=128)
    prompt: str = Field(..., min_length=1, max_length=8000)
    max_tokens: int = Field(default=128, ge=1, le=4096)


@router.post("/tasks/dispatch")
def admin_dispatch_task(req: DispatchRequest) -> dict[str, Any]:
    """Send one allow-listed completion task to a specific worker (operator test)."""
    state = get_coordinator_state()
    node = state.registry.get_node(req.node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node is not enrolled")
    if node.trust_state.value != "approved":
        raise HTTPException(status_code=409, detail="Node is not approved")

    envelope = state.tasks.create_task(
        node_id=req.node_id,
        task_type=TaskType.TEXT_COMPLETION,
        model_name=req.model_name,
        parameters={"prompt": req.prompt, "max_tokens": req.max_tokens},
    )
    get_database().log_routing_event(
        "task_dispatched",
        source="coordinator",
        target=req.node_id,
        task_id=envelope.task_id,
        state="assigned",
        message=f"Operator dispatched task {envelope.task_id} to {req.node_id}",
        detail={"model": req.model_name, "max_tokens": req.max_tokens},
    )
    return {
        "task_id": envelope.task_id,
        "node_id": req.node_id,
        "status": "assigned",
    }


@router.get("/tasks")
def admin_tasks() -> list[dict[str, Any]]:
    return get_coordinator_state().tasks.list_tasks()


@router.get("/downloads")
def admin_downloads() -> dict[str, Any]:
    """Live model download progress tracked by the model routes."""
    return {"downloads": _PULL_STATUS, "server_time": datetime.now(timezone.utc).isoformat()}


@router.get("/invites")
def admin_invites() -> list[dict[str, Any]]:
    return get_coordinator_state().invites.list_invites()


class InviteRequest(BaseModel):
    max_uses: int = Field(default=5, ge=1, le=1000)
    expiry_hours: float = Field(default=24.0, ge=0.1, le=24 * 365)


@router.post("/invites")
def admin_create_invite(req: InviteRequest) -> dict[str, Any]:
    code = get_coordinator_state().invites.create_invite(
        max_uses=req.max_uses, expiry_hours=req.expiry_hours
    )
    return {"invite_code": code, "max_uses": req.max_uses, "expiry_hours": req.expiry_hours}


def _check(name: str, status: str, detail: str, hint: Optional[str] = None) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "hint": hint}


@router.get("/diagnostics")
async def admin_diagnostics() -> dict[str, Any]:
    """Structured operator diagnostics (the RACoN equivalent of `mesh-llm doctor`)."""
    checks: list[dict[str, Any]] = []

    # Database
    try:
        info = database_info()
        with get_db_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        checks.append(
            _check(
                "database",
                "ok",
                f"{info['backend']} · {info['database'] or 'local'}",
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "database",
                "fail",
                f"{type(exc).__name__}: {exc}",
                "Check DATABASE_URL, PostgreSQL health and credentials.",
            )
        )

    # Coordinator identity and registry
    try:
        state = get_coordinator_state()
        nodes = state.registry.list_nodes()
        approved = sum(1 for node in nodes if node.trust_state.value == "approved")
        checks.append(
            _check(
                "coordinator",
                "ok",
                f"identity {state.public_key_hex[:16]}… · {len(nodes)} node(s), {approved} approved",
            )
        )
    except Exception as exc:
        checks.append(
            _check("coordinator", "fail", f"{type(exc).__name__}: {exc}", "Restart the server process.")
        )

    # Active inference runtime
    try:
        engine_status = await get_engine().status()
        health = engine_status.get("health") or {}
        available = bool(health.get("available"))
        runtime_name = (engine_status.get("runtime") or {}).get("runtime", "unknown")
        checks.append(
            _check(
                "inference_runtime",
                "ok" if available else "fail",
                f"{runtime_name} · available={available} · "
                f"loaded={engine_status.get('loaded_model') or 'none'}",
                None if available else "Start Ollama, or install llama.cpp and set RACON_RUNTIME=llama_cpp.",
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "inference_runtime",
                "fail",
                f"{type(exc).__name__}: {exc}",
                "Check the runtime configuration and model directory.",
            )
        )

    # Ollama (optional)
    try:
        ollama_url = get_settings().ollama_url.rstrip("/")
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{ollama_url}/api/tags")
        count = len(response.json().get("models", [])) if response.status_code == 200 else 0
        checks.append(
            _check(
                "ollama",
                "ok" if response.status_code == 200 else "warn",
                f"{ollama_url} · HTTP {response.status_code} · {count} model(s)",
                None if response.status_code == 200 else "Optional when llama.cpp is available.",
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "ollama",
                "warn",
                f"{type(exc).__name__}: {exc}",
                "Optional; the server can serve GGUF models with llama.cpp instead.",
            )
        )

    # llama.cpp binary
    binary = find_llama_tool("llama-server")
    checks.append(
        _check(
            "llama_cpp",
            "ok" if binary else "warn",
            binary or "llama-server not found",
            None
            if binary
            else "Install llama.cpp (winget install ggml.llamacpp) or use the Docker image.",
        )
    )

    # Models and disk
    try:
        manager = get_engine().models
        installed = manager.installed()
        checks.append(
            _check(
                "models",
                "ok" if installed else "warn",
                f"{len(installed)} installed · {manager.disk_free_mb():.0f} MB free · {manager.models_dir}",
                None if installed else "Download a verified model from the Model Hub.",
            )
        )
    except Exception as exc:
        checks.append(
            _check("models", "warn", f"{type(exc).__name__}: {exc}", "Check RACON_MODEL_DIR.")
        )

    # Worker liveness
    try:
        merged = _merge_nodes()
        online = sum(1 for node in merged if node.get("online"))
        checks.append(
            _check(
                "workers",
                "ok" if online else "warn",
                f"{online}/{len(merged)} online",
                None if online else "Start an agent with RACON_COORDINATOR_URL set.",
            )
        )
    except Exception as exc:
        checks.append(_check("workers", "warn", f"{type(exc).__name__}: {exc}"))

    # Routing audit
    try:
        events = get_database().list_routing_events(limit=1)
        checks.append(
            _check(
                "routing_log",
                "ok",
                f"last event: {events[-1]['timestamp']}" if events else "no events yet",
            )
        )
    except Exception as exc:
        checks.append(_check("routing_log", "warn", f"{type(exc).__name__}: {exc}"))

    # Tailscale
    tailscale = shutil.which("tailscale")
    if not tailscale:
        checks.append(
            _check(
                "tailscale",
                "warn",
                "tailscale CLI not found",
                "Install Tailscale on the host to reach this server from other machines.",
            )
        )
    else:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [tailscale, "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = (result.stdout or "").strip()
            ip = output.splitlines()[0] if output else ""
            checks.append(
                _check(
                    "tailscale",
                    "ok" if ip else "warn",
                    f"{tailscale} · {ip or 'no IPv4 address'}",
                    None if ip else "Run `tailscale up`.",
                )
            )
        except Exception as exc:
            checks.append(
                _check("tailscale", "warn", f"{type(exc).__name__}: {exc}")
            )

    # Authentication strength
    try:
        token = get_auth_token()
        weak = token in ("racon-local-dev-token",) or len(token) < 20
        checks.append(
            _check(
                "auth",
                "warn" if weak else "ok",
                "development token in use" if weak else "custom token configured",
                "Change AUTH_TOKEN in .env before exposing this server."
                if weak
                else None,
            )
        )
    except Exception as exc:
        checks.append(_check("auth", "warn", f"{type(exc).__name__}: {exc}"))

    overall = (
        "failed"
        if any(check["status"] == "fail" for check in checks)
        else "degraded"
        if any(check["status"] == "warn" for check in checks)
        else "ok"
    )
    return {
        "status": overall,
        "checks": checks,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }
