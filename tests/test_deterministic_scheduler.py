"""Unit tests for the deterministic scheduling engine."""
from __future__ import annotations

import pytest

from racon.scheduler.models import (
    WorkerCandidate,
    SchedulingObjective,
    WorkerStatus,
    DeviceType,
    CandidateEvaluation,
)
from racon.scheduler.deterministic import DeterministicScheduler


def make_worker(**overrides) -> WorkerCandidate:
    """Create a default valid WorkerCandidate with sensible defaults."""
    defaults = {
        "worker_id": "worker-1",
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
def make_worker_fixture():
    """Fixture providing the worker factory function."""
    return make_worker


@pytest.fixture
def scheduler() -> DeterministicScheduler:
    """Fixture providing a DeterministicScheduler instance."""
    return DeterministicScheduler()


def test_valid_candidate_selected(scheduler: DeterministicScheduler):
    """A single valid available worker with sufficient RAM should be selected."""
    worker = make_worker(worker_id="valid-worker-1")
    best, evals, failure = scheduler.select_best([worker], SchedulingObjective.BALANCED)

    assert failure is None, f"Expected success but got failure: {failure}"
    assert best is not None
    assert best.worker_id == "valid-worker-1"
    assert best.is_feasible is True
    assert best.score is not None and best.score > 0
    assert len(evals) == 1
    assert evals[0].is_feasible is True
    assert evals[0].worker_id == "valid-worker-1"


def test_infeasible_insufficient_ram_rejected(scheduler: DeterministicScheduler):
    """Worker with ram_available_mb < model_partition_required_mb must be rejected."""
    worker = make_worker(
        worker_id="low-ram-worker",
        ram_available_mb=1024.0,
        model_partition_required_mb=2048.0,
    )

    evaluation = scheduler.validate_candidate(worker)
    assert evaluation.is_feasible is False
    assert any("Insufficient RAM" in reason for reason in evaluation.rejection_reasons)

    best, evals, failure = scheduler.select_best([worker], SchedulingObjective.BALANCED)
    assert best is None
    assert failure is not None
    assert failure.reason == "No feasible candidates"
    assert len(evals) == 1
    assert evals[0].is_feasible is False
    assert evals[0].score is None


def test_incompatible_runtime_rejected(scheduler: DeterministicScheduler):
    """Worker with device_type not in compatible_runtimes must be rejected."""
    worker = make_worker(
        worker_id="cuda-worker",
        device_type=DeviceType.CUDA,
        compatible_runtimes=[DeviceType.CPU, DeviceType.ONNX],
    )

    evaluation = scheduler.validate_candidate(worker)
    assert evaluation.is_feasible is False
    assert any("Incompatible runtime" in reason for reason in evaluation.rejection_reasons)

    best, evals, failure = scheduler.select_best([worker], SchedulingObjective.BALANCED)
    assert best is None
    assert failure is not None
    assert failure.reason == "No feasible candidates"
    assert len(evals) == 1
    assert evals[0].is_feasible is False
    assert evals[0].score is None


def test_unavailable_worker_rejected(scheduler: DeterministicScheduler):
    """Worker with status=UNAVAILABLE must be rejected."""
    worker = make_worker(
        worker_id="unavail-worker",
        status=WorkerStatus.UNAVAILABLE,
    )

    evaluation = scheduler.validate_candidate(worker)
    assert evaluation.is_feasible is False
    assert any("Worker is unavailable" in reason for reason in evaluation.rejection_reasons)

    best, evals, failure = scheduler.select_best([worker], SchedulingObjective.BALANCED)
    assert best is None
    assert failure is not None
    assert failure.reason == "No feasible candidates"
    assert len(evals) == 1
    assert evals[0].is_feasible is False


def test_busy_worker_full_load_rejected(scheduler: DeterministicScheduler):
    """Worker with status=BUSY and workload=100% must be rejected."""
    worker = make_worker(
        worker_id="fully-busy-worker",
        status=WorkerStatus.BUSY,
        current_workload_pct=100.0,
    )

    evaluation = scheduler.validate_candidate(worker)
    assert evaluation.is_feasible is False
    assert any("Worker is fully busy" in reason for reason in evaluation.rejection_reasons)

    best, evals, failure = scheduler.select_best([worker], SchedulingObjective.BALANCED)
    assert best is None
    assert failure is not None
    assert failure.reason == "No feasible candidates"
    assert len(evals) == 1
    assert evals[0].is_feasible is False


def test_no_feasible_candidates_failure(scheduler: DeterministicScheduler):
    """When all candidates fail validation, select_best returns failure with clear reason."""
    w1 = make_worker(worker_id="w1-unavail", status=WorkerStatus.UNAVAILABLE)
    w2 = make_worker(
        worker_id="w2-low-ram",
        ram_available_mb=500.0,
        model_partition_required_mb=2048.0,
    )
    w3 = make_worker(
        worker_id="w3-bad-runtime",
        device_type=DeviceType.MPS,
        compatible_runtimes=[DeviceType.CPU],
    )

    best, evals, failure = scheduler.select_best([w1, w2, w3], SchedulingObjective.BALANCED)

    assert best is None
    assert failure is not None
    assert failure.reason == "No feasible candidates"
    assert "rejected by hard constraints" in failure.details
    assert failure.candidate_count == 3
    assert failure.feasible_count == 0
    assert len(evals) == 3
    assert all(not ev.is_feasible for ev in evals)
    assert all(ev.score is None for ev in evals)


def test_empty_candidates_failure(scheduler: DeterministicScheduler):
    """Empty candidate list returns failure."""
    best, evals, failure = scheduler.select_best([], SchedulingObjective.BALANCED)

    assert best is None
    assert evals == []
    assert failure is not None
    assert failure.reason == "No candidates provided"
    assert "candidate list is empty" in failure.details
    assert failure.candidate_count == 0
    assert failure.feasible_count == 0


def test_deterministic_same_inputs_same_result(scheduler: DeterministicScheduler):
    """Running the same inputs twice must produce identical scores and selection."""
    w1 = make_worker(
        worker_id="worker-a",
        processing_speed_tps=30.0,
        network_latency_ms=10.0,
        current_workload_pct=20.0,
        ram_available_mb=4096.0,
    )
    w2 = make_worker(
        worker_id="worker-b",
        processing_speed_tps=50.0,
        network_latency_ms=25.0,
        current_workload_pct=15.0,
        ram_available_mb=8192.0,
    )
    w3 = make_worker(
        worker_id="worker-c",
        processing_speed_tps=15.0,
        network_latency_ms=2.0,
        current_workload_pct=5.0,
        ram_available_mb=12288.0,
    )

    candidates = [w1, w2, w3]

    best1, evals1, fail1 = scheduler.select_best(candidates, SchedulingObjective.BALANCED)
    best2, evals2, fail2 = scheduler.select_best(candidates, SchedulingObjective.BALANCED)

    assert fail1 is None and fail2 is None
    assert best1 is not None and best2 is not None
    assert best1.worker_id == best2.worker_id
    assert best1.score == best2.score
    assert [e.worker_id for e in evals1] == [e.worker_id for e in evals2]
    assert [e.score for e in evals1] == [e.score for e in evals2]
    assert [e.score_components for e in evals1] == [e.score_components for e in evals2]


def test_different_objectives_different_selection(scheduler: DeterministicScheduler):
    """Create 3 workers where latency objective picks one and throughput picks another. Verify they differ."""
    # Worker A: ultra-low network latency, moderate TPS, lower RAM headroom
    # effective_latency = (100 / 100) + 1.0 = 2.0 -> latency score = 0.5
    # throughput score = 100 * 1.0 * (100 / 100) = 100.0
    worker_lat = make_worker(
        worker_id="worker-latency-champ",
        processing_speed_tps=100.0,
        network_latency_ms=1.0,
        current_workload_pct=0.0,
        ram_available_mb=100.0,
        model_partition_required_mb=100.0,
    )

    # Worker B: very high TPS, massive RAM headroom, but high network latency
    # effective_latency = (100 / 500) + 200.0 = 200.2 -> latency score = ~0.005
    # throughput score = 500 * 1.0 * min(1000/100, 10) = 5000.0
    worker_tp = make_worker(
        worker_id="worker-throughput-champ",
        processing_speed_tps=500.0,
        network_latency_ms=200.0,
        current_workload_pct=0.0,
        ram_available_mb=1000.0,
        model_partition_required_mb=100.0,
    )

    # Worker C: balanced/average stats
    worker_mid = make_worker(
        worker_id="worker-mid",
        processing_speed_tps=50.0,
        network_latency_ms=50.0,
        current_workload_pct=0.0,
        ram_available_mb=300.0,
        model_partition_required_mb=100.0,
    )

    candidates = [worker_lat, worker_tp, worker_mid]

    best_lat, _, fail_lat = scheduler.select_best(candidates, SchedulingObjective.LOWEST_LATENCY)
    best_tp, _, fail_tp = scheduler.select_best(candidates, SchedulingObjective.HIGHEST_THROUGHPUT)

    assert fail_lat is None
    assert fail_tp is None
    assert best_lat is not None
    assert best_tp is not None

    assert best_lat.worker_id == "worker-latency-champ"
    assert best_tp.worker_id == "worker-throughput-champ"
    assert best_lat.worker_id != best_tp.worker_id


def test_multiple_feasible_highest_score_wins(scheduler: DeterministicScheduler):
    """Among 3 feasible candidates, the one with the highest score is selected."""
    w_low = make_worker(
        worker_id="worker-low-tp",
        processing_speed_tps=10.0,
        ram_available_mb=2048.0,
        model_partition_required_mb=1024.0,
        current_workload_pct=0.0,
    )
    w_mid = make_worker(
        worker_id="worker-mid-tp",
        processing_speed_tps=40.0,
        ram_available_mb=2048.0,
        model_partition_required_mb=1024.0,
        current_workload_pct=0.0,
    )
    w_high = make_worker(
        worker_id="worker-high-tp",
        processing_speed_tps=120.0,
        ram_available_mb=2048.0,
        model_partition_required_mb=1024.0,
        current_workload_pct=0.0,
    )

    best, evals, failure = scheduler.select_best([w_low, w_mid, w_high], SchedulingObjective.HIGHEST_THROUGHPUT)

    assert failure is None
    assert best is not None
    assert best.worker_id == "worker-high-tp"

    max_eval_score = max(ev.score for ev in evals if ev.score is not None)
    assert best.score == max_eval_score


def test_score_components_populated(scheduler: DeterministicScheduler):
    """Score components dict is non-empty for feasible candidates."""
    worker = make_worker(worker_id="worker-components")

    for obj in SchedulingObjective:
        evaluation = scheduler.score_candidate(worker, obj)
        assert evaluation.is_feasible is True
        assert evaluation.score is not None
        assert isinstance(evaluation.score_components, dict)
        assert len(evaluation.score_components) > 0
        for comp_name, comp_value in evaluation.score_components.items():
            assert isinstance(comp_name, str)
            assert isinstance(comp_value, (int, float))


def test_all_four_objectives_work(scheduler: DeterministicScheduler):
    """Each SchedulingObjective produces a valid score for a feasible candidate."""
    worker = make_worker(worker_id="worker-all-objectives")
    expected_objectives = [
        SchedulingObjective.LOWEST_LATENCY,
        SchedulingObjective.HIGHEST_THROUGHPUT,
        SchedulingObjective.RESOURCE_EFFICIENCY,
        SchedulingObjective.BALANCED,
    ]

    for obj in expected_objectives:
        ev = scheduler.score_candidate(worker, obj)
        assert ev.is_feasible is True
        assert ev.score is not None
        assert isinstance(ev.score, float)
        assert ev.score > 0.0
        assert len(ev.rejection_reasons) == 0
        assert len(ev.score_components) > 0


def test_mixed_feasible_infeasible(scheduler: DeterministicScheduler):
    """Some candidates feasible, some not. Only feasible ones get scores. Selection is from feasible set."""
    feasible_1 = make_worker(worker_id="f-1", processing_speed_tps=20.0)
    feasible_2 = make_worker(worker_id="f-2", processing_speed_tps=80.0)
    infeasible_ram = make_worker(
        worker_id="inf-ram",
        ram_available_mb=512.0,
        model_partition_required_mb=2048.0,
    )
    infeasible_status = make_worker(
        worker_id="inf-status",
        status=WorkerStatus.UNAVAILABLE,
    )

    candidates = [infeasible_ram, feasible_1, infeasible_status, feasible_2]
    best, evals, failure = scheduler.select_best(candidates, SchedulingObjective.HIGHEST_THROUGHPUT)

    assert failure is None
    assert best is not None
    assert best.is_feasible is True
    assert best.worker_id == "f-2"
    assert best.score is not None

    eval_map = {ev.worker_id: ev for ev in evals}

    # Feasible candidates must have is_feasible=True, valid scores, no rejection reasons
    assert eval_map["f-1"].is_feasible is True
    assert eval_map["f-1"].score is not None
    assert len(eval_map["f-1"].rejection_reasons) == 0

    assert eval_map["f-2"].is_feasible is True
    assert eval_map["f-2"].score is not None
    assert len(eval_map["f-2"].rejection_reasons) == 0

    # Infeasible candidates must have is_feasible=False, score=None, populated rejection reasons
    assert eval_map["inf-ram"].is_feasible is False
    assert eval_map["inf-ram"].score is None
    assert len(eval_map["inf-ram"].rejection_reasons) > 0

    assert eval_map["inf-status"].is_feasible is False
    assert eval_map["inf-status"].score is None
    assert len(eval_map["inf-status"].rejection_reasons) > 0
