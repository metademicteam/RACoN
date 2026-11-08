"""End-to-end tests for the unified worker ingress and the admin API.

These use real Ed25519 signatures over the same canonical payloads the desktop agent
produces. They prove that a worker can enroll, heartbeat, claim a scheduler-dispatched
task and report its result without a second coordinator process.
"""
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from backend.racon.api import coordinator_state as cs
from backend.racon.api.app import app
from protocols import (
    EnrollmentRequest,
    HardwareCapabilities,
    HeartbeatMessage,
    NodeRole,
    PROTOCOL_VERSION,
    TaskResultEnvelope,
    TaskState,
)
from protocols.crypto import (
    canonicalize_json,
    generate_keypair,
    public_key_to_hex,
    sign_message,
)

TOKEN = "racon-local-dev-token"


@pytest.fixture(scope="module")
def client():
    cs.reset_coordinator_state()
    with TestClient(app, headers={"X-RACON-TOKEN": TOKEN}) as test_client:
        yield test_client
    cs.reset_coordinator_state()


def _caps() -> HardwareCapabilities:
    return HardwareCapabilities(
        cpu_name="Test Ryzen 5",
        cpu_cores=6,
        ram_total_mb=16384.0,
        ram_available_mb=8192.0,
        gpu_name="NVIDIA RTX 3060",
        gpu_vram_mb=12288.0,
        runtimes=["llama_cpp", "ollama"],
        os_name="Windows 11",
        is_simulated=False,
        rpc_endpoint="100.64.0.9:50052",
    )


def _enroll(client):
    private, public = generate_keypair()
    public_hex = public_key_to_hex(public)
    node_id = "node-" + public_hex[:12]
    caps = _caps()
    now = time.time()
    request = EnrollmentRequest(
        node_id=node_id,
        public_key_hex=public_hex,
        invite_code="RCN-TEST-JOIN",
        role=NodeRole.CONTRIBUTOR,
        capabilities=caps,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        models=["qwen2.5:0.5b", "smollm2:360m"],
        features=["text_generation"],
        signature="0" * 128,
    )
    request.signature = sign_message(private, request.payload_bytes())
    response = client.post("/enroll", json=request.model_dump())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trust_state"] == "approved"
    assert body["session_token"]
    return private, public_hex, request, body


