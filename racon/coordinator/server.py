"""Dual-Listener Coordinator Server Implementation.

Implements ADR-006:
1. Public Worker-Facing API (WorkerIngress): Minimal, control-plane only routes.
2. Loopback-Only Admin API (AdminControl): Operator management surface, never published.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from protocols import (
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatMessage,
    HeartbeatAck,
    TaskResultEnvelope,
    TaskEnvelope,
    PROTOCOL_VERSION,
)
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry
from racon.coordinator.tasks import TaskManager
from racon.coordinator.ingress import PublicIngress, TailscaleFunnelIngress

logger = logging.getLogger(__name__)


def create_worker_api(
    registry: NodeRegistry,
    invites: InviteManager,
    tasks: TaskManager,
) -> FastAPI:
    """Create the Worker-facing FastAPI application (published via Funnel).
    
    Contains strictly worker control routes. Zero admin routes exist in this router.
    """
    app = FastAPI(
        title="RACoN Worker Ingress API",
        version=PROTOCOL_VERSION,
        docs_url=None,  # Disable Swagger UI on public endpoint to reduce surface
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health_check() -> Dict[str, str]:
        """Minimal unauthenticated health check route."""
        return {
            "status": "healthy",
            "version": PROTOCOL_VERSION,
            "service": "racon-worker-ingress",
        }

    @app.post("/enroll", response_model=EnrollmentResponse)
    async def enroll_worker(req: EnrollmentRequest) -> EnrollmentResponse:
        """Enroll a new worker node with an invite code."""
        # 1. Validate invite code
        is_valid = invites.validate_and_consume(req.invite_code)
        # 2. Process enrollment through node registry
        resp = registry.register_node(req, is_invite_valid=is_valid, auto_approve=False)
        return resp

    @app.post("/heartbeat", response_model=HeartbeatAck)
    async def receive_heartbeat(msg: HeartbeatMessage) -> HeartbeatAck:
        """Process periodic heartbeat from an enrolled worker."""
        return registry.record_heartbeat(msg)

    @app.post("/tasks/claim")
    async def claim_task(req: Dict[str, Any]) -> Dict[str, Any]:
        """Worker checks if a task has been assigned to it."""
        node_id = req.get("node_id")
        token = req.get("session_token")
        if not node_id or not token or not registry.authenticate_session(node_id, token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session credentials")

        # Search for assigned task
        for t in tasks.list_tasks():
            if t["node_id"] == node_id and t["state"] == "assigned":
                task_full = tasks.get_task(t["task_id"])
                return {"has_task": True, "task": task_full}

        return {"has_task": False, "task": None}

    @app.post("/tasks/report")
    async def report_task_result(result: TaskResultEnvelope) -> Dict[str, Any]:
        """Worker reports completed task result."""
        node = registry.get_node(result.node_id)
        if not node:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not registered")

        success = tasks.record_result(result, worker_public_key_hex=node.public_key_hex)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to record task result")

        return {"status": "accepted", "task_id": result.task_id}

    return app


def create_admin_api(
    registry: NodeRegistry,
    invites: InviteManager,
    tasks: TaskManager,
    ingress: PublicIngress,
) -> FastAPI:
    """Create the Loopback-Only Operator Admin API.
    
    MUST NEVER BE PUBLISHED VIA FUNNEL OR EXTERNAL PROXY.
    """
    app = FastAPI(
        title="RACoN Coordinator Admin Control",
        version=PROTOCOL_VERSION,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/admin/status")
    async def get_coordinator_status() -> Dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "registered_nodes_count": len(registry.list_nodes()),
            "ingress": ingress.get_status().model_dump(),
        }

    @app.get("/admin/nodes")
    async def list_nodes() -> list[dict]:
        return [n.model_dump() for n in registry.list_nodes()]

    @app.post("/admin/nodes/{node_id}/approve")
    async def approve_node(node_id: str) -> Dict[str, Any]:
        success = registry.approve_node(node_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found or not in pending state")
        return {"status": "approved", "node_id": node_id}

    @app.post("/admin/nodes/{node_id}/revoke")
    async def revoke_node(node_id: str) -> Dict[str, Any]:
        success = registry.revoke_node(node_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
        return {"status": "revoked", "node_id": node_id}

    @app.get("/admin/invites")
    async def list_invites() -> list[dict]:
        return invites.list_invites()

    @app.post("/admin/invites")
    async def create_invite(payload: Dict[str, Any]) -> Dict[str, Any]:
        max_uses = payload.get("max_uses", 1)
        expiry_hours = payload.get("expiry_hours", 24.0)
        code = invites.create_invite(max_uses=max_uses, expiry_hours=expiry_hours)
        return {"invite_code": code, "max_uses": max_uses, "expiry_hours": expiry_hours}

    @app.post("/admin/invites/{code_hash}/revoke")
    async def revoke_invite(code_hash: str) -> Dict[str, Any]:
        success = invites.revoke_invite(code_hash)
        return {"status": "revoked" if success else "not_found"}

    @app.get("/admin/tasks")
    async def list_tasks() -> list[dict]:
        return tasks.list_tasks()

    @app.post("/admin/tasks")
    async def dispatch_task(payload: Dict[str, Any]) -> Dict[str, Any]:
        node_id = payload.get("node_id")
        task_type = payload.get("task_type", "text_completion")
        model_name = payload.get("model_name", "qwen2.5:1.5b")
        parameters = payload.get("parameters", {})

        from protocols import TaskType
        envelope = tasks.create_task(
            node_id=node_id,
            task_type=TaskType(task_type),
            model_name=model_name,
            parameters=parameters,
        )
        return {"task_id": envelope.task_id, "node_id": node_id, "status": "assigned"}

    @app.get("/admin/ingress")
    async def get_ingress_status() -> Dict[str, Any]:
        return ingress.get_status().model_dump()

    @app.post("/admin/ingress/enable")
    async def enable_ingress(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        local_port = (payload or {}).get("local_port", 8765)
        public_port = (payload or {}).get("public_port", 443)
        res = ingress.enable(local_port=local_port, public_port=public_port)
        return res.model_dump()

    @app.post("/admin/ingress/disable")
    async def disable_ingress() -> Dict[str, Any]:
        """Kill switch for public access."""
        res = ingress.disable()
        return res.model_dump()

    @app.post("/admin/ingress/probe")
    async def probe_ingress() -> Dict[str, Any]:
        report = ingress.probe_reachability()
        return report.model_dump()

    return app
