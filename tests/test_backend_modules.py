"""Unit and integration tests for RACoN backend hardware, runtime, and worker modules."""

import asyncio
import time
import pytest

from backend.racon.hardware import (
    GpuInfo,
    HardwareSnapshot,
    detect_gpu,
    discover_hardware,
    get_cpu_name,
    get_lightweight_update,
)
from backend.racon.runtime import (
    DistributedRuntime,
    ExecutionMode,
    InferenceExecutionError,
    InferenceRuntime,
    LayerPartition,
    ModelNotFoundError,
    OllamaConnectionError,
    OllamaRuntime,
    PartitionPlan,
    RACoNRuntimeError,
    RuntimeUnavailableError,
)
from backend.racon.workers import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    InvalidStateTransitionError,
    NodeStatus,
    TaskState,
    TaskStateMachine,
    VALID_TRANSITIONS,
    WorkerManager,
    WorkerNode,
    WorkerSession,
    WorkerSessionStatus,
    is_terminal_state,
    transition_task,
    validate_transition,
)


# =====================================================================
# 1. Hardware Discovery Tests
# =====================================================================

def test_hardware_snapshot_instantiation():
    """HardwareSnapshot creates with valid defaults."""
    snap = HardwareSnapshot()
    assert snap.cpu_name == "Unknown"
    assert snap.cpu_logical_count == 0
    assert snap.ram_total_mb == 0.0
    assert isinstance(snap.gpu, GpuInfo)
    assert isinstance(snap.to_dict(), dict)


def test_discover_hardware_returns_real_telemetry():
    """discover_hardware() captures genuine local CPU, RAM, and OS telemetry."""
    snap = discover_hardware()
    assert isinstance(snap, HardwareSnapshot)
    assert len(snap.cpu_name.strip()) > 0
    assert snap.cpu_name != "Unknown"
    assert snap.cpu_logical_count > 0
    assert snap.ram_total_mb > 0
    assert snap.ram_available_mb > 0
    assert snap.ram_available_mb <= snap.ram_total_mb
    assert snap.process_memory_mb > 0
    assert snap.os_name in ("Windows", "Linux", "Darwin")
    assert isinstance(snap.to_dict(), dict)


def test_intel_uhd_gpu_non_fabrication():
    """Intel UHD integrated GPUs must not fabricate VRAM or utilization metrics."""
    gpu = detect_gpu()
    assert isinstance(gpu, GpuInfo)
    if gpu.name and "intel" in gpu.name.lower():
        assert gpu.vendor == "Intel"
        assert gpu.supported is False
        assert gpu.vram_total_mb is None
        assert gpu.vram_used_mb is None
        assert gpu.utilization_pct is None


def test_get_lightweight_update():
    """get_lightweight_update() provides fast-updating metrics without error."""
    update = get_lightweight_update()
    assert isinstance(update, dict)
    assert "cpu_utilization_pct" in update
    assert "ram_available_mb" in update
    assert "ram_used_mb" in update
    assert "ram_utilization_pct" in update
    assert "process_memory_mb" in update
    assert "timestamp" in update


# =====================================================================
# 2. Runtime Interface & Exceptions Tests
# =====================================================================

def test_inference_runtime_cannot_be_instantiated_directly():
    """InferenceRuntime ABC requires implementation of abstract methods."""
    with pytest.raises(TypeError):
        InferenceRuntime()  # type: ignore


def test_runtime_exceptions_hierarchy():
    """Verify runtime exception class inheritance."""
    err = OllamaConnectionError("service down", details={"port": 11434})
    assert isinstance(err, RuntimeUnavailableError)
    assert isinstance(err, RACoNRuntimeError)
    assert err.details["port"] == 11434


# =====================================================================
# 3. Ollama Runtime Tests
# =====================================================================

def test_ollama_runtime_connection_refusal_graceful():
    """OllamaRuntime handles connection refusal cleanly with informative errors."""
    async def _run():
        dead_runtime = OllamaRuntime(base_url="http://127.0.0.1:54321", timeout=1.0)
        avail = await dead_runtime.is_available()
        assert avail is False

        models = await dead_runtime.list_models()
        assert models == []

        health = await dead_runtime.health()
        assert health["status"] == "offline"
        assert health["available"] is False
        assert health["error"] is not None

        with pytest.raises(OllamaConnectionError) as exc_info:
            await dead_runtime.execute("nonexistent-model", "test prompt")
        assert "Connection refused" in str(exc_info.value)

        await dead_runtime.close()

    asyncio.run(_run())