def test_admin_dashboard_is_served(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "RACoN Admin" in response.text


def test_enroll_heartbeat_and_worker_hardware_visibility(client):
    private, _public_hex, request, _body = _enroll(client)

    now = time.time()
    nonce = "a" * 32
    heartbeat_payload = canonicalize_json(
        {
            "node_id": request.node_id,
            "timestamp": now,
            "nonce": nonce,
            "cpu_util_pct": 12.5,
            "ram_available_mb": 7000.0,
            "active_tasks": [],
        }
    )
    heartbeat = HeartbeatMessage(
        node_id=request.node_id,
        timestamp=now,
        nonce=nonce,
        cpu_util_pct=12.5,
        ram_available_mb=7000.0,
        active_tasks=[],
        signature=sign_message(private, heartbeat_payload),
    )
    response = client.post("/heartbeat", json=heartbeat.model_dump())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    nodes = client.get("/api/v1/admin/nodes").json()
    node = next(item for item in nodes if item["id"] == request.node_id)
    assert node["online"] is True
    assert node["trust_state"] == "approved"
    assert node["cpu"]["cores"] == 6
    assert node["cpu"]["name"] == "Test Ryzen 5"
    assert node["ram_total_mb"] == 16384.0
    assert node["ram_available_mb"] == 7000.0
    assert node["gpu"]["name"] == "NVIDIA RTX 3060"
    assert node["gpu"]["vram_total_mb"] == 12288.0
    assert "llama_cpp" in node["runtimes"]
    assert node["rpc_endpoint"] == "100.64.0.9:50052"
    assert node["models"] == ["qwen2.5:0.5b", "smollm2:360m"]
    assert node["features"] == ["text_generation"]

    events = client.get("/api/v1/admin/events?limit=100").json()["events"]
    kinds = {(event["event_type"], event["source"]) for event in events}
    assert ("enroll", request.node_id) in kinds
    assert ("heartbeat", request.node_id) in kinds


def test_heartbeat_refreshes_worker_model_inventory(client):
    """A model installed or removed after enrollment is reflected on the next heartbeat."""
    private, _public_hex, request, _body = _enroll(client)

    def send(models):
        heartbeat = HeartbeatMessage(
            node_id=request.node_id,
            timestamp=time.time(),
            nonce=secrets.token_hex(16),
            cpu_util_pct=15.0,
            ram_available_mb=6400.0,
            active_tasks=[],
            models=models,
            models_reported=True,
            signature="0" * 128,
        )
        heartbeat.signature = sign_message(private, heartbeat.payload_bytes())
        response = client.post("/heartbeat", json=heartbeat.model_dump())
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"

    send(["llama3.2:1b", "qwen2.5:1.5b"])
    admin_node = next(
        item
        for item in client.get("/api/v1/admin/nodes").json()
        if item["id"] == request.node_id
    )
    assert admin_node["models"] == ["llama3.2:1b", "qwen2.5:1.5b"]

    client_node = next(
        item
        for item in client.get("/api/v1/nodes").json()
        if item["id"] == request.node_id
    )
    assert client_node["capabilities"]["models"] == ["llama3.2:1b", "qwen2.5:1.5b"]

    send([])
    admin_node = next(
        item
        for item in client.get("/api/v1/admin/nodes").json()
        if item["id"] == request.node_id
    )
    assert admin_node["models"] == []

    client_node = next(
        item
        for item in client.get("/api/v1/nodes").json()
        if item["id"] == request.node_id
    )
    assert client_node["capabilities"]["models"] == []


def test_heartbeat_renews_an_expired_worker_session(client):
    """A live worker must not keep showing online while its session token is dead."""
    private, _public_hex, request, body = _enroll(client)
    token = body["session_token"]
    state = cs.get_coordinator_state()
    state.registry._nodes[request.node_id].session_token_expiry = time.time() - 1.0
    assert state.registry.authenticate_session(request.node_id, token) is False

    now = time.time()
    nonce = "b" * 32
    heartbeat_payload = canonicalize_json(
        {
            "node_id": request.node_id,
            "timestamp": now,
            "nonce": nonce,
            "cpu_util_pct": 20.0,
            "ram_available_mb": 6500.0,
            "active_tasks": [],
        }
    )
    heartbeat = HeartbeatMessage(
        node_id=request.node_id,
        timestamp=now,
        nonce=nonce,
        cpu_util_pct=20.0,
        ram_available_mb=6500.0,
        active_tasks=[],
        signature=sign_message(private, heartbeat_payload),
    )
    response = client.post("/heartbeat", json=heartbeat.model_dump())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert state.registry.authenticate_session(request.node_id, token) is True

    dispatch = client.post(
        "/api/v1/admin/tasks/dispatch",
        json={
            "node_id": request.node_id,
            "model_name": "qwen2.5:0.5b",
            "prompt": "Reply with pong.",
            "max_tokens": 8,
        },
    )
    assert dispatch.status_code == 200, dispatch.text
    claim = client.post(
        "/tasks/claim",
        json={"node_id": request.node_id, "session_token": token},
    )
    assert claim.status_code == 200
    assert claim.json()["has_task"] is True


def test_dispatch_claim_and_report(client):
    private, _public_hex, request, body = _enroll(client)
    session_token = body["session_token"]

    dispatch = client.post(
        "/api/v1/admin/tasks/dispatch",
        json={
            "node_id": request.node_id,
            "model_name": "qwen2.5:0.5b",
            "prompt": "Reply with pong.",
            "max_tokens": 8,
        },
    )
    assert dispatch.status_code == 200, dispatch.text
    task_id = dispatch.json()["task_id"]

    claim = client.post(
        "/tasks/claim",
        json={"node_id": request.node_id, "session_token": session_token},
    )
    assert claim.status_code == 200
    claimed = claim.json()
    assert claimed["has_task"] is True
    assert claimed["task"]["task_id"] == task_id

    result_payload = canonicalize_json(
        {
            "task_id": task_id,
            "node_id": request.node_id,
            "status": TaskState.COMPLETED.value,
            "result_data": {"response": "pong"},
            "execution_time_ms": 12.0,
            "error": None,
        }
    )
    result = TaskResultEnvelope(
        task_id=task_id,
        node_id=request.node_id,
        status=TaskState.COMPLETED,
        result_data={"response": "pong"},
        execution_time_ms=12.0,
        error=None,
        worker_signature=sign_message(private, result_payload),
    )
    report = client.post("/tasks/report", json=result.model_dump())
    assert report.status_code == 200, report.text
    assert report.json()["status"] == "accepted"

    events = client.get("/api/v1/admin/events?limit=200").json()["events"]
    kinds = {event["event_type"] for event in events}
    assert {"task_dispatched", "task_claimed", "task_reported"}.issubset(kinds)


def test_re_enrollment_keeps_approval_and_revocation_is_final(client):
    _private, _public, request, _body = _enroll(client)

    again = client.post("/enroll", json=request.model_dump())
    assert again.status_code == 200
    assert again.json()["trust_state"] == "approved"

    revoke = client.post(f"/api/v1/admin/workers/{request.node_id}/revoke")
    assert revoke.status_code == 200

    rejected = client.post("/enroll", json=request.model_dump())
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["trust_state"] == "revoked"


def test_diagnostics_returns_structured_checks(client):
    response = client.get("/api/v1/admin/diagnostics")
    assert response.status_code == 200, response.text
    data = response.json()
    names = {check["name"] for check in data["checks"]}
    assert {
        "database",
        "coordinator",
        "inference_runtime",
        "ollama",
        "llama_cpp",
        "models",
        "workers",
        "routing_log",
        "tailscale",
        "auth",
    }.issubset(names)
    assert data["status"] in ("ok", "degraded", "failed")
    database = next(check for check in data["checks"] if check["name"] == "database")
    assert database["status"] == "ok"


def test_admin_api_requires_token():
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/admin/overview").status_code == 401
        assert anonymous.get("/api/v1/admin/nodes").status_code == 401
        assert anonymous.get("/api/v1/admin/diagnostics").status_code == 401
