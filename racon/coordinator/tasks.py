"""Coordinator Task Dispatcher & State Machine.

Signs task envelopes using the coordinator's Ed25519 private key.
Verifies worker signatures on returned task results (ADR-007, Section 7).
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional
from cryptography.hazmat.primitives.asymmetric import ed25519

from protocols import (
    TaskType,
    TaskState,
    DataClass,
    TaskEnvelope,
    TaskResultEnvelope,
    TaskCancelMessage,
    sign_message,
    verify_signature,
    load_public_key_from_hex,
)

logger = logging.getLogger(__name__)


class TaskRecord:
    def __init__(self, envelope: TaskEnvelope) -> None:
        self.envelope = envelope
        self.state: TaskState = TaskState.ASSIGNED
        self.created_at: float = time.time()
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.result_data: Optional[Dict[str, Any]] = None
        self.duration_ms: float = 0.0
        self.error: Optional[str] = None
        self.worker_signature: Optional[str] = None


class TaskManager:
    def __init__(self, coordinator_private_key: ed25519.Ed25519PrivateKey) -> None:
        self._private_key = coordinator_private_key
        self._public_key = coordinator_private_key.public_key()
        self._lock = threading.Lock()
        self._tasks: Dict[str, TaskRecord] = {}  # task_id -> TaskRecord
        self._idempotency_cache: Dict[str, str] = {}  # idempotency_key -> task_id

    def create_task(
        self,
        node_id: str,
        task_type: TaskType,
        model_name: str,
        parameters: Dict[str, Any],
        idempotency_key: Optional[str] = None,
        expiry_seconds: float = 60.0,
    ) -> TaskEnvelope:
        """Create and cryptographically sign a task envelope assigned to a specific node."""
        if not idempotency_key:
            idempotency_key = secrets.token_hex(16)

        with self._lock:
            if idempotency_key in self._idempotency_cache:
                existing_task_id = self._idempotency_cache[idempotency_key]
                return self._tasks[existing_task_id].envelope

        task_id = f"task-{secrets.token_hex(8)}"
        expiry = time.time() + expiry_seconds

        # Construct canonical payload for coordinator signature
        from protocols.crypto import canonicalize_json
        payload = canonicalize_json({
            "task_id": task_id,
            "node_id": node_id,
            "task_type": task_type.value,
            "model_name": model_name,
            "parameters": parameters,
            "data_class": DataClass.PUBLIC.value,
            "expiry": expiry,
            "idempotency_key": idempotency_key,
        })

        sig_hex = sign_message(self._private_key, payload)

        envelope = TaskEnvelope(
            task_id=task_id,
            node_id=node_id,
            task_type=task_type,
            model_name=model_name,
            parameters=parameters,
            data_class=DataClass.PUBLIC,
            expiry=expiry,
            idempotency_key=idempotency_key,
            coordinator_signature=sig_hex,
        )

        record = TaskRecord(envelope)
        with self._lock:
            self._tasks[task_id] = record
            self._idempotency_cache[idempotency_key] = task_id

        logger.info("Created signed task %s for worker %s (type=%s)", task_id, node_id, task_type.value)
        return envelope

    def record_result(self, result: TaskResultEnvelope, worker_public_key_hex: str) -> bool:
        """Record completed task results after verifying worker signature."""
        # 1. Verify worker signature
        try:
            worker_pub = load_public_key_from_hex(worker_public_key_hex)
            if not verify_signature(worker_pub, result.payload_bytes(), result.worker_signature):
                logger.error("Task result signature verification failed for task %s", result.task_id)
                return False
        except Exception as e:
            logger.error("Failed to parse worker public key: %s", e)
            return False

        with self._lock:
            record = self._tasks.get(result.task_id)
            if not record:
                logger.warning("Received result for unknown task %s", result.task_id)
                return False

            if record.state in (TaskState.COMPLETED, TaskState.CANCELLED):
                logger.warning("Task %s already in terminal state %s", result.task_id, record.state.value)
                return False

            record.state = result.status
            record.completed_at = time.time()
            record.result_data = result.result_data
            record.duration_ms = result.execution_time_ms
            record.error = result.error
            record.worker_signature = result.worker_signature

        logger.info("Recorded task %s status=%s in %.2f ms", result.task_id, result.status.value, result.execution_time_ms)
        return True

    def cancel_task(self, task_id: str, reason: str = "Operator cancelled") -> Optional[TaskCancelMessage]:
        """Cancel a pending or running task and generate a signed cancellation message."""
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return None
            record.state = TaskState.CANCELLED
            record.error = reason
            node_id = record.envelope.node_id

        # Sign cancellation message
        from protocols.crypto import canonicalize_json
        now = time.time()
        payload = canonicalize_json({
            "task_id": task_id,
            "node_id": node_id,
            "reason": reason,
            "timestamp": now,
        })
        sig = sign_message(self._private_key, payload)

        return TaskCancelMessage(
            task_id=task_id,
            node_id=node_id,
            reason=reason,
            timestamp=now,
            coordinator_signature=sig,
        )

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return None
            return {
                "task_id": record.envelope.task_id,
                "node_id": record.envelope.node_id,
                "task_type": record.envelope.task_type.value,
                "model_name": record.envelope.model_name,
                "state": record.state.value,
                "parameters": record.envelope.parameters,
                "duration_ms": record.duration_ms,
                "result_data": record.result_data,
                "error": record.error,
                "created_at": record.created_at,
                "completed_at": record.completed_at,
            }

    def list_tasks(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "task_id": r.envelope.task_id,
                    "node_id": r.envelope.node_id,
                    "task_type": r.envelope.task_type.value,
                    "model_name": r.envelope.model_name,
                    "state": r.state.value,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                    "created_at": r.created_at,
                    "completed_at": r.completed_at,
                }
                for r in self._tasks.values()
            ]
