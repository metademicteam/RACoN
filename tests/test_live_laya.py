"""Opt-in live Laya decision smoke tests.

Fulfills Action Item 5 and Gate 1 of RACoN Detailed Architecture.
These tests exercise the actual installed Laya package (v0.4.12)
with real Router instantiation and decision validation.
"""
from __future__ import annotations

import pytest

from racon.scheduler.laya_adapter import LayaAdapter, LayaStatus
from racon.scheduler.models import (
    WorkerCandidate,
    SchedulingObjective,
    SchedulingMode,
    WorkerStatus,
    DeviceType,
)
from racon.scheduler.evaluation import SchedulingEngine


@pytest.mark.live_laya
def test_live_laya_package_availability():
    """Verify that the installed laya package is discovered and reports a valid version."""
    adapter = LayaAdapter()
    available = adapter.check_availability()
    assert available is True
    assert adapter.version is not None
    assert adapter.version != "unknown"


@pytest.mark.live_laya
def test_live_laya_router_initialization_and_shutdown():
    """Verify that Router(max_loaded=1) initializes cleanly and shuts down without leaking resources."""
    adapter = LayaAdapter()
    success = adapter.initialize()
    if not success:
        pytest.skip(f"Laya checkpoint unavailable in offline environment: {adapter.error_message}")

    assert adapter.is_ready is True
    assert adapter.status == LayaStatus.READY

    # Clean shutdown
    adapter.shutdown()
    assert adapter.is_ready is False
    assert adapter.status == LayaStatus.UNINITIALIZED


@pytest.mark.live_laya
def test_live_laya_decision_pipeline():
    """Verify that Laya selects among valid candidate IDs and returns checkpoint metadata."""
    adapter = LayaAdapter()
    if not adapter.initialize():
        pytest.skip("Laya checkpoint download not possible in current environment")

    candidate_ids = ["worker-cpu-node", "worker-fast-compute"]
    candidate_descriptions = {
        "worker-cpu-node": "CPU worker, 8192 MB available RAM, 20 tokens/s, 5ms latency",
        "worker-fast-compute": "High throughput worker, 12288 MB available RAM, 65 tokens/s, 2ms latency",
    }

    decision = adapter.decide(
        candidate_ids=candidate_ids,
        candidate_descriptions=candidate_descriptions,
        scenario_context="Test batch inference job requiring high throughput",
    )

    try:
        assert decision.success is True
        assert decision.selected_id in candidate_ids
        assert decision.duration_ms >= 0
        assert decision.checkpoint_name is not None
    finally:
        adapter.shutdown()
