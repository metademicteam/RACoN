"""Coordinator Node Registry & Trust State Machine.

Tracks worker nodes, cryptographic public keys, capabilities, heartbeats,
and enforces operator trust approval (ADR-009).
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from protocols import (
    TrustState,
    NodeRole,
    HardwareCapabilities,
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatMessage,
    HeartbeatAck,
    verify_signature,
    load_public_key_from_hex,
)

logger = logging.getLogger(__name__)


class NodeRecord(BaseModel):
    node_id: str
    public_key_hex: str
    role: NodeRole
    trust_state: TrustState = TrustState.PENDING
    capabilities: HardwareCapabilities
    registered_at: float = Field(default_factory=time.time)
    last_heartbeat: float = Field(default_factory=time.time)
    session_token: Optional[str] = None
    session_token_expiry: Optional[float] = None
    cpu_util_pct: float = 0.0
    ram_available_mb: float = 0.0
    active_tasks: List[str] = Field(default_factory=list)


class NodeRegistry:
    def __init__(self, coordinator_public_key_hex: str) -> None:
        self.coordinator_public_key_hex = coordinator_public_key_hex
        self._lock = threading.Lock()
        self._nodes: Dict[str, NodeRecord] = {}  # node_id -> NodeRecord

    def register_node(
        self,
        request: EnrollmentRequest,
        is_invite_valid: bool,
        auto_approve: bool = False,
    ) -> EnrollmentResponse:
        """Process an enrollment request. Verifies worker signature and assigns trust state."""
        # 1. Verify worker Ed25519 signature
        try:
            worker_pub = load_public_key_from_hex(request.public_key_hex)
            if not verify_signature(worker_pub, request.payload_bytes(), request.signature):
                return EnrollmentResponse(
                    status="rejected",
                    node_id=request.node_id,
                    trust_state=TrustState.REVOKED,
                    coordinator_public_key=self.coordinator_public_key_hex,
                    message="Cryptographic signature verification failed.",
                )
        except Exception as e:
            return EnrollmentResponse(
                status="rejected",
                node_id=request.node_id,
                trust_state=TrustState.REVOKED,
                coordinator_public_key=self.coordinator_public_key_hex,
                message=f"Invalid public key format: {e}",
            )

        # 2. Verify invite code validity
        if not is_invite_valid:
            return EnrollmentResponse(
                status="rejected",
                node_id=request.node_id,
                trust_state=TrustState.REVOKED,
                coordinator_public_key=self.coordinator_public_key_hex,
                message="Invite code is invalid, expired, or already consumed.",
            )

        # 3. Determine initial trust state
        trust_state = TrustState.APPROVED if auto_approve else TrustState.PENDING
        token = secrets.token_urlsafe(32)
        token_expiry = time.time() + (15 * 60)  # 15 minutes session token

        record = NodeRecord(
            node_id=request.node_id,
            public_key_hex=request.public_key_hex,
            role=request.role,
            trust_state=trust_state,
            capabilities=request.capabilities,
            registered_at=time.time(),
            last_heartbeat=time.time(),
            session_token=token,
            session_token_expiry=token_expiry,
            ram_available_mb=request.capabilities.ram_available_mb,
        )

        with self._lock:
            self._nodes[request.node_id] = record

        msg = (
            "Enrollment accepted. Node is approved for task execution."
            if trust_state == TrustState.APPROVED
            else "Enrollment received. Node is pending operator approval."
        )

        return EnrollmentResponse(
            status="approved" if trust_state == TrustState.APPROVED else "pending",
            node_id=request.node_id,
            trust_state=trust_state,
            session_token=token,
            token_expiry=token_expiry,
            coordinator_public_key=self.coordinator_public_key_hex,
            message=msg,
        )

    def record_heartbeat(self, message: HeartbeatMessage) -> HeartbeatAck:
        """Process periodic heartbeat from an enrolled worker."""
        with self._lock:
            record = self._nodes.get(message.node_id)
            if not record:
                return HeartbeatAck(node_id=message.node_id, status="error", commands=["re_enroll"])

            if record.trust_state == TrustState.REVOKED:
                return HeartbeatAck(node_id=message.node_id, status="revoked", commands=["terminate"])

            # Verify signature
            try:
                worker_pub = load_public_key_from_hex(record.public_key_hex)
                if not verify_signature(worker_pub, message.payload_bytes(), message.signature):
                    return HeartbeatAck(node_id=message.node_id, status="error", commands=["auth_failure"])
            except Exception:
                return HeartbeatAck(node_id=message.node_id, status="error", commands=["auth_failure"])

            # Update telemetry & freshness
            record.last_heartbeat = time.time()
            record.cpu_util_pct = message.cpu_util_pct
            record.ram_available_mb = message.ram_available_mb
            record.active_tasks = message.active_tasks
            if record.trust_state == TrustState.OFFLINE:
                record.trust_state = TrustState.APPROVED

            return HeartbeatAck(node_id=message.node_id, status="ok", commands=[])

    def approve_node(self, node_id: str) -> bool:
        """Approve a pending node for task participation."""
        with self._lock:
            record = self._nodes.get(node_id)
            if record and record.trust_state in (TrustState.PENDING, TrustState.RESTRICTED):
                record.trust_state = TrustState.APPROVED
                logger.info("Node %s approved by operator", node_id)
                return True
            return False

    def revoke_node(self, node_id: str) -> bool:
        """Revoke a node immediately."""
        with self._lock:
            record = self._nodes.get(node_id)
            if record:
                record.trust_state = TrustState.REVOKED
                record.session_token = None
                logger.warning("Node %s revoked by operator", node_id)
                return True
            return False

    def check_stale_heartbeats(self, timeout_s: float = 30.0) -> list[str]:
        """Detect nodes with stale heartbeats and mark them OFFLINE."""
        now = time.time()
        stale_nodes = []
        with self._lock:
            for node_id, record in self._nodes.items():
                if record.trust_state == TrustState.APPROVED:
                    if (now - record.last_heartbeat) > timeout_s:
                        record.trust_state = TrustState.OFFLINE
                        stale_nodes.append(node_id)
        return stale_nodes

    def authenticate_session(self, node_id: str, token: str) -> bool:
        """Verify that a node has a valid, non-expired session token and is approved."""
        with self._lock:
            record = self._nodes.get(node_id)
            if not record:
                return False
            if record.trust_state != TrustState.APPROVED:
                return False
            if not record.session_token or record.session_token != token:
                return False
            if record.session_token_expiry and time.time() > record.session_token_expiry:
                return False
            return True

    def get_node(self, node_id: str) -> Optional[NodeRecord]:
        with self._lock:
            record = self._nodes.get(node_id)
            return record.model_copy() if record else None

    def list_nodes(self) -> List[NodeRecord]:
        with self._lock:
            return [rec.model_copy() for rec in self._nodes.values()]
