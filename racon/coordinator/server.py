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
    auto_approve: bool = False,
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
        resp = registry.register_node(req, is_invite_valid=is_valid, auto_approve=auto_approve)
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


def run_coordinator(
    worker_host: str = "0.0.0.0",
    worker_port: int = 8765,
    admin_port: int = 8766,
    auto_approve: bool = True,
    enable_funnel: bool = False,
) -> None:
    """Run dual-listener coordinator servers."""
    import sys
    import threading
    import uvicorn
    from pathlib import Path
    from protocols.crypto import (
        generate_keypair,
        private_key_to_bytes,
        load_private_key_from_bytes,
        public_key_to_hex,
    )
    from racon.coordinator.power_guard import CoordinatorPowerGuard
    from racon.config import get_data_dir

    # 1. Power guard check
    power_guard = CoordinatorPowerGuard()
    power_guard.check_power_plan()

    # 2. Key management (load or persist)
    key_dir = get_data_dir()
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "coordinator_key.bin"

    if key_file.exists():
        try:
            priv_bytes = key_file.read_bytes()
            coord_priv = load_private_key_from_bytes(priv_bytes)
            coord_pub = coord_priv.public_key()
            logger.info("Loaded coordinator identity key from %s", key_file)
        except Exception as e:
            logger.warning("Failed to load existing key: %s, generating new keypair", e)
            coord_priv, coord_pub = generate_keypair()
            key_file.write_bytes(private_key_to_bytes(coord_priv))
    else:
        coord_priv, coord_pub = generate_keypair()
        key_file.write_bytes(private_key_to_bytes(coord_priv))
        logger.info("Generated new coordinator identity key at %s", key_file)

    coord_pub_hex = public_key_to_hex(coord_pub)

    # 3. Instantiate coordinator components
    registry = NodeRegistry(coordinator_public_key_hex=coord_pub_hex)
    invites = InviteManager()
    tasks = TaskManager(coordinator_private_key=coord_priv)
    ingress = TailscaleFunnelIngress()

    if enable_funnel:
        ingress.enable(local_port=worker_port, public_port=443)

    ingress_status = ingress.get_status()

    # 4. Generate initial invite code
    init_invite = invites.create_invite(max_uses=20, expiry_hours=72.0)

    # 5. Build FastAPI apps
    worker_app = create_worker_api(registry=registry, invites=invites, tasks=tasks, auto_approve=auto_approve)
    admin_app = create_admin_api(registry=registry, invites=invites, tasks=tasks, ingress=ingress)

    # 6. Display banner
    public_url = ingress_status.public_url or "https://desktop-b7l73cl.tail6cb521.ts.net"
    print("\n" + "=" * 76)
    print("  🚀 RACoN Coordinator — Cluster Management Service")
    print("=" * 76)
    print(f"  Worker Ingress (Local):   http://{worker_host}:{worker_port}")
    print(f"  Public Tailscale Funnel:  {public_url}")
    print(f"  Admin Control (Loopback): http://127.0.0.1:{admin_port}")
    print(f"  Coordinator Identity:     {coord_pub_hex[:16]}...{coord_pub_hex[-8:]}")
    print(f"  Auto-Approve Workers:     {'ENABLED' if auto_approve else 'DISABLED (manual approval)'}")
    print("-" * 76)
    print(f"  🔑 ACTIVE INVITE CODE:     {init_invite}")
    print("=" * 76)
    print("  INSTRUCTIONS FOR REMOTE USERS:")
    print("  1. Download & run the installer on the remote PC:")
    print("     RACoN_Setup_v0.1.0.exe")
    print("  2. Open RACoN -> Click the '🖥 Worker Agent' tab")
    print(f"  3. In Coordinator URL, enter:")
    print(f"     {public_url}")
    print(f"  4. In Invite Code, enter:")
    print(f"     {init_invite}")
    print("  5. Click 'Enroll Node', then click 'Start Worker'")
    print("=" * 76 + "\n", flush=True)

    # 7. Start both servers
    def run_worker():
        uvicorn.run(worker_app, host=worker_host, port=worker_port, log_level="info")

    def run_admin():
        uvicorn.run(admin_app, host="127.0.0.1", port=admin_port, log_level="warning")

    t_admin = threading.Thread(target=run_admin, daemon=True)
    t_admin.start()

    run_worker()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="RACoN Dual-Listener Coordinator Server")
    parser.add_argument("--worker-host", default="0.0.0.0", help="Worker API bind address (default: 0.0.0.0)")
    parser.add_argument("--worker-port", type=int, default=8765, help="Worker API port (default: 8765)")
    parser.add_argument("--admin-port", type=int, default=8766, help="Admin API port (default: 8766)")
    parser.add_argument("--auto-approve", action="store_true", default=True, help="Automatically approve workers with valid invite codes (default: True)")
    parser.add_argument("--manual-approve", dest="auto_approve", action="store_false", help="Require manual operator approval for workers")
    parser.add_argument("--enable-funnel", action="store_true", default=False, help="Enable Tailscale Funnel on startup")
    args = parser.parse_args()

    run_coordinator(
        worker_host=args.worker_host,
        worker_port=args.worker_port,
        admin_port=args.admin_port,
        auto_approve=args.auto_approve,
        enable_funnel=args.enable_funnel,
    )


if __name__ == "__main__":
    main()

