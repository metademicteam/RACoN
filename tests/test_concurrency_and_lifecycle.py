"""Tests for concurrency, duplicate jobs prevention, SQLite concurrent access, and graceful shutdown.

Fulfills Action Item 4 and Gate 1 requirements of RACoN Detailed Architecture.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication

from racon.scheduler.models import (
    WorkerCandidate,
    SchedulingObjective,
    SchedulingMode,
    WorkerStatus,
    DeviceType,
    SchedulerResult,
)
from racon.scheduler.evaluation import SchedulingEngine
from racon.services.benchmark_service import BenchmarkService
from racon.storage.database import Database
from racon.storage.repository import BenchmarkRepository


def make_test_worker(worker_id: str = "w1", **overrides) -> WorkerCandidate:
    defaults = {
        "worker_id": worker_id,
        "is_simulated": True,
        "status": WorkerStatus.AVAILABLE,
        "device_type": DeviceType.CPU,
        "ram_total_mb": 16384.0,
        "ram_available_mb": 8192.0,
        "accelerator_memory_mb": None,
        "processing_speed_tps": 25.0,
        "network_latency_ms": 5.0,
        "current_workload_pct": 10.0,
        "model_partition_required_mb": 2048.0,
        "compatible_runtimes": [DeviceType.CPU, DeviceType.ONNX],
    }
    defaults.update(overrides)
    return WorkerCandidate(**defaults)


@pytest.fixture
def qapp():
    """Ensure a QCoreApplication exists for Qt signals/threads."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create a temporary initialized Database in WAL mode."""
    db_path = tmp_path / "test_concurrency.db"
    db = Database(db_path)
    db.initialize()
    yield db
    db.close()


def test_duplicate_jobs_prevented(qapp, temp_db):
    """Calling run_scheduler while an operation is already busy must reject duplicate run."""
    repo = BenchmarkRepository(temp_db)
    service = BenchmarkService(repo)

    candidates = [make_test_worker("w1"), make_test_worker("w2")]

    error_messages = []
    service.scheduler_error.connect(lambda err: error_messages.append(err))

    # Manually mark as busy to simulate an ongoing background operation
    service._busy = True

    # Attempt to trigger another run
    service.run_scheduler(
        candidates,
        SchedulingObjective.BALANCED,
        SchedulingMode.DETERMINISTIC,
        "Duplicate Job Test",
    )

    # Process events
    qapp.processEvents()

    assert len(error_messages) == 1
    assert "already running" in error_messages[0]


def test_cancel_or_wait_graceful_shutdown(qapp, temp_db):
    """cancel_or_wait cleanly shuts down threads without errors or hanging."""
    repo = BenchmarkRepository(temp_db)
    service = BenchmarkService(repo)

    # Calling cancel_or_wait when idle should be safe
    service.cancel_or_wait(timeout_ms=500)
    assert service.is_busy is False


def test_database_concurrent_writes(temp_db):
    """Multiple threads simultaneously writing benchmark runs to SQLite must not corrupt or lock."""
    repo = BenchmarkRepository(temp_db)

    def write_run(idx: int):
        result = SchedulerResult(
            scenario_name=f"Concurrent Scenario {idx}",
            objective=SchedulingObjective.BALANCED,
            mode=SchedulingMode.DETERMINISTIC,
            candidate_count=2,
            feasible_count=2,
            final_choice=f"worker-{idx}",
            final_score=0.95,
            success=True,
            decision_duration_ms=12.5,
        )
        return repo.save_run(result)

    num_threads = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(write_run, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # All writes must succeed
    assert all(results)
    assert repo.get_run_count() == num_threads


def test_shadow_mode_preserves_deterministic_decision():
    """Verify shadow mode never alters the deterministic choice."""
    engine = SchedulingEngine()
    candidates = [
        make_test_worker("fast-worker", processing_speed_tps=50.0),
        make_test_worker("slow-worker", processing_speed_tps=10.0),
    ]

    res_det = engine.run(
        candidates,
        SchedulingObjective.HIGHEST_THROUGHPUT,
        SchedulingMode.DETERMINISTIC,
        "Shadow Preserves Deterministic",
    )

    res_shadow = engine.run(
        candidates,
        SchedulingObjective.HIGHEST_THROUGHPUT,
        SchedulingMode.LAYA_SHADOW,
        "Shadow Preserves Deterministic",
    )

    # The final choice in shadow mode MUST be identical to pure deterministic
    assert res_shadow.final_choice == res_det.final_choice
    assert res_shadow.final_choice == "fast-worker"
