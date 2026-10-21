"""Contract and serialization tests for protocols package."""
import json
import pytest
import time

from protocols import (
    PROTOCOL_VERSION,
    MIN_SUPPORTED_VERSION,
    MAX_ENVELOPE_BYTES,
    NodeRole,
    TrustState,
    TaskType,
    TaskState,
    HardwareCapabilities,
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatMessage,
    HeartbeatAck,
    TaskEnvelope,
    TaskResultEnvelope,
    generate_keypair,
    public_key_to_hex,
    sign_message,
    verify_signature,
    load_public_key_from_hex,
)


def test_crypto_keypair_generation_and_signatures():
    priv, pub = generate_keypair()
    pub_hex = public_key_to_hex(pub)
    assert len(pub_hex) == 64

    # Sign message
    message = b"RACoN coordinator task verification payload"
    sig_hex = sign_message(priv, message)
    assert len(sig_hex) == 128

    # Verify signature
    assert verify_signature(pub, message, sig_hex) is True

    # Tampered message must fail
    assert verify_signature(pub, b"Tampered message", sig_hex) is False

    # Roundtrip public key reload
    pub_reloaded = load_public_key_from_hex(pub_hex)
    assert verify_signature(pub_reloaded, message, sig_hex) is True


def test_enrollment_request_serialization_and_signature():
    priv, pub = generate_keypair()
    pub_hex = public_key_to_hex(pub)

    caps = HardwareCapabilities(
        cpu_name="Intel Core i3-10100",
        cpu_cores=4,
        ram_total_mb=16384.0,
        ram_available_mb=12288.0,
        gpu_name=None,
        gpu_vram_mb=None,
        runtimes=["ollama"],
        os_name="Windows 11",
        is_simulated=False,
    )

    req_partial = {
        "node_id": "worker-node-alpha-1",
        "public_key_hex": pub_hex,
        "invite_code": "INV-TEST-001",
        "role": NodeRole.CONTRIBUTOR,
        "capabilities": caps,
        "protocol_version": PROTOCOL_VERSION,
        "timestamp": time.time(),
    }

    # Construct and sign canonical payload
    from protocols.crypto import canonicalize_json
    payload = canonicalize_json({
        "node_id": req_partial["node_id"],
        "public_key_hex": req_partial["public_key_hex"],
        "invite_code": req_partial["invite_code"],
        "role": req_partial["role"].value,
        "capabilities": req_partial["capabilities"].model_dump(),
        "protocol_version": req_partial["protocol_version"],
        "timestamp": req_partial["timestamp"],
    })

    sig_hex = sign_message(priv, payload)

    req = EnrollmentRequest(
        node_id=req_partial["node_id"],
        public_key_hex=req_partial["public_key_hex"],
        invite_code=req_partial["invite_code"],
        role=req_partial["role"],
        capabilities=req_partial["capabilities"],
        protocol_version=req_partial["protocol_version"],
        timestamp=req_partial["timestamp"],
        signature=sig_hex,
    )

    # Verification on receiver
    sender_pub = load_public_key_from_hex(req.public_key_hex)
    assert verify_signature(sender_pub, req.payload_bytes(), req.signature) is True


def test_task_envelope_signed_by_coordinator():
    coord_priv, coord_pub = generate_keypair()
    coord_pub_hex = public_key_to_hex(coord_pub)

    task_data = {
        "task_id": "task-88776655-abcd",
        "node_id": "worker-node-alpha-1",
        "task_type": TaskType.TEXT_COMPLETION,
        "model_name": "qwen2.5:1.5b",
        "parameters": {"prompt": "Hello RACoN cluster", "max_tokens": 50},
        "expiry": time.time() + 60.0,
        "idempotency_key": "idemp-key-9988776655443322",
    }

    from protocols.crypto import canonicalize_json
    payload = canonicalize_json({
        "task_id": task_data["task_id"],
        "node_id": task_data["node_id"],
        "task_type": task_data["task_type"].value,
        "model_name": task_data["model_name"],
        "parameters": task_data["parameters"],
        "data_class": "public",
        "expiry": task_data["expiry"],
        "idempotency_key": task_data["idempotency_key"],
    })

    coord_sig = sign_message(coord_priv, payload)

    envelope = TaskEnvelope(
        task_id=task_data["task_id"],
        node_id=task_data["node_id"],
        task_type=task_data["task_type"],
        model_name=task_data["model_name"],
        parameters=task_data["parameters"],
        expiry=task_data["expiry"],
        idempotency_key=task_data["idempotency_key"],
        coordinator_signature=coord_sig,
    )

    # Worker verifies
    assert verify_signature(coord_pub, envelope.payload_bytes(), envelope.coordinator_signature) is True


def test_task_size_limits():
    """Verify serialized envelope is well within the 64 KB limit."""
    coord_priv, coord_pub = generate_keypair()
    envelope = TaskEnvelope(
        task_id="task-12345678",
        node_id="worker-node-1234",
        task_type=TaskType.TEXT_COMPLETION,
        model_name="qwen2.5:1.5b",
        parameters={"prompt": "Short test prompt", "temperature": 0.7},
        expiry=time.time() + 30.0,
        idempotency_key="idemp-key-1234567890123456",
        coordinator_signature="a" * 128,
    )

    serialized = envelope.model_dump_json().encode("utf-8")
    assert len(serialized) < MAX_ENVELOPE_BYTES
