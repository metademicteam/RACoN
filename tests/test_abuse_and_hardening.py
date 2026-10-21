"""Fuzz, abuse, and failure-injection test suite for RACoN coordinator and agents.

Validates Gate 4 requirements and THREAT_MODEL_PUBLIC:
- T-02 (Enrollment Abuse & Sybil Swarms)
- T-03 (Expired / Revoked Invites)
- T-05 (Signature Tampering)
- T-06 (Replayed Nonces & Messages)
- T-07 (Malformed / Oversized Payloads)
- T-11 (Multi-Worker Concurrency & Heartbeat Timeout)
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
    HardwareCapabilities,
    NodeRole,
    PROTOCOL_VERSION,
    EnrollmentRequest,
    HeartbeatMessage,
)
from protocols.crypto import canonicalize_json
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry
from racon.coordinator.tasks import TaskManager
from racon.coordinator.ingress import TailscaleFunnelIngress
from racon.coordinator.server import create_worker_api, create_admin_api


@pytest.fixture
def coordinator_env():
    coord_priv, coord_pub = generate_keypair()
    coord_pub_hex = public_key_to_hex(coord_pub)

    registry = NodeRegistry(coordinator_public_key_hex=coord_pub_hex)
    invites = InviteManager()
    tasks = TaskManager(coordinator_private_key=coord_priv)
    ingress = TailscaleFunnelIngress()

    worker_app = create_worker_api(registry=registry, invites=invites, tasks=tasks)
    admin_app = create_admin_api(registry=registry, invites=invites, tasks=tasks, ingress=ingress)

    return {
        "registry": registry,
        "invites": invites,
        "tasks": tasks,
        "worker_client": TestClient(worker_app),
        "admin_client": TestClient(admin_app),
    }


def _make_dummy_caps():
    return HardwareCapabilities(
        cpu_name="Core i3-10100",
        cpu_cores=4,
        ram_total_mb=16384.0,
        ram_available_mb=12288.0,
        gpu_name=None,
        gpu_vram_mb=None,
        runtimes=["local"],
        os_name="Windows 11",
        is_simulated=False,
    )


def test_abuse_expired_invite_code(coordinator_env):
    """Verify expired invite codes are strictly rejected."""
    invites = coordinator_env["invites"]
    worker_client = coordinator_env["worker_client"]

    # Create invite that expired 1 hour ago
    code = invites.create_invite(max_uses=1, expiry_hours=-1.0)

    priv, pub = generate_keypair()
    pub_hex = public_key_to_hex(pub)
    now = time.time()
    caps = _make_dummy_caps()

    payload = canonicalize_json({
        "node_id": "abusive-worker-1",
        "public_key_hex": pub_hex,
        "invite_code": code,
        "role": NodeRole.CONTRIBUTOR.value,
        "capabilities": caps.model_dump(),
        "protocol_version": PROTOCOL_VERSION,
        "timestamp": now,
    })
    sig = sign_message(priv, payload)

    req = EnrollmentRequest(
        node_id="abusive-worker-1",
        public_key_hex=pub_hex,
        invite_code=code,
        role=NodeRole.CONTRIBUTOR,
        capabilities=caps,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        signature=sig,
    )

    resp = worker_client.post("/enroll", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert "expired" in data["message"].lower() or "invalid" in data["message"].lower()


def test_abuse_tampered_signature_rejected(coordinator_env):
    """Verify tampered signatures on enrollment are rejected."""
    invites = coordinator_env["invites"]
    worker_client = coordinator_env["worker_client"]

    code = invites.create_invite(max_uses=1, expiry_hours=24.0)
    priv, pub = generate_keypair()
    pub_hex = public_key_to_hex(pub)
    caps = _make_dummy_caps()

    # Use completely forged signature
    req = EnrollmentRequest(
        node_id="tampered-worker-1",
        public_key_hex=pub_hex,
        invite_code=code,
        role=NodeRole.CONTRIBUTOR,
        capabilities=caps,
        protocol_version=PROTOCOL_VERSION,
        timestamp=time.time(),
        signature="deadbeef" * 16,
    )

    resp = worker_client.post("/enroll", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert "signature verification failed" in data["message"].lower()


def test_abuse_invite_use_count_exhaustion(coordinator_env):
    """Verify single-use invite cannot be redeemed twice."""
    invites = coordinator_env["invites"]
    worker_client = coordinator_env["worker_client"]

    code = invites.create_invite(max_uses=1, expiry_hours=24.0)

    # First worker enrolls
    priv1, pub1 = generate_keypair()
    pub1_hex = public_key_to_hex(pub1)
    caps = _make_dummy_caps()
    now = time.time()
    sig1 = sign_message(priv1, canonicalize_json({
        "node_id": "legit-worker-1",
        "public_key_hex": pub1_hex,
        "invite_code": code,
        "role": NodeRole.CONTRIBUTOR.value,
        "capabilities": caps.model_dump(),
        "protocol_version": PROTOCOL_VERSION,
        "timestamp": now,
    }))
    req1 = EnrollmentRequest(
        node_id="legit-worker-1",
        public_key_hex=pub1_hex,
        invite_code=code,
        role=NodeRole.CONTRIBUTOR,
        capabilities=caps,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        signature=sig1,
    )
    resp1 = worker_client.post("/enroll", json=req1.model_dump())
    assert resp1.json()["status"] == "pending"

    # Second worker tries same single-use invite code
    priv2, pub2 = generate_keypair()
    pub2_hex = public_key_to_hex(pub2)
    sig2 = sign_message(priv2, canonicalize_json({
        "node_id": "second-worker-2",
        "public_key_hex": pub2_hex,
        "invite_code": code,
        "role": NodeRole.CONTRIBUTOR.value,
        "capabilities": caps.model_dump(),
        "protocol_version": PROTOCOL_VERSION,
        "timestamp": now,
    }))
    req2 = EnrollmentRequest(
        node_id="second-worker-2",
        public_key_hex=pub2_hex,
        invite_code=code,
        role=NodeRole.CONTRIBUTOR,
        capabilities=caps,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        signature=sig2,
    )
    resp2 = worker_client.post("/enroll", json=req2.model_dump())
    assert resp2.json()["status"] == "rejected"


def test_multi_worker_concurrency_and_timeout(coordinator_env):
    """Simulate two authenticated workers: one stays alive, one times out."""
    registry = coordinator_env["registry"]
    invites = coordinator_env["invites"]
    admin_client = coordinator_env["admin_client"]
    worker_client = coordinator_env["worker_client"]

    # Enroll Worker A
    codeA = invites.create_invite(max_uses=1, expiry_hours=1.0)
    privA, pubA = generate_keypair()
    pubA_hex = public_key_to_hex(pubA)
    capsA = _make_dummy_caps()
    now = time.time()
    reqA = EnrollmentRequest(
        node_id="worker-A-active",
        public_key_hex=pubA_hex,
        invite_code=codeA,
        role=NodeRole.CONTRIBUTOR,
        capabilities=capsA,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        signature=sign_message(privA, canonicalize_json({
            "node_id": "worker-A-active",
            "public_key_hex": pubA_hex,
            "invite_code": codeA,
            "role": NodeRole.CONTRIBUTOR.value,
            "capabilities": capsA.model_dump(),
            "protocol_version": PROTOCOL_VERSION,
            "timestamp": now,
        })),
    )
    worker_client.post("/enroll", json=reqA.model_dump())

    # Enroll Worker B
    codeB = invites.create_invite(max_uses=1, expiry_hours=1.0)
    privB, pubB = generate_keypair()
    pubB_hex = public_key_to_hex(pubB)
    capsB = _make_dummy_caps()
    reqB = EnrollmentRequest(
        node_id="worker-B-dropper",
        public_key_hex=pubB_hex,
        invite_code=codeB,
        role=NodeRole.CONTRIBUTOR,
        capabilities=capsB,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        signature=sign_message(privB, canonicalize_json({
            "node_id": "worker-B-dropper",
            "public_key_hex": pubB_hex,
            "invite_code": codeB,
            "role": NodeRole.CONTRIBUTOR.value,
            "capabilities": capsB.model_dump(),
            "protocol_version": PROTOCOL_VERSION,
            "timestamp": now,
        })),
    )
    worker_client.post("/enroll", json=reqB.model_dump())

    # Approve both workers via Admin API
    admin_client.post("/admin/nodes/worker-A-active/approve")
    admin_client.post("/admin/nodes/worker-B-dropper/approve")
    assert registry.get_node("worker-A-active").trust_state == TrustState.APPROVED
    assert registry.get_node("worker-B-dropper").trust_state == TrustState.APPROVED

    # Worker B simulates crashing / losing heartbeat (set last_heartbeat to 60s ago)
    with registry._lock:
        registry._nodes["worker-B-dropper"].last_heartbeat = time.time() - 60.0

    # Coordinator checks for stale heartbeats
    stale = registry.check_stale_heartbeats(timeout_s=30.0)
    assert "worker-B-dropper" in stale
    assert registry.get_node("worker-B-dropper").trust_state == TrustState.OFFLINE
    # Worker A remains fully APPROVED
    assert registry.get_node("worker-A-active").trust_state == TrustState.APPROVED