def test_ollama_runtime_live_inspection():
    """OllamaRuntime queries live local Ollama instance if available."""
    async def _run():
        runtime = OllamaRuntime()
        avail = await runtime.is_available()
        if avail:
            models = await runtime.list_models()
            assert isinstance(models, list)
            if models:
                m = models[0]
                assert "name" in m
                assert "size" in m
                assert "digest" in m
                assert "modified_at" in m

            h = await runtime.health()
            assert h["status"] == "online"
            assert h["available"] is True
            assert h["model_count"] == len(models)
        await runtime.close()

    asyncio.run(_run())


# =====================================================================
# 4. Distributed Runtime Tests
# =====================================================================

def test_distributed_runtime_simulation_labeling():
    """DistributedRuntime in simulation mode explicitly labels synthetic execution."""
    async def _run():
        runtime = DistributedRuntime(mode=ExecutionMode.SIMULATION)
        assert await runtime.is_available() is True

        models = await runtime.list_models()
        assert any(m.get("is_simulated") for m in models)

        # Test layer partition creation
        plan = runtime.create_partition_plan(
            model_name="llama3:70b-simulated",
            total_layers=32,
            worker_assignments=[
                {"worker_id": "worker-alpha", "device_type": "cuda", "is_simulated": True},
                {"worker_id": "worker-beta", "device_type": "cpu", "is_simulated": True},
            ],
        )
        assert plan.is_simulated is True
        assert len(plan.partitions) == 2
        assert plan.partitions[0].layer_count() == 16
        assert plan.partitions[1].layer_count() == 16

        # Execute simulation
        result = await runtime.execute("llama3:70b-simulated", "Explain pipeline parallelism", partition_plan=plan)

        # STRICT ASSERTIONS: Simulations are never presented as real distributed inference
        assert result["is_simulated"] is True
        assert result["synthetic_execution"] is True
        assert result["execution_mode"] == "simulation"
        assert result["simulation_provenance"] == "SYNTHETIC_SIMULATION"
        assert "simulation_notice" in result
        assert "CRITICAL" in result["simulation_notice"]
        assert "partition_traces" in result
        assert len(result["partition_traces"]) == 2

    asyncio.run(_run())


# =====================================================================
# 5. Worker State Machine Tests
# =====================================================================

def test_task_state_enum_members():
    """Verify all 7 mandatory task states exist."""
    expected = {"QUEUED", "VALIDATING", "SCHEDULED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"}
    actual = {s.value for s in TaskState}
    assert actual == expected


def test_transition_task_legal_transitions():
    """Valid transitions succeed."""
    assert transition_task(TaskState.QUEUED, TaskState.VALIDATING) is True
    assert transition_task("QUEUED", "VALIDATING") is True
    assert transition_task("queued", "validating") is True
    assert transition_task(TaskState.VALIDATING, TaskState.SCHEDULED) is True
    assert transition_task(TaskState.SCHEDULED, TaskState.RUNNING) is True
    assert transition_task(TaskState.RUNNING, TaskState.COMPLETED) is True

    # Cancellation from non-terminal states
    assert transition_task(TaskState.QUEUED, TaskState.CANCELLED) is True
    assert transition_task(TaskState.VALIDATING, TaskState.CANCELLED) is True
    assert transition_task(TaskState.SCHEDULED, TaskState.CANCELLED) is True
    assert transition_task(TaskState.RUNNING, TaskState.CANCELLED) is True

    # Failure from processing states
    assert transition_task(TaskState.VALIDATING, TaskState.FAILED) is True
    assert transition_task(TaskState.SCHEDULED, TaskState.FAILED) is True
    assert transition_task(TaskState.RUNNING, TaskState.FAILED) is True


