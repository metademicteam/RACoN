"""Worker and Node management module for RACoN.

Manages node registration, local hardware telemetry integration,
worker session lifecycles, and heartbeat stale timeout detection.
"""
from __future__ import annotations

import enum
import logging
import platform
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from backend.racon.hardware.discovery import HardwareSnapshot, discover_hardware

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 30.0


class NodeStatus(str, enum.Enum):
    """Operational status of a registered compute node."""
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY = "BUSY"
    DEGRADED = "DEGRADED"


class WorkerSessionStatus(str, enum.Enum):
    """Lifecycle status of an active worker session."""
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


@dataclass
class WorkerSession:
    """An active worker process or execution session on a node."""
    session_id: str
    node_id: str
    status: WorkerSessionStatus = WorkerSessionStatus.ACTIVE
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    stopped_at: Optional[str] = None
    assigned_tasks: list[str] = field(default_factory=list)
    active_task_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert session to dictionary."""
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class WorkerNode:
    """A registered compute node (local host, remote peer, or simulated worker)."""
    node_id: str
    hostname: str
    is_local: bool = False
    is_simulated: bool = False
    status: NodeStatus = NodeStatus.ONLINE
    hardware: Optional[HardwareSnapshot] = None
    last_heartbeat: float = field(default_factory=time.time)
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    capabilities: dict[str, Any] = field(default_factory=dict)
    active_session_ids: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert node to dictionary representation."""
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "is_local": self.is_local,
            "is_simulated": self.is_simulated,
            "status": self.status.value,
            "hardware": self.hardware.to_dict() if self.hardware else None,
            "last_heartbeat": self.last_heartbeat,
            "registered_at": self.registered_at,
            "capabilities": self.capabilities,
            "active_session_ids": list(self.active_session_ids),
            "metadata": self.metadata,
        }


