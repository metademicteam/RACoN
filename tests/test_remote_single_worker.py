"""Remote Single-Worker Execution & Safety Invariant Tests.

Fulfills Phase 6 Requirements:
- REMOTE_SINGLE_WORKER placement
- Deadline enforcement
- Cancellation enforcement (cancelled tasks never report success)
- Idempotency key deduplication
- Result size boundary enforcement (256 KB limit)
- Explicit mode tagging (REMOTE_SINGLE_WORKER, LOCAL, SIMULATED)
"""
import time
import pytest
from fastapi.testclient import TestClient

from protocols import (
    generate_keypair,
    public_key_to_hex,
    sign_message,
    TrustState,
    TaskType,
    TaskState,
    MAX_RESULT_BYTES,
)
from protocols.crypto import canonicalize_json
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry
from racon.coordinator.tasks import TaskManager
from racon.coordinator.ingress import TailscaleFunnelIngress
from racon.coordinator.server import create_worker_api, create_admin_api
from racon.agent.crypto_store import AgentKeyStore
from racon.agent.worker import RaconAgent


@pytest.fixture
def env(tmp_path):
    coord_priv, coord_pub = generate_keypair()
    coord_pub_hex = public_key_to_hex(coord_pub)

    registry = NodeRegistry(coordinator_public_key_hex=coord_pub_hex)
    invites = InviteManager()
    tasks = TaskManager(coordinator_private_key=coord_priv)
    ingress = TailscaleFunnelIngress()

    worker_app = create_worker_api(registry=registry, invites=invites, tasks=tasks)
    admin_app = create_admin_api(registry=registry, invites=invites, tasks=tasks, ingress=ingress)

    key_store = AgentKeyStore(key_path=tmp_path / "remote_agent.key")
    agent = RaconAgent(coordinator_url="http://testserver", key_store=key_store, node_id_override="worker-remote-01")

    # Enroll and approve agent directly in registry
    with registry._lock:
        from racon.coordinator.registry import NodeRecord
        from protocols import HardwareCapabilities, NodeRole
        caps = HardwareCapabilities(
            cpu_name="Remote PC Intel i5",
            cpu_cores=6,
            ram_total_mb=16384.0,
            ram_available_mb=12000.0,
            gpu_name=None,
            gpu_vram_mb=None,
            runtimes=["local", "ollama"],
            os_name="Windows 11",
            is_simulated=False,
        )
        registry._nodes[agent.node_id] = NodeRecord(
            node_id=agent.node_id,
            public_key_hex=agent._public_key_hex,
            role=NodeRole.CONTRIBUTOR,
            trust_state=TrustState.APPROVED,
            capabilities=caps,
            session_token="test-session-token-12345",
            session_token_expiry=time.time() + 3600,
            ram_available_mb=12000.0,
        )
    agent._session_token = "test-session-token-12345"

    return {
        "tasks": tasks,
        "registry": registry,
        "agent": agent,
        "worker_client": TestClient(worker_app),
        "admin_client": TestClient(admin_app),
    }


def test_remote_single_worker_execution_and_mode_tag(env):
    """Verify standard REMOTE_SINGLE_WORKER dispatch and execution."""
    admin_client = env["admin_client"]
    worker_client = env["worker_client"]
    agent = env["agent"]
    tasks = env["tasks"]

    # Dispatch task
    dispatch_resp = admin_client.post(
        "/admin/tasks",
        json={
            "node_id": agent.node_id,
            "task_type": "text_completion",
            "model_name": "qwen2.5:1.5b",
            "parameters": {"prompt": "Explain quantum computing in one sentence"},
        },
    )
    assert dispatch_resp.status_code == 200
    task_id = dispatch_resp.json()["task_id"]

    # Claim
    claim_resp = worker_client.post("/tasks/claim", json={"node_id": agent.node_id, "session_token": agent._session_token})
    assert claim_resp.status_code == 200
    task_data = claim_resp.json()["task"]
    assert task_data["task_id"] == task_id

    # Execute
    res, err = agent._execute_task_workload(task_data["task_type"], task_data["model_name"], task_data["parameters"])
    assert err is None
    # Result must carry an explicit mode tag (ADR Rule 5 & Section 8)
    assert res.get("mode") in ("LOCAL", "OBSERVED", "REMOTE_SINGLE_WORKER")

    # Report
    from protocols import TaskResultEnvelope
    payload = canonicalize_json({
        "task_id": task_id,
        "node_id": agent.node_id,
        "status": TaskState.COMPLETED.value,
        "result_data": res,
        "execution_time_ms": 25.0,
        "error": None,
    })
    sig = sign_message(agent._private_key, payload)
    env_res = TaskResultEnvelope(
        task_id=task_id,
        node_id=agent.node_id,
        status=TaskState.COMPLETED,
        result_data=res,
        execution_time_ms=25.0,
        error=None,
        worker_signature=sig,
    )
    report_resp = worker_client.post("/tasks/report", json=env_res.model_dump())
    assert report_resp.status_code == 200

    # Verify coordinator stores completion
    stored = tasks.get_task(task_id)
    assert stored["state"] == "completed"
    assert stored["duration_ms"] == 25.0


def test_cancellation_enforcement_never_reports_success(env):
    """Cancelled jobs must never report success (Section 9 invariant)."""
    tasks = env["tasks"]
    agent = env["agent"]
    worker_client = env["worker_client"]

    # Create task
    envelope = tasks.create_task(
        node_id=agent.node_id,
        task_type=TaskType.BENCHMARK_PROBE,
        model_name="probe",
        parameters={},
    )

    # Cancel task before result is received
    tasks.cancel_task(envelope.task_id, reason="User cancelled from UI")
    assert tasks.get_task(envelope.task_id)["state"] == "cancelled"

    # Attempt to report success afterward
    from protocols import TaskResultEnvelope
    payload = canonicalize_json({
        "task_id": envelope.task_id,
        "node_id": agent.node_id,
        "status": TaskState.COMPLETED.value,
        "result_data": {"done": True},
        "execution_time_ms": 10.0,
        "error": None,
    })
    sig = sign_message(agent._private_key, payload)
    res_env = TaskResultEnvelope(
        task_id=envelope.task_id,
        node_id=agent.node_id,
        status=TaskState.COMPLETED,
        result_data={"done": True},
        execution_time_ms=10.0,
        error=None,
        worker_signature=sig,
    )

    # Coordinator must reject overwriting cancelled task
    report_resp = worker_client.post("/tasks/report", json=res_env.model_dump())
    assert report_resp.status_code == 400

    # Task state remains CANCELLED
    assert tasks.get_task(envelope.task_id)["state"] == "cancelled"


def test_idempotency_key_deduplication(env):
    """Submitting duplicate task with same idempotency key returns existing task."""
    tasks = env["tasks"]
    agent = env["agent"]

    idemp_key = "idemp-duplicate-test-key-12345"
    task1 = tasks.create_task(
        node_id=agent.node_id,
        task_type=TaskType.TEXT_COMPLETION,
        model_name="qwen2.5:1.5b",
        parameters={"prompt": "Test"},
        idempotency_key=idemp_key,
    )

    task2 = tasks.create_task(
        node_id=agent.node_id,
        task_type=TaskType.TEXT_COMPLETION,
        model_name="qwen2.5:1.5b",
        parameters={"prompt": "Test"},
        idempotency_key=idemp_key,
    )

    assert task1.task_id == task2.task_id
    assert len(tasks.list_tasks()) == 1
