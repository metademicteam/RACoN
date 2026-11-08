"""Worker ingress routes for the unified RACoN server.

These are the routes a provider agent calls: ``/discover``, ``/enroll``, ``/heartbeat`` and
``/tasks/{claim,report}``. They were previously only available when the standalone
coordinator process (``racon.coordinator.server``) was running. The Docker container and
the desktop shell now expose them from the same FastAPI app, which is what makes joining
the network work in a single-container deployment.

Authentication is the Ed25519 envelope verification performed by ``NodeRegistry`` and
``TaskManager``; the admin API is separate and token protected.
"""
from __future__ import annotations

import hmac
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status

from protocols import (
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatAck,
    HeartbeatMessage,
    PROTOCOL_VERSION,
    TaskProgressMessage,
    TaskResultEnvelope,
    TrustState,
)
from racon import netdefaults

from .coordinator_state import get_coordinator_state
from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Worker Ingress"])


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _mirror_node(
    node_id: str,
    request: Request,
    *,
    status_value: str,
    caps: Any,
    models: Optional[list[str]] = None,
    features: Optional[list[str]] = None,
) -> None:
    """Persist the enrolled node so the admin dashboard survives coordinator restarts."""
    try:
        db = get_database()
        db.upsert_node(
            {
                "id": node_id,
                "name": f"{caps.os_name or 'Worker'} · {node_id[:12]}",
                "is_local": False,
                "status": status_value,
                "host": _client_host(request),
                "port": 8765,
                "ram_total_mb": float(caps.ram_total_mb or 0),
                "ram_available_mb": float(caps.ram_available_mb or 0),
                "capabilities": {
                    "cpu_name": caps.cpu_name,
                    "cpu_logical_count": caps.cpu_cores,
                    "ram_total_mb": float(caps.ram_total_mb or 0),
                    "ram_available_mb": float(caps.ram_available_mb or 0),
                    "gpu": {"name": caps.gpu_name} if caps.gpu_name else {},
                    "gpu_name": caps.gpu_name,
                    "gpu_vram_mb": caps.gpu_vram_mb,
                    "runtimes": list(caps.runtimes or []),
                    "rpc_endpoint": caps.rpc_endpoint,
                    "os": caps.os_name,
                    "models": list(models or []),
                    "features": list(features or []),
                },
            }
        )
    except Exception as exc:  # a mirror failure must never reject a valid worker
        logger.warning("Could not mirror node %s into the database: %s", node_id, exc)


@router.get("/discover")
async def discover() -> Dict[str, Any]:
    """Public capability probe used by the desktop app to configure itself."""
    state = get_coordinator_state()
    return {
        "service": "racon-worker-ingress",
        "version": PROTOCOL_VERSION,
        "auto_join": bool(state.auto_join_code),
        "auto_approve": state.auto_approve,
        "gateway_url": netdefaults.DEFAULT_GATEWAY_URL,
        "coordinator_public_key": state.public_key_hex,
    }


@router.get("/worker/health")
async def worker_health() -> Dict[str, Any]:
    state = get_coordinator_state()
    return {
        "status": "healthy",
        "service": "racon-worker-ingress",
        "version": PROTOCOL_VERSION,
        "nodes": len(state.registry.list_nodes()),
        "auto_approve": state.auto_approve,
    }


@router.post("/enroll", response_model=EnrollmentResponse)
async def enroll_worker(req: EnrollmentRequest, request: Request) -> EnrollmentResponse:
    """Enroll a provider. Accepts any valid invite or the built-in join code."""
    state = get_coordinator_state()

    is_valid = state.invites.validate_and_consume(req.invite_code)
    used_join_code = False
    if not is_valid and state.auto_join_code and hmac.compare_digest(
        req.invite_code.strip().encode("utf-8"), state.auto_join_code.encode("utf-8")
    ):
        is_valid = True
        used_join_code = True

    resp = state.registry.register_node(
        req, is_invite_valid=is_valid, auto_approve=state.auto_approve
    )

    if used_join_code and resp.status == "approved":
        logger.info("Node %s auto-joined with the built-in join code", req.node_id)

    if resp.trust_state == TrustState.APPROVED:
        try:
            from racon.cluster.auth import ApiKeyStore

            resp.gateway_key = ApiKeyStore(state.gateway_keys_path).create(f"node:{req.node_id}")
        except Exception as exc:  # key issuance must not fail the enrollment
            logger.error("Could not issue gateway key for %s: %s", req.node_id, exc)

    if resp.trust_state == TrustState.APPROVED:
        mirror_status = "available"
    elif resp.trust_state == TrustState.REVOKED:
        mirror_status = "revoked"
    else:
        mirror_status = "pending"
    _mirror_node(
        req.node_id,
        request,
        status_value=mirror_status,
        caps=req.capabilities,
        models=req.models,
        features=req.features,
    )

    try:
        get_database().log_routing_event(
            "enroll",
            source=req.node_id,
            target="coordinator",
            state=resp.trust_state.value,
            message=(
                f"Node {req.node_id} enrolled ({resp.trust_state.value})"
                + (" via auto-join code" if used_join_code else "")
            ),
            detail={
                "client": _client_host(request),
                "cpu": req.capabilities.cpu_name,
                "cpu_cores": req.capabilities.cpu_cores,
                "ram_total_mb": req.capabilities.ram_total_mb,
                "ram_available_mb": req.capabilities.ram_available_mb,
                "gpu": req.capabilities.gpu_name,
                "rpc_endpoint": req.capabilities.rpc_endpoint,
                "models": list(req.models),
                "features": list(req.features),
            },
        )
    except Exception as exc:
        logger.debug("Routing log failed for enrollment: %s", exc)

    return resp


