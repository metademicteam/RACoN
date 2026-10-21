"""Scheduler configuration and placement preview routes."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..scenarios.loader import get_scenario, load_scenarios
from ..scheduler.engine import get_scheduling_engine
from ..scheduler.models import (
    SchedulerResult,
    SchedulingMode,
    SchedulingObjective,
    WorkerCandidate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/scheduler", tags=["Scheduler"])


class SchedulerConfigUpdate(BaseModel):
    default_objective: Optional[SchedulingObjective] = None
    default_mode: Optional[SchedulingMode] = None
    auto_fallback: Optional[bool] = None


class SchedulerPreviewRequest(BaseModel):
    scenario_name: Optional[str] = "Multiple Feasible Candidates"
    objective: Optional[SchedulingObjective] = SchedulingObjective.BALANCED
    mode: Optional[SchedulingMode] = SchedulingMode.DETERMINISTIC
    candidates: Optional[list[WorkerCandidate]] = None


@router.get("/config")
def get_scheduler_configuration():
    """Retrieve current scheduling engine mode, default objective, and Laya status."""
    engine = get_scheduling_engine()
    return engine.get_config()


@router.put("/config")
def update_scheduler_configuration(update: SchedulerConfigUpdate):
    """Update active scheduling defaults (objective, mode, fallback policy)."""
    engine = get_scheduling_engine()
    updated = engine.update_config(
        default_objective=update.default_objective,
        default_mode=update.default_mode,
        auto_fallback=update.auto_fallback,
    )
    return {"status": "updated", "config": updated}


@router.post("/preview", response_model=SchedulerResult)
def preview_scheduler_decision(req: SchedulerPreviewRequest):
    """
    Run placement evaluation over a selected predefined scenario or custom candidates.
    Returns complete candidate evaluations, scores, deterministic choice, and Laya recommendation.
    """
    engine = get_scheduling_engine()
    candidates: list[WorkerCandidate] = []
    scenario_title = req.scenario_name or "Custom Scenario"

    if req.candidates:
        candidates = req.candidates
    elif req.scenario_name:
        sc = get_scenario(req.scenario_name)
        if not sc:
            all_scs = load_scenarios()
            if all_scs:
                sc = all_scs[0]
                scenario_title = sc["name"]
        if sc:
            candidates = sc["workers"]

    if not candidates:
        raise HTTPException(
            status_code=400,
            detail=f"Could not load candidates for scenario '{req.scenario_name}'",
        )

    result = engine.run(
        candidates=candidates,
        objective=req.objective or engine.default_objective,
        mode=req.mode or engine.default_mode,
        scenario_name=scenario_title,
    )

    return result
