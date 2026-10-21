"""Scheduler and system benchmark suite routes."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..scenarios.loader import get_scenario, load_scenarios
from ..scheduler.engine import get_scheduling_engine
from ..scheduler.models import SchedulingMode, SchedulingObjective
from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/benchmarks", tags=["Benchmarks"])


class TriggerBenchmarkRequest(BaseModel):
    scenarios: Optional[list[str]] = None
    objectives: list[SchedulingObjective] = [
        SchedulingObjective.LOWEST_LATENCY,
        SchedulingObjective.HIGHEST_THROUGHPUT,
        SchedulingObjective.RESOURCE_EFFICIENCY,
        SchedulingObjective.BALANCED,
    ]
    modes: list[SchedulingMode] = [
        SchedulingMode.DETERMINISTIC,
        SchedulingMode.LAYA_SHADOW,
        SchedulingMode.LAYA_ASSISTED,
    ]


@router.get("")
def list_benchmark_runs(limit: int = Query(50, ge=1, le=500)):
    """Retrieve historical benchmark runs and aggregated performance stats."""
    db = get_database()
    runs = db.get_benchmark_runs(limit=limit)
    return runs


@router.post("")
def trigger_benchmark_suite(req: TriggerBenchmarkRequest):
    """
    Execute repeatable benchmark suite over specified scenarios, objectives, and modes.
    Saves full candidate evaluations and timing results to SQLite database.
    """
    engine = get_scheduling_engine()
    db = get_database()
    all_scenarios = load_scenarios()

    if req.scenarios:
        target_scenarios = [s for s in all_scenarios if s["name"] in req.scenarios]
    else:
        target_scenarios = all_scenarios

    if not target_scenarios:
        target_scenarios = all_scenarios[:2] if all_scenarios else []

    results: list[dict[str, Any]] = []
    total_runs = 0
    success_count = 0
    total_duration_ms = 0.0

    for sc in target_scenarios:
        for mode in req.modes:
            for obj in req.objectives:
                total_runs += 1
                res = engine.run(
                    candidates=sc["workers"],
                    objective=obj,
                    mode=mode,
                    scenario_name=sc["name"],
                )
                if res.success:
                    success_count += 1
                total_duration_ms += res.decision_duration_ms
                results.append(res.to_dict())

    avg_duration = round(total_duration_ms / max(total_runs, 1), 2)
    summary = {
        "total_runs": total_runs,
        "success_count": success_count,
        "failure_count": total_runs - success_count,
        "avg_decision_duration_ms": avg_duration,
        "results": results,
    }

    db.log_audit("BENCHMARK_SUITE", f"Executed benchmark suite ({total_runs} runs, {success_count} succeeded)")
    return summary
