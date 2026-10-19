"""Tests for the Laya adapter."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from racon.scheduler.laya_adapter import LayaAdapter, LayaDecision, LayaStatus


def test_initial_status_uninitialized():
    """1. New adapter starts with UNINITIALIZED status."""
    adapter = LayaAdapter()
    assert adapter.status == LayaStatus.UNINITIALIZED
    assert adapter.is_ready is False
    assert adapter.error_message is None
    assert adapter.version is None


def test_missing_laya_package_handled():
    """2. When import laya raises ImportError, check_availability returns False and status is UNAVAILABLE."""
    adapter = LayaAdapter()
    with patch.dict(sys.modules, {"laya": None}):
        result = adapter.check_availability()
        assert result is False
        assert adapter.status == LayaStatus.UNAVAILABLE
        assert adapter.error_message is not None
        assert "not installed" in adapter.error_message


def test_initialization_error_handled():
    """3. When Router() raises an exception, status becomes FAILED with error message."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_laya.Router.side_effect = RuntimeError("Checkpoint download failed")

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        result = adapter.initialize()
        assert result is False
        assert adapter.status == LayaStatus.FAILED
        assert adapter.is_ready is False
        assert adapter.error_message is not None
        assert "Checkpoint download failed" in adapter.error_message


def test_successful_initialization():
    """4. Mock laya.Router, verify status becomes READY."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_router = MagicMock()
    mock_laya.Router.return_value = mock_router

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        result = adapter.initialize()
        assert result is True
        assert adapter.status == LayaStatus.READY
        assert adapter.is_ready is True
        assert adapter.version == "0.4.0"
        mock_laya.Router.assert_called_once_with(max_loaded=1)


def test_invalid_candidate_id_rejected():
    """5. When Laya returns a choice not in candidate_ids, the decision is marked unsuccessful."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_router = MagicMock()
    mock_router.predict.return_value = {
        "answers": {
            "best_worker": {
                "choice": "WORKER-INVALID",
                "confidence": 0.95,
            }
        },
        "routing": {"model": "english"},
    }
    mock_laya.Router.return_value = mock_router

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        assert adapter.initialize() is True

        decision = adapter.decide(
            candidate_ids=["WORKER-A", "WORKER-B"],
            candidate_descriptions={"WORKER-A": "Node A", "WORKER-B": "Node B"},
            scenario_context="Select worker with lowest latency",
        )

        assert decision.success is False
        assert decision.selected_id is None
        assert decision.error is not None
        assert "invalid choice" in decision.error
        mock_router.predict.assert_called_once()


def test_single_candidate_shortcut():
    """6. When only 1 candidate, adapter returns it immediately without calling Router."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_router = MagicMock()
    mock_laya.Router.return_value = mock_router

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        assert adapter.initialize() is True

        decision = adapter.decide(
            candidate_ids=["WORKER-SOLO"],
            candidate_descriptions={"WORKER-SOLO": "Solo worker"},
            scenario_context="Context scenario",
        )

        assert decision.success is True
        assert decision.selected_id == "WORKER-SOLO"
        assert decision.confidence == 1.0
        mock_router.predict.assert_not_called()


def test_empty_candidates_error():
    """7. Empty candidate list returns unsuccessful decision."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_router = MagicMock()
    mock_laya.Router.return_value = mock_router

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        assert adapter.initialize() is True

        decision = adapter.decide(
            candidate_ids=[],
            candidate_descriptions={},
            scenario_context="Empty candidates context",
        )

        assert decision.success is False
        assert decision.selected_id is None
        assert decision.error is not None
        assert "no candidates" in decision.error.lower()
        mock_router.predict.assert_not_called()


def test_decide_when_not_ready():
    """8. Calling decide() when not initialized returns unsuccessful with status info."""
    adapter = LayaAdapter()
    assert adapter.status == LayaStatus.UNINITIALIZED

    decision = adapter.decide(
        candidate_ids=["WORKER-A", "WORKER-B"],
        candidate_descriptions={"WORKER-A": "Node A", "WORKER-B": "Node B"},
        scenario_context="Context",
    )

    assert decision.success is False
    assert decision.selected_id is None
    assert decision.error is not None
    assert "not ready" in decision.error.lower()
    assert "uninitialized" in decision.error.lower()


def test_decide_exception_handled():
    """9. When predict() raises exception, returns unsuccessful decision with error."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_router = MagicMock()
    mock_router.predict.side_effect = RuntimeError("Inference engine timed out")
    mock_laya.Router.return_value = mock_router

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        assert adapter.initialize() is True

        decision = adapter.decide(
            candidate_ids=["WORKER-A", "WORKER-B"],
            candidate_descriptions={"WORKER-A": "Node A", "WORKER-B": "Node B"},
            scenario_context="Test scenario",
        )

        assert decision.success is False
        assert decision.selected_id is None
        assert decision.error is not None
        assert "Inference engine timed out" in decision.error


def test_successful_decision():
    """10. Mock predict() to return valid choice, verify decision contains correct id and confidence."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_router = MagicMock()
    mock_router.predict.return_value = {
        "answers": {
            "best_worker": {
                "choice": "WORKER-A",
                "confidence": 0.85,
            }
        },
        "routing": {"model": "english"},
    }
    mock_laya.Router.return_value = mock_router

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        assert adapter.initialize() is True

        decision = adapter.decide(
            candidate_ids=["WORKER-A", "WORKER-B"],
            candidate_descriptions={
                "WORKER-A": "Fast GPU worker",
                "WORKER-B": "Standard CPU worker",
            },
            scenario_context="High throughput required",
        )

        assert decision.success is True
        assert decision.selected_id == "WORKER-A"
        assert decision.confidence == pytest.approx(0.85)
        assert decision.error is None
        assert decision.duration_ms >= 0.0

        mock_router.predict.assert_called_once()
        args, _ = mock_router.predict.call_args
        state_arg, questions_arg = args[0], args[1]
        assert "High throughput required" in state_arg
        assert "best_worker" in questions_arg
        assert questions_arg["best_worker"]["type"] == "choice"
        assert questions_arg["best_worker"]["criteria"]["WORKER-A"] == "Fast GPU worker"
        assert questions_arg["best_worker"]["criteria"]["WORKER-B"] == "Standard CPU worker"


def test_shutdown_resets_status():
    """11. After shutdown(), status returns to UNINITIALIZED."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.0"
    mock_router = MagicMock()
    mock_laya.Router.return_value = mock_router

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        assert adapter.initialize() is True
        assert adapter.status == LayaStatus.READY

        adapter.shutdown()

        assert adapter.status == LayaStatus.UNINITIALIZED
        assert adapter.is_ready is False
        assert adapter.error_message is None
        mock_router.unload.assert_called_once()


def test_check_availability_with_laya():
    """12. When laya is importable, returns True and captures version."""
    mock_laya = MagicMock()
    mock_laya.__version__ = "0.4.2"

    with patch.dict(sys.modules, {"laya": mock_laya}):
        adapter = LayaAdapter()
        result = adapter.check_availability()
        assert result is True
        assert adapter.version == "0.4.2"
