"""Typed versioned protocol schemas for RACoN coordinator-worker communication.

Enforces strict size limits, field boundaries, and cryptographic verification
per RACoN Detailed Architecture v1.0 and Production Master Plan.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

PROTOCOL_VERSION = "1.0.0"
MIN_SUPPORTED_VERSION = "1.0.0"

# Size limits (in bytes)
MAX_ENVELOPE_BYTES = 65536     # 64 KB for control messages
MAX_RESULT_BYTES = 262144      # 256 KB for task results
MAX_HEARTBEAT_BYTES = 8192     # 8 KB for heartbeats
MAX_STRING_FIELD_LENGTH = 4096


class NodeRole(str, Enum):
    CONTRIBUTOR = "contributor"
    REQUESTER = "requester"
    BOTH = "both"


class TrustState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    RESTRICTED = "restricted"
    REVOKED = "revoked"
    OFFLINE = "offline"


class TaskType(str, Enum):
    TEXT_COMPLETION = "text_completion"
    EMBEDDING = "embedding"
    BENCHMARK_PROBE = "benchmark_probe"
    DISTRIBUTED_INFERENCE = "distributed_inference"
    SHARD_VERIFICATION = "shard_verification"


class TaskState(str, Enum):
    QUEUED = "queued"
    VALIDATING = "validating"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DataClass(str, Enum):
    PUBLIC = "public"


class HardwareCapabilities(BaseModel):
    cpu_name: str = Field(..., max_length=256)
    cpu_cores: int = Field(..., ge=1, le=512)
    ram_total_mb: float = Field(..., gt=0)
    ram_available_mb: float = Field(..., ge=0)
    gpu_name: Optional[str] = Field(None, max_length=256)
    gpu_vram_mb: Optional[float] = Field(None, ge=0)
    runtimes: List[str] = Field(default_factory=list, max_length=16)
    os_name: str = Field(..., max_length=128)
    is_simulated: bool = False
    rpc_endpoint: Optional[str] = Field(None, max_length=128)
    assigned_shards: List[str] = Field(default_factory=list, max_length=64)


class EnrollmentRequest(BaseModel):
    node_id: str = Field(..., min_length=8, max_length=64)
    public_key_hex: str = Field(..., min_length=64, max_length=64)
    invite_code: str = Field(..., min_length=6, max_length=64)
    role: NodeRole = NodeRole.CONTRIBUTOR
    capabilities: HardwareCapabilities
    protocol_version: str = Field(default=PROTOCOL_VERSION, max_length=16)
    timestamp: float = Field(default_factory=time.time)
    signature: str = Field(..., min_length=128, max_length=128)

    def payload_bytes(self) -> bytes:
        from protocols.crypto import canonicalize_json
        data = {
            "node_id": self.node_id,
            "public_key_hex": self.public_key_hex,
            "invite_code": self.invite_code,
            "role": self.role.value,
            "capabilities": self.capabilities.model_dump(),
            "protocol_version": self.protocol_version,
            "timestamp": self.timestamp,
        }
        return canonicalize_json(data)


class EnrollmentResponse(BaseModel):
    status: str = Field(..., max_length=32)  # "approved", "pending", "rejected"
    node_id: str
    trust_state: TrustState
    session_token: Optional[str] = Field(None, max_length=256)
    token_expiry: Optional[float] = None
    coordinator_public_key: str = Field(..., min_length=64, max_length=64)
    message: str = Field(..., max_length=512)
    # Issued once at enrollment so the node can call the public gateway without manual setup.
    gateway_key: Optional[str] = Field(None, max_length=128)


class HeartbeatMessage(BaseModel):
    node_id: str = Field(..., min_length=8, max_length=64)
    timestamp: float = Field(default_factory=time.time)
    nonce: str = Field(..., min_length=16, max_length=64)
    cpu_util_pct: float = Field(..., ge=0.0, le=100.0)
    ram_available_mb: float = Field(..., ge=0.0)
    active_tasks: List[str] = Field(default_factory=list, max_length=64)
    signature: str = Field(..., min_length=128, max_length=128)

    def payload_bytes(self) -> bytes:
        from protocols.crypto import canonicalize_json
        data = {
            "node_id": self.node_id,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "cpu_util_pct": self.cpu_util_pct,
            "ram_available_mb": self.ram_available_mb,
            "active_tasks": sorted(self.active_tasks),
        }
        return canonicalize_json(data)


class HeartbeatAck(BaseModel):
    node_id: str
    coordinator_time: float = Field(default_factory=time.time)
    status: str = "ok"
    commands: List[str] = Field(default_factory=list, max_length=16)


class TaskEnvelope(BaseModel):
    task_id: str = Field(..., min_length=8, max_length=64)
    node_id: str = Field(..., min_length=8, max_length=64)
    task_type: TaskType
    model_name: str = Field(..., max_length=128)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    data_class: DataClass = DataClass.PUBLIC
    expiry: float
    idempotency_key: str = Field(..., min_length=16, max_length=64)
    coordinator_signature: str = Field(..., min_length=128, max_length=128)

    def payload_bytes(self) -> bytes:
        from protocols.crypto import canonicalize_json
        data = {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "task_type": self.task_type.value,
            "model_name": self.model_name,
            "parameters": self.parameters,
            "data_class": self.data_class.value,
            "expiry": self.expiry,
            "idempotency_key": self.idempotency_key,
        }
        return canonicalize_json(data)


class TaskResultEnvelope(BaseModel):
    task_id: str = Field(..., min_length=8, max_length=64)
    node_id: str = Field(..., min_length=8, max_length=64)
    status: TaskState
    result_data: Dict[str, Any] = Field(default_factory=dict)
    execution_time_ms: float = Field(..., ge=0.0)
    error: Optional[str] = Field(None, max_length=1024)
    worker_signature: str = Field(..., min_length=128, max_length=128)

    def payload_bytes(self) -> bytes:
        from protocols.crypto import canonicalize_json
        data = {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "status": self.status.value,
            "result_data": self.result_data,
            "execution_time_ms": self.execution_time_ms,
            "error": self.error,
        }
        return canonicalize_json(data)


class TaskCancelMessage(BaseModel):
    task_id: str = Field(..., min_length=8, max_length=64)
    node_id: str = Field(..., min_length=8, max_length=64)
    reason: str = Field(..., max_length=256)
    timestamp: float = Field(default_factory=time.time)
    coordinator_signature: str = Field(..., min_length=128, max_length=128)

    def payload_bytes(self) -> bytes:
        from protocols.crypto import canonicalize_json
        data = {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }
        return canonicalize_json(data)
