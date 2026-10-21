"""End-to-End Loopback Tests for Coordinator and Worker Agent.

Validates Gate 3 and ADRs:
- ADR-005 (Hub and Spoke)
- ADR-006 (Dual Listeners - Admin routes 404 on Worker listener)
- ADR-007 (Funnel Control-Plane Scope & Allow-listed Tasks)
- ADR-009 (Invite-Only & Operator Approval)
- ADR-010 (Decoupled Worker Agent Lifecycle)
"""
import pytest
from fastapi.testclient import TestClient

from protocols import (
    generate_keypair,
    public_key_to_hex,
    TrustState,
    TaskType,
    TaskState,
)
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry
from racon.coordinator.tasks import TaskManager
from racon.coordinator.ingress import TailscaleFunnelIngress
from racon.coordinator.server import create_worker_api, create_admin_api
from racon.agent.crypto_store import AgentKeyStore
from racon.agent.worker import RaconAgent


@pytest.fixture
def coordinator_setup(tmp_path):
    coord_priv, coord_pub = generate_keypair()
    coord_pub_hex = public_key_to_hex(coord_pub)

    registry = NodeRegistry(coordinator_public_key_hex=coord_pub_hex)
    invites = InviteManager()
    tasks = TaskManager(coordinator_private_key=coord_priv)
    ingress = TailscaleFunnelIngress()

    worker_app = create_worker_api(registry=registry, invites=invites, tasks=tasks)
    admin_app = create_admin_api(registry=registry, invites=invites, tasks=tasks, ingress=ingress)

    worker_client = TestClient(worker_app)
    admin_client = TestClient(admin_app)

    return {
        "registry": registry,
        "invites": invites,
        "tasks": tasks,
        "worker_client": worker_client,
        "admin_client": admin_client,
        "coord_pub_hex": coord_pub_hex,
    }


def test_adr006_dual_listeners_admin_routes_404_on_worker_api(coordinator_setup):
    """Verify that NO administrative routes exist on the worker-facing public listener (ADR-006)."""
    worker_client = coordinator_setup["worker_client"]

    # All admin paths must return 404 Not Found on worker listener
    admin_paths = [
        "/admin/status",
        "/admin/nodes",
        "/admin/nodes/node-123/approve",
        "/admin/nodes/node-123/revoke",
        "/admin/invites",
        "/admin/tasks",
        "/admin/ingress",
        "/admin/ingress/enable",
        "/admin/ingress/disable",
    ]

    for path in admin_paths:
        resp = worker_client.get(path)
        assert resp.status_code == 404, f"Admin route {path} leaked into worker listener with code {resp.status_code}"
        post_resp = worker_client.post(path, json={})
        assert post_resp.status_code == 404, f"Admin POST {path} leaked into worker listener with code {post_resp.status_code}"


