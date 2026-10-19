"""Tests for Database and BenchmarkRepository storage layer."""
from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from racon.scheduler.models import (
    CandidateEvaluation,
    SchedulingMode,
    SchedulingObjective,
    SchedulerResult,
)
from racon.storage.database import Database
from racon.storage.repository import BenchmarkRepository


def make_sample_result(
    run_id: str = "run-001",
    timestamp: str = "2026-10-20T10:00:00",
    scenario_name: str = "edge_cluster_heavy_load",
    objective: SchedulingObjective = SchedulingObjective.LOWEST_LATENCY,
    mode: SchedulingMode = SchedulingMode.LAYA_ASSISTED,
    candidate_count: int = 2,
    feasible_count: int = 2,
    deterministic_choice: str = "worker-1",
    deterministic_score: float = 0.88,
    laya_recommendation: str = "worker-1",
    laya_confidence: float = 0.92,
    laya_used_successfully: bool = True,
    final_choice: str = "worker-1",
    final_score: float = 0.90,
    decision_duration_ms: float = 45.2,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    success: bool = True,
    failure_reason: str | None = None,
    evaluations: list[CandidateEvaluation] | None = None,
) -> SchedulerResult:
    """Helper to build a valid SchedulerResult with evaluations."""
    if evaluations is None:
        evaluations = [
            CandidateEvaluation(
                worker_id="worker-1",
                is_feasible=True,
                rejection_reasons=[],
                score=0.88,
                score_components={"latency": 0.9, "speed": 0.85},
                is_simulated=False,
            ),
            CandidateEvaluation(
                worker_id="worker-2",
                is_feasible=True,
                rejection_reasons=[],
                score=0.75,
                score_components={"latency": 0.7, "speed": 0.80},
                is_simulated=True,
            ),
        ]
    return SchedulerResult(
        run_id=run_id,
        timestamp=timestamp,
        scenario_name=scenario_name,
        objective=objective,
        mode=mode,
        candidate_count=candidate_count,
        feasible_count=feasible_count,
        deterministic_choice=deterministic_choice,
        deterministic_score=deterministic_score,
        laya_recommendation=laya_recommendation,
        laya_confidence=laya_confidence,
        laya_used_successfully=laya_used_successfully,
        final_choice=final_choice,
        final_score=final_score,
        decision_duration_ms=decision_duration_ms,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        success=success,
        failure_reason=failure_reason,
        evaluations=evaluations,
    )


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test database files."""
    temp_dir_path = tempfile.mkdtemp()
    yield Path(temp_dir_path)
    shutil.rmtree(temp_dir_path, ignore_errors=True)


@pytest.fixture
def db(temp_dir):
    """Create and initialize a Database instance backed by a temporary file."""
    db_path = temp_dir / "test_benchmark.db"
    database = Database(db_path)
    database.initialize()
    yield database
    database.close()


@pytest.fixture
def repo(db):
    """Create a BenchmarkRepository connected to the test database."""
    return BenchmarkRepository(db)


def test_database_initialization(db):
    """1. Database creates tables successfully."""
    conn = db.connect()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}

    assert "schema_info" in tables
    assert "settings" in tables
    assert "benchmark_runs" in tables
    assert "candidate_evaluations" in tables

    version_cursor = conn.execute("SELECT MAX(version) FROM schema_info")
    assert version_cursor.fetchone()[0] == 1

    # Initialization is idempotent
    db.initialize()
    assert db._initialized is True


def test_save_and_retrieve_run(repo):
    """2. Save a SchedulerResult, retrieve it, verify all fields."""
    sample = make_sample_result()
    saved = repo.save_run(sample)
    assert saved is True

    retrieved = repo.get_run(sample.run_id)
    assert retrieved is not None
    assert retrieved["run_id"] == sample.run_id
    assert retrieved["timestamp"] == sample.timestamp
    assert retrieved["scenario_name"] == sample.scenario_name
    assert retrieved["objective"] == sample.objective.value
    assert retrieved["mode"] == sample.mode.value
    assert retrieved["candidate_count"] == sample.candidate_count
    assert retrieved["feasible_count"] == sample.feasible_count
    assert retrieved["deterministic_choice"] == sample.deterministic_choice
    assert retrieved["deterministic_score"] == pytest.approx(sample.deterministic_score)
    assert retrieved["laya_recommendation"] == sample.laya_recommendation
    assert retrieved["laya_confidence"] == pytest.approx(sample.laya_confidence)
    assert retrieved["laya_used_successfully"] == 1
    assert retrieved["final_choice"] == sample.final_choice
    assert retrieved["final_score"] == pytest.approx(sample.final_score)
    assert retrieved["decision_duration_ms"] == pytest.approx(sample.decision_duration_ms)
    assert retrieved["fallback_used"] == 0
    assert retrieved["fallback_reason"] is None
    assert retrieved["success"] == 1
    assert retrieved["failure_reason"] is None


def test_settings_persist(repo):
    """3. set_setting then get_setting returns same value."""
    success = repo.set_setting("active_theme", "dracula")
    assert success is True
    assert repo.get_setting("active_theme") == "dracula"
    assert repo.get_setting("unknown_key", default="fallback_val") == "fallback_val"
    assert repo.get_setting("missing_key") == ""


def test_settings_overwrite(repo):
    """4. Setting same key twice keeps latest value."""
    repo.set_setting("target_fps", "30")
    assert repo.get_setting("target_fps") == "30"

    repo.set_setting("target_fps", "60")
    assert repo.get_setting("target_fps") == "60"


def test_export_csv(repo):
    """5. Export runs as CSV, verify it contains expected columns."""
    assert repo.export_runs_csv() == ""

    run1 = make_sample_result(run_id="run-c1", timestamp="2026-10-20T10:00:00")
    run2 = make_sample_result(run_id="run-c2", timestamp="2026-10-20T11:00:00")
    repo.save_run(run1)
    repo.save_run(run2)

    csv_data = repo.export_runs_csv()
    assert len(csv_data) > 0

    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)
    assert len(rows) == 2

    expected_fields = {
        "run_id",
        "timestamp",
        "scenario_name",
        "objective",
        "mode",
        "candidate_count",
        "feasible_count",
        "deterministic_choice",
        "final_choice",
        "success",
    }
    assert expected_fields.issubset(set(reader.fieldnames or []))

    exported_ids = [r["run_id"] for r in rows]
    assert "run-c1" in exported_ids
    assert "run-c2" in exported_ids


def test_export_json(repo):
    """6. Export runs as JSON, verify it parses correctly."""
    assert json.loads(repo.export_runs_json()) == []

    run1 = make_sample_result(run_id="run-j1", timestamp="2026-10-20T09:00:00")
    run2 = make_sample_result(run_id="run-j2", timestamp="2026-10-20T12:00:00")
    repo.save_run(run1)
    repo.save_run(run2)

    json_data = repo.export_runs_json()
    parsed = json.loads(json_data)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    # Ordered by timestamp DESC
    assert parsed[0]["run_id"] == "run-j2"
    assert parsed[1]["run_id"] == "run-j1"


def test_clear_all_runs(repo, db):
    """7. After clearing, get_all_runs returns empty."""
    repo.save_run(make_sample_result(run_id="run-clear-1"))
    repo.save_run(make_sample_result(run_id="run-clear-2"))
    assert repo.get_run_count() == 2

    cleared = repo.clear_all_runs()
    assert cleared is True
    assert repo.get_all_runs() == []
    assert repo.get_run_count() == 0

    conn = db.connect()
    ev_count = conn.execute("SELECT COUNT(*) FROM candidate_evaluations").fetchone()[0]
    assert ev_count == 0


def test_run_count(repo):
    """8. get_run_count returns correct number after multiple saves."""
    assert repo.get_run_count() == 0

    repo.save_run(make_sample_result(run_id="run-cnt-1", timestamp="2026-10-20T01:00:00"))
    assert repo.get_run_count() == 1

    repo.save_run(make_sample_result(run_id="run-cnt-2", timestamp="2026-10-20T02:00:00"))
    assert repo.get_run_count() == 2

    repo.save_run(make_sample_result(run_id="run-cnt-3", timestamp="2026-10-20T03:00:00"))
    assert repo.get_run_count() == 3


def test_get_run_with_evaluations(repo):
    """9. Saved evaluations are retrievable with the run."""
    eval_feasible = CandidateEvaluation(
        worker_id="worker-gpu",
        is_feasible=True,
        rejection_reasons=[],
        score=0.95,
        score_components={"latency": 0.9, "throughput": 1.0},
        is_simulated=False,
    )
    eval_infeasible = CandidateEvaluation(
        worker_id="worker-oom",
        is_feasible=False,
        rejection_reasons=["insufficient_memory", "high_latency"],
        score=None,
        score_components={},
        is_simulated=True,
    )

    result = make_sample_result(
        run_id="run-with-evals",
        evaluations=[eval_feasible, eval_infeasible],
    )
    repo.save_run(result)

    run = repo.get_run("run-with-evals")
    assert run is not None
    evals = run["evaluations"]
    assert len(evals) == 2

    gpu_ev = next(e for e in evals if e["worker_id"] == "worker-gpu")
    oom_ev = next(e for e in evals if e["worker_id"] == "worker-oom")

    assert gpu_ev["is_feasible"] == 1
    assert gpu_ev["is_simulated"] == 0
    assert gpu_ev["score"] == pytest.approx(0.95)
    assert gpu_ev["rejection_reasons"] is None
    assert json.loads(gpu_ev["score_components"]) == {"latency": 0.9, "throughput": 1.0}

    assert oom_ev["is_feasible"] == 0
    assert oom_ev["is_simulated"] == 1
    assert oom_ev["score"] is None
    assert json.loads(oom_ev["rejection_reasons"]) == ["insufficient_memory", "high_latency"]
    assert oom_ev["score_components"] is None


def test_invalid_run_id_returns_none(repo):
    """10. get_run with nonexistent ID returns None."""
    assert repo.get_run("nonexistent-run-id-999") is None


def test_multiple_runs_ordered_by_timestamp(repo):
    """11. Runs are returned newest first."""
    repo.save_run(make_sample_result(run_id="run-early", timestamp="2026-10-20T08:00:00"))
    repo.save_run(make_sample_result(run_id="run-latest", timestamp="2026-10-20T12:00:00"))
    repo.save_run(make_sample_result(run_id="run-middle", timestamp="2026-10-20T10:00:00"))

    runs = repo.get_all_runs()
    assert len(runs) == 3
    run_ids = [r["run_id"] for r in runs]
    assert run_ids == ["run-latest", "run-middle", "run-early"]


def test_database_persistence_across_reconnect(temp_dir):
    """12. Close and reopen database; data persists."""
    db_path = temp_dir / "persistence_test.db"

    # First connection: create and populate
    db1 = Database(db_path)
    repo1 = BenchmarkRepository(db1)
    run = make_sample_result(run_id="run-persist-1", timestamp="2026-10-20T10:30:00")
    repo1.save_run(run)
    repo1.set_setting("system_state", "ready")
    db1.close()

    # Second connection: verify persistence
    db2 = Database(db_path)
    repo2 = BenchmarkRepository(db2)
    retrieved = repo2.get_run("run-persist-1")
    assert retrieved is not None
    assert retrieved["run_id"] == "run-persist-1"
    assert len(retrieved["evaluations"]) == 2
    assert repo2.get_setting("system_state") == "ready"
    assert repo2.get_run_count() == 1
    db2.close()