class WorkerManager:
    """Thread-safe manager for worker nodes, telemetry, and sessions."""

    def __init__(self, heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS):
        """Initialize the WorkerManager.

        Args:
            heartbeat_timeout: Seconds before an un-heartbeated node is marked OFFLINE.
        """
        self.heartbeat_timeout = heartbeat_timeout
        self._nodes: dict[str, WorkerNode] = {}
        self._sessions: dict[str, WorkerSession] = {}
        self._local_node_id: Optional[str] = None
        self._lock = threading.RLock()

    @property
    def local_node_id(self) -> Optional[str]:
        """Get the ID of the registered local node, if any."""
        with self._lock:
            return self._local_node_id

    def register_local_node(self, node_id: Optional[str] = None) -> WorkerNode:
        """Discover local hardware and register this machine as a local node.

        Args:
            node_id: Optional explicit ID. If None, generates a deterministic
                     or persistent node ID based on hostname.

        Returns:
            WorkerNode: The registered local node with real hardware snapshot.
        """
        snapshot = discover_hardware()
        host = platform.node() or "localhost"

        with self._lock:
            if node_id is None:
                if self._local_node_id and self._local_node_id in self._nodes:
                    node_id = self._local_node_id
                else:
                    node_id = f"node-local-{uuid.uuid4().hex[:8]}"

            caps: dict[str, Any] = {
                "cpu_cores": snapshot.cpu_logical_count,
                "cpu_physical_cores": snapshot.cpu_physical_count,
                "ram_total_mb": snapshot.ram_total_mb,
                "gpu_name": snapshot.gpu.name,
                "gpu_vendor": snapshot.gpu.vendor,
                "gpu_supported": snapshot.gpu.supported,
                "runtimes": ["cpu", "ollama"],
            }

            node = WorkerNode(
                node_id=node_id,
                hostname=host,
                is_local=True,
                is_simulated=False,
                status=NodeStatus.ONLINE,
                hardware=snapshot,
                last_heartbeat=time.time(),
                capabilities=caps,
                metadata={"platform": platform.platform()},
            )

            self._nodes[node_id] = node
            self._local_node_id = node_id
            logger.info("Registered local node [%s] (%s)", node_id, host)
            return node

    def register_node(
        self,
        node_id: str,
        hostname: str,
        is_simulated: bool = False,
        hardware: Optional[HardwareSnapshot] = None,
        capabilities: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> WorkerNode:
        """Register a remote or simulated compute node.

        Args:
            node_id: Unique identifier for the node.
            hostname: Hostname or network identifier.
            is_simulated: True if this is a synthetic simulation node.
            hardware: Optional initial HardwareSnapshot.
            capabilities: Optional capabilities dictionary.
            metadata: Optional additional metadata.

        Returns:
            WorkerNode: The registered node.
        """
        with self._lock:
            node = WorkerNode(
                node_id=node_id,
                hostname=hostname,
                is_local=False,
                is_simulated=is_simulated,
                status=NodeStatus.ONLINE,
                hardware=hardware,
                last_heartbeat=time.time(),
                capabilities=capabilities or {},
                metadata=metadata or {},
            )
            self._nodes[node_id] = node
            logger.info(
                "Registered %s node [%s] (%s)",
                "simulated" if is_simulated else "remote",
                node_id,
                hostname,
            )
            return node

    def unregister_node(self, node_id: str) -> bool:
        """Unregister a node and cleanly stop its active sessions."""
        with self._lock:
            node = self._nodes.pop(node_id, None)
            if not node:
                return False

            # Stop all active sessions for this node
            for sess_id in list(node.active_session_ids):
                self.stop_worker_session(sess_id, reason="node_unregistered")

            if self._local_node_id == node_id:
                self._local_node_id = None

            logger.info("Unregistered node [%s]", node_id)
            return True

    def get_node(self, node_id: str) -> Optional[WorkerNode]:
        """Retrieve a registered node by ID."""
        with self._lock:
            return self._nodes.get(node_id)

    def get_local_node(self) -> Optional[WorkerNode]:
        """Retrieve the local node if registered."""
        with self._lock:
            if self._local_node_id:
                return self._nodes.get(self._local_node_id)
            return None

    def list_nodes(self) -> list[WorkerNode]:
        """Return a snapshot of all registered nodes."""
        with self._lock:
            return list(self._nodes.values())

    def update_heartbeat(self, node_id: str) -> bool:
        """Record a heartbeat signal from a node.

        If the node was previously marked OFFLINE, it is restored to ONLINE.

        Args:
            node_id: Node to update.

        Returns:
            bool: True if node exists and heartbeat was recorded, False otherwise.
        """
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return False

            node.last_heartbeat = time.time()
            if node.status == NodeStatus.OFFLINE:
                node.status = NodeStatus.ONLINE
                logger.info("Node [%s] restored to ONLINE from heartbeat", node_id)
            return True

    def check_heartbeats(
        self,
        stale_threshold_seconds: Optional[float] = None,
    ) -> list[str]:
        """Check all nodes for expired heartbeats and transition stale nodes to OFFLINE.

        Args:
            stale_threshold_seconds: Max acceptable elapsed time without a heartbeat.
                                     Defaults to self.heartbeat_timeout (30.0s).

        Returns:
            list[str]: IDs of nodes that timed out and were transitioned to OFFLINE.
        """
        threshold = (
            stale_threshold_seconds
            if stale_threshold_seconds is not None
            else self.heartbeat_timeout
        )
        now = time.time()
        timed_out: list[str] = []

        with self._lock:
            for node_id, node in self._nodes.items():
                if node.status != NodeStatus.OFFLINE:
                    elapsed = now - node.last_heartbeat
                    if elapsed > threshold:
                        node.status = NodeStatus.OFFLINE
                        timed_out.append(node_id)
                        logger.warning(
                            "Node [%s] heartbeat timed out (elapsed %.1fs > %.1fs). Marked OFFLINE.",
                            node_id,
                            elapsed,
                            threshold,
                        )
                        # Mark its sessions as stopped due to timeout
                        for sess_id in list(node.active_session_ids):
                            self.stop_worker_session(
                                sess_id, reason="node_heartbeat_timeout"
                            )

        return timed_out

    def start_worker_session(
        self,
        node_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> WorkerSession:
        """Start a new worker session on a registered node.

        Args:
            node_id: Node on which to start the session.
            metadata: Optional session metadata.

        Returns:
            WorkerSession: The newly created active session.

        Raises:
            KeyError: If node_id does not exist.
            ValueError: If the target node is OFFLINE.
        """
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                raise KeyError(f"Node '{node_id}' is not registered.")
            if node.status == NodeStatus.OFFLINE:
                raise ValueError(
                    f"Cannot start worker session on OFFLINE node '{node_id}'."
                )

            session_id = f"sess-{uuid.uuid4().hex[:8]}"
            session = WorkerSession(
                session_id=session_id,
                node_id=node_id,
                status=WorkerSessionStatus.ACTIVE,
                metadata=metadata or {},
            )

            self._sessions[session_id] = session
            node.active_session_ids.add(session_id)
            logger.info("Started worker session [%s] on node [%s]", session_id, node_id)
            return session

    def stop_worker_session(
        self,
        session_id: str,
        reason: str = "",
    ) -> Optional[WorkerSession]:
        """Stop an existing worker session.

        Args:
            session_id: ID of the session to terminate.
            reason: Optional justification for stopping.

        Returns:
            Optional[WorkerSession]: The updated session, or None if not found.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            if session.status != WorkerSessionStatus.STOPPED:
                session.status = WorkerSessionStatus.STOPPED
                session.stopped_at = datetime.now(timezone.utc).isoformat()
                if reason:
                    session.metadata["stop_reason"] = reason

                node = self._nodes.get(session.node_id)
                if node and session_id in node.active_session_ids:
                    node.active_session_ids.remove(session_id)

                logger.info(
                    "Stopped worker session [%s] on node [%s] (reason: '%s')",
                    session_id,
                    session.node_id,
                    reason,
                )

            return session

    def get_session(self, session_id: str) -> Optional[WorkerSession]:
        """Retrieve a session by ID."""
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(
        self,
        node_id: Optional[str] = None,
        active_only: bool = False,
    ) -> list[WorkerSession]:
        """List worker sessions, optionally filtered by node or active status."""
        with self._lock:
            sessions = list(self._sessions.values())
            if node_id is not None:
                sessions = [s for s in sessions if s.node_id == node_id]
            if active_only:
                sessions = [
                    s for s in sessions if s.status == WorkerSessionStatus.ACTIVE
                ]
            return sessions

    def refresh_local_telemetry(self) -> Optional[HardwareSnapshot]:
        """Re-probe local hardware telemetry and update the local node snapshot."""
        with self._lock:
            if not self._local_node_id:
                return None
            node = self._nodes.get(self._local_node_id)
            if not node:
                return None

            snapshot = discover_hardware()
            node.hardware = snapshot
            node.last_heartbeat = time.time()
            return snapshot