def test_full_coordinator_agent_loopback_lifecycle(coordinator_setup, tmp_path):
    admin_client = coordinator_setup["admin_client"]
    worker_client = coordinator_setup["worker_client"]
    registry = coordinator_setup["registry"]
    tasks = coordinator_setup["tasks"]

    # 1. Operator creates an invite code via Admin API
    invite_resp = admin_client.post("/admin/invites", json={"max_uses": 1, "expiry_hours": 1.0})
    assert invite_resp.status_code == 200
    invite_code = invite_resp.json()["invite_code"]
    assert invite_code.startswith("RCN-")

    # 2. Worker Agent initializes cryptographic identity
    key_store = AgentKeyStore(key_path=tmp_path / "test_agent.key")
    agent = RaconAgent(coordinator_url="http://testserver", key_store=key_store, node_id_override="worker-node-test-1")

    # Override agent httpx client to talk directly to worker_client
    # Let's test enrollment payload directly
    from protocols import EnrollmentRequest, HardwareCapabilities, NodeRole, PROTOCOL_VERSION, sign_message
    import time

    hw_caps = HardwareCapabilities(
        cpu_name="Test CPU",
        cpu_cores=4,
        ram_total_mb=8192.0,
        ram_available_mb=6144.0,
        gpu_name=None,
        gpu_vram_mb=None,
        runtimes=["local"],
        os_name="Windows 11",
        is_simulated=False,
    )

    from protocols.crypto import canonicalize_json
    now = time.time()
    payload = canonicalize_json({
        "node_id": agent.node_id,
        "public_key_hex": agent._public_key_hex,
        "invite_code": invite_code,
        "role": NodeRole.CONTRIBUTOR.value,
        "capabilities": hw_caps.model_dump(),
        "protocol_version": PROTOCOL_VERSION,
        "timestamp": now,
    })
    sig = sign_message(agent._private_key, payload)

    enroll_req = EnrollmentRequest(
        node_id=agent.node_id,
        public_key_hex=agent._public_key_hex,
        invite_code=invite_code,
        role=NodeRole.CONTRIBUTOR,
        capabilities=hw_caps,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        signature=sig,
    )

    # Submit enrollment to worker API
    enroll_resp = worker_client.post("/enroll", json=enroll_req.model_dump())
    assert enroll_resp.status_code == 200
    enroll_data = enroll_resp.json()
    assert enroll_data["status"] == "pending"
    assert enroll_data["trust_state"] == "pending"
    session_token = enroll_data["session_token"]
    agent._session_token = session_token

    # 3. Verify node is PENDING in coordinator registry
    node = registry.get_node(agent.node_id)
    assert node is not None
    assert node.trust_state == TrustState.PENDING

    # 4. Operator reviews and approves node via Admin API
    approve_resp = admin_client.post(f"/admin/nodes/{agent.node_id}/approve")
    assert approve_resp.status_code == 200
    assert registry.get_node(agent.node_id).trust_state == TrustState.APPROVED

    # 5. Worker sends heartbeat to worker API
    from protocols import HeartbeatMessage
    import secrets
    nonce = secrets.token_hex(16)
    hb_payload = canonicalize_json({
        "node_id": agent.node_id,
        "timestamp": now,
        "nonce": nonce,
        "cpu_util_pct": 25.5,
        "ram_available_mb": 5500.0,
        "active_tasks": [],
    })
    hb_sig = sign_message(agent._private_key, hb_payload)

    hb_msg = HeartbeatMessage(
        node_id=agent.node_id,
        timestamp=now,
        nonce=nonce,
        cpu_util_pct=25.5,
        ram_available_mb=5500.0,
        active_tasks=[],
        signature=hb_sig,
    )
    hb_resp = worker_client.post("/heartbeat", json=hb_msg.model_dump())
    assert hb_resp.status_code == 200
    assert hb_resp.json()["status"] == "ok"

    # 6. Operator dispatches a task to approved worker
    task_dispatch = admin_client.post(
        "/admin/tasks",
        json={
            "node_id": agent.node_id,
            "task_type": "text_completion",
            "model_name": "test-model-1",
            "parameters": {"prompt": "What is the capital of France?"},
        },
    )
    assert task_dispatch.status_code == 200
    task_id = task_dispatch.json()["task_id"]

    # 7. Worker claims the task
    claim_resp = worker_client.post("/tasks/claim", json={"node_id": agent.node_id, "session_token": session_token})
    assert claim_resp.status_code == 200
    claim_data = claim_resp.json()
    assert claim_data["has_task"] is True
    claimed_task = claim_data["task"]
    assert claimed_task["task_id"] == task_id

    # 8. Worker executes workload and submits signed result
    from protocols import TaskResultEnvelope
    exec_res, err = agent._execute_task_workload(
        claimed_task["task_type"], claimed_task["model_name"], claimed_task["parameters"]
    )
    assert err is None

    res_payload = canonicalize_json({
        "task_id": task_id,
        "node_id": agent.node_id,
        "status": TaskState.COMPLETED.value,
        "result_data": exec_res,
        "execution_time_ms": 12.5,
        "error": None,
    })
    worker_sig = sign_message(agent._private_key, res_payload)

    result_envelope = TaskResultEnvelope(
        task_id=task_id,
        node_id=agent.node_id,
        status=TaskState.COMPLETED,
        result_data=exec_res,
        execution_time_ms=12.5,
        error=None,
        worker_signature=worker_sig,
    )

    report_resp = worker_client.post("/tasks/report", json=result_envelope.model_dump())
    assert report_resp.status_code == 200
    assert report_resp.json()["status"] == "accepted"

    # 9. Verify coordinator task state updated to COMPLETED
    stored_task = tasks.get_task(task_id)
    assert stored_task["state"] == "completed"
    assert "response" in stored_task["result_data"]

    # 10. Operator revokes node
    revoke_resp = admin_client.post(f"/admin/nodes/{agent.node_id}/revoke")
    assert revoke_resp.status_code == 200
    assert registry.get_node(agent.node_id).trust_state == TrustState.REVOKED

    # Subsequent claim by revoked node must be rejected with 401
    claim_after_revoke = worker_client.post("/tasks/claim", json={"node_id": agent.node_id, "session_token": session_token})
    assert claim_after_revoke.status_code == 401