@router.post("/heartbeat", response_model=HeartbeatAck)
async def receive_heartbeat(msg: HeartbeatMessage, request: Request) -> HeartbeatAck:
    """Record a heartbeat and live telemetry from an enrolled worker."""
    state = get_coordinator_state()
    ack = state.registry.record_heartbeat(msg)

    try:
        db = get_database()
        db.update_node_heartbeat(
            msg.node_id,
            ram_available=msg.ram_available_mb,
            models=msg.models if msg.models_reported else None,
        )
        db.log_routing_event(
            "heartbeat",
            source=msg.node_id,
            target="coordinator",
            state=ack.status,
            message=f"Heartbeat from {msg.node_id} (CPU {msg.cpu_util_pct:.0f}%)",
            detail={
                "cpu_util_pct": msg.cpu_util_pct,
                "ram_available_mb": msg.ram_available_mb,
                "active_tasks": msg.active_tasks,
                "client": _client_host(request),
            },
        )
    except Exception as exc:
        logger.debug("Heartbeat persistence failed: %s", exc)

    return ack


@router.post("/tasks/claim")
async def claim_task(req: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Atomically claim one assigned task. A task is delivered exactly once."""
    state = get_coordinator_state()
    node_id = req.get("node_id")
    token = req.get("session_token")
    if not node_id or not token or not state.registry.authenticate_session(node_id, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session credentials; re-enroll to refresh the session token.",
        )

    task = state.tasks.claim_task(node_id)
    if task is None:
        return {"has_task": False, "task": None}

    try:
        get_database().log_routing_event(
            "task_claimed",
            source=node_id,
            target="coordinator",
            task_id=task.get("task_id"),
            state="running",
            message=f"{node_id} claimed task {task.get('task_id')}",
            detail={"task_type": task.get("task_type"), "client": _client_host(request)},
        )
    except Exception as exc:
        logger.debug("Routing log failed for task claim: %s", exc)

    return {"has_task": True, "task": task}


@router.post("/tasks/progress")
async def report_task_progress(msg: TaskProgressMessage) -> Dict[str, Any]:
    """Receive a streamed delta for an in-flight chat task."""
    state = get_coordinator_state()
    if not state.registry.authenticate_session(msg.node_id, msg.session_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session credentials; re-enroll to refresh the session token.",
        )

    accepted = state.tasks.record_progress(
        msg.node_id, msg.task_id, msg.seq, msg.delta, msg.finish_reason
    )
    if accepted:
        from ..network.broker import get_delta_broker, task_channel

        get_delta_broker().publish(
            task_channel(msg.task_id),
            {
                "event": "delta",
                "task_id": msg.task_id,
                "node_id": msg.node_id,
                "seq": msg.seq,
                "delta": msg.delta,
                "finish_reason": msg.finish_reason,
            },
        )
    return {"accepted": accepted, "seq": msg.seq}


@router.post("/tasks/report")
async def report_task_result(result: TaskResultEnvelope, request: Request) -> Dict[str, Any]:
    """Worker reports a completed or failed task result."""
    state = get_coordinator_state()
    node = state.registry.get_node(result.node_id)
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not registered")

    accepted = state.tasks.record_result(result, worker_public_key_hex=node.public_key_hex)
    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to record task result"
        )

    try:
        from ..network.broker import get_delta_broker, task_channel

        get_delta_broker().publish(
            task_channel(result.task_id),
            {
                "event": "done",
                "task_id": result.task_id,
                "state": result.status.value,
                "result_data": result.result_data,
                "error": result.error,
                "execution_time_ms": result.execution_time_ms,
            },
        )
    except Exception:
        logger.debug("Could not publish terminal task event", exc_info=True)

    try:
        get_database().log_routing_event(
            "task_reported",
            source=result.node_id,
            target="coordinator",
            task_id=result.task_id,
            state=result.status.value,
            message=(
                f"{result.node_id} reported {result.status.value} for task {result.task_id} "
                f"({result.execution_time_ms:.0f} ms)"
            ),
            detail={"error": result.error, "client": _client_host(request)},
        )
    except Exception as exc:
        logger.debug("Routing log failed for task report: %s", exc)

    return {"status": "accepted", "task_id": result.task_id}