def test_transition_task_illegal_transitions():
    """Invalid transitions are blocked."""
    assert transition_task(TaskState.QUEUED, TaskState.RUNNING) is False
    assert transition_task(TaskState.QUEUED, TaskState.COMPLETED) is False
    assert transition_task(TaskState.COMPLETED, TaskState.RUNNING) is False
    assert transition_task(TaskState.FAILED, TaskState.RUNNING) is False
    assert transition_task(TaskState.CANCELLED, TaskState.RUNNING) is False
    assert transition_task("INVALID_STATE", "RUNNING") is False


def test_validate_transition_raises_error():
    """validate_transition raises InvalidStateTransitionError on illegal move."""
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        validate_transition(TaskState.QUEUED, TaskState.RUNNING, reason="premature leap")
    assert "Invalid task state transition from 'QUEUED' to 'RUNNING'" in str(exc_info.value)


def test_task_state_machine_audit_trail():
    """TaskStateMachine correctly tracks transitions and timestamps."""
    sm = TaskStateMachine(task_id="task-001", initial_state=TaskState.QUEUED)
    assert sm.current_state == TaskState.QUEUED
    assert sm.is_terminal() is False

    sm.transition_to(TaskState.VALIDATING, reason="syntax validation")
    assert sm.current_state == TaskState.VALIDATING

    sm.transition_to(TaskState.SCHEDULED, reason="node assigned")
    sm.transition_to(TaskState.RUNNING, reason="execution started")
    sm.transition_to(TaskState.COMPLETED, reason="tokens emitted")

    assert sm.is_terminal() is True
    assert len(sm.history) == 4
    serialized = sm.to_dict()
    assert serialized["current_state"] == "COMPLETED"
    assert len(serialized["history"]) == 4


# =====================================================================
# 6. Worker Manager Tests
# =====================================================================

def test_worker_manager_local_node_registration():
    """WorkerManager registers local node with real hardware snapshot."""
    manager = WorkerManager()
    node = manager.register_local_node()

    assert node.is_local is True
    assert node.is_simulated is False
    assert node.status == NodeStatus.ONLINE
    assert node.hardware is not None
    assert node.hardware.cpu_logical_count > 0
    assert node.node_id == manager.local_node_id

    # Verify node retrieval
    fetched = manager.get_node(node.node_id)
    assert fetched is not None
    assert fetched.node_id == node.node_id
    assert fetched.to_dict()["is_local"] is True


def test_worker_manager_session_lifecycle():
    """WorkerManager creates and stops worker sessions on a node."""
    manager = WorkerManager()
    node = manager.register_local_node()

    # Start session
    session = manager.start_worker_session(node.node_id, metadata={"worker_type": "ollama"})
    assert session.status == WorkerSessionStatus.ACTIVE
    assert session.node_id == node.node_id
    assert session.session_id in node.active_session_ids

    # Query active sessions
    active = manager.list_sessions(node_id=node.node_id, active_only=True)
    assert len(active) == 1

    # Stop session
    stopped = manager.stop_worker_session(session.session_id, reason="task complete")
    assert stopped is not None
    assert stopped.status == WorkerSessionStatus.STOPPED
    assert stopped.stopped_at is not None
    assert session.session_id not in node.active_session_ids


def test_worker_manager_heartbeat_timeout_detection():
    """Nodes missing heartbeats beyond stale threshold are marked OFFLINE."""
    manager = WorkerManager(heartbeat_timeout=1.0)
    remote = manager.register_node(
        node_id="remote-node-1",
        hostname="rig-alpha",
        is_simulated=False,
    )
    session = manager.start_worker_session(remote.node_id)
    assert session.status == WorkerSessionStatus.ACTIVE

    # Simulate time passing by manually dialing back last_heartbeat
    remote.last_heartbeat = time.time() - 35.0  # 35 seconds stale (> 30s)

    timed_out = manager.check_heartbeats(stale_threshold_seconds=30.0)
    assert "remote-node-1" in timed_out
    assert remote.status == NodeStatus.OFFLINE

    # Active sessions on the timed out node should be stopped
    assert session.status == WorkerSessionStatus.STOPPED
    assert session.metadata.get("stop_reason") == "node_heartbeat_timeout"

    # Heartbeat restore
    assert manager.update_heartbeat("remote-node-1") is True
    assert remote.status == NodeStatus.ONLINE
