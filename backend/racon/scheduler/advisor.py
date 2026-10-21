"""Scheduler Advisor interface and concrete implementations."""
from __future__ import annotations

import abc
import logging
from typing import Optional

from .models import (
    WorkerCandidate,
    CandidateEvaluation,
    SchedulingObjective,
    SchedulingMode,
)
from .deterministic import DeterministicScheduler
from .laya_adapter import LayaAdapter, get_laya_adapter

logger = logging.getLogger(__name__)


class SchedulerAdvisor(abc.ABC):
    """Abstract interface for scheduler advisors."""

    @property
    @abc.abstractmethod
    def mode(self) -> SchedulingMode:
        """Return the scheduling mode supported by this advisor."""
        raise NotImplementedError

    @abc.abstractmethod
    def advise(
        self,
        candidates: list[WorkerCandidate],
        evaluations: list[CandidateEvaluation],
        objective: SchedulingObjective,
        scenario_name: str = "",
    ) -> tuple[Optional[str], Optional[float], bool, Optional[str]]:
        """
        Evaluate candidates and return:
        (chosen_worker_id, confidence_or_score, success, fallback_or_error_reason)
        """
        raise NotImplementedError


class DeterministicSchedulerAdvisor(SchedulerAdvisor):
    """Advisor using strictly deterministic rule-based multi-objective scoring."""

    def __init__(self, scheduler: Optional[DeterministicScheduler] = None):
        self.scheduler = scheduler or DeterministicScheduler()

    @property
    def mode(self) -> SchedulingMode:
        return SchedulingMode.DETERMINISTIC

    def advise(
        self,
        candidates: list[WorkerCandidate],
        evaluations: list[CandidateEvaluation],
        objective: SchedulingObjective,
        scenario_name: str = "",
    ) -> tuple[Optional[str], Optional[float], bool, Optional[str]]:
        feasible = [ev for ev in evaluations if ev.is_feasible and ev.score is not None]
        if not feasible:
            return None, None, False, "No feasible candidates passed hard constraints"
        best = max(feasible, key=lambda ev: (ev.score if ev.score is not None else -1.0, ev.worker_id))
        return best.worker_id, best.score, True, None


class LayaSchedulerAdvisor(SchedulerAdvisor):
    """Advisor querying Laya's Router.predict typed choice question with safety validation."""

    def __init__(
        self,
        adapter: Optional[LayaAdapter] = None,
        deterministic: Optional[DeterministicScheduler] = None,
    ):
        self.adapter = adapter or get_laya_adapter()
        self.deterministic = deterministic or DeterministicScheduler()

    @property
    def mode(self) -> SchedulingMode:
        return SchedulingMode.LAYA_ASSISTED

    def advise(
        self,
        candidates: list[WorkerCandidate],
        evaluations: list[CandidateEvaluation],
        objective: SchedulingObjective,
        scenario_name: str = "",
    ) -> tuple[Optional[str], Optional[float], bool, Optional[str]]:
        feasible_ids = [ev.worker_id for ev in evaluations if ev.is_feasible]
        if not feasible_ids:
            return None, None, False, "No feasible candidates to evaluate"

        if not self.adapter.is_ready:
            return None, None, False, f"Laya not ready (status: {self.adapter.status.value})"

        descriptions = {}
        for c in candidates:
            if c.worker_id in feasible_ids:
                dev = c.device_type.value if hasattr(c.device_type, "value") else str(c.device_type)
                descriptions[c.worker_id] = (
                    f"Worker {c.worker_id}: Device {dev}, RAM {c.ram_available_mb:.0f}/{c.ram_total_mb:.0f}MB, "
                    f"Speed {c.processing_speed_tps or 'unmeasured'} tps, Load {c.current_workload_pct:.0f}%"
                )

        obj_name = objective.value if hasattr(objective, "value") else str(objective)
        context = f"Scenario: {scenario_name or 'Default'}. Objective: {obj_name}."
        dec = self.adapter.decide(feasible_ids, descriptions, context)
        if dec.success and dec.selected_id in feasible_ids:
            ev = next((e for e in evaluations if e.worker_id == dec.selected_id), None)
            score = ev.score if ev and ev.score is not None else dec.confidence
            return dec.selected_id, score, True, None

        error_msg = dec.error or f"Laya returned candidate '{dec.selected_id}' not in feasible set"
        return None, None, False, error_msg


class ShadowSchedulerAdvisor(SchedulerAdvisor):
    """
    Shadow mode advisor:
    Deterministic placement executes as authoritative decision, while Laya recommendation is
    computed in parallel for audit and comparison.
    """

    def __init__(
        self,
        deterministic: Optional[DeterministicScheduler] = None,
        adapter: Optional[LayaAdapter] = None,
    ):
        self.deterministic = deterministic or DeterministicScheduler()
        self.adapter = adapter or get_laya_adapter()
        self.det_advisor = DeterministicSchedulerAdvisor(self.deterministic)
        self.laya_advisor = LayaSchedulerAdvisor(self.adapter, self.deterministic)

    @property
    def mode(self) -> SchedulingMode:
        return SchedulingMode.LAYA_SHADOW

    def advise(
        self,
        candidates: list[WorkerCandidate],
        evaluations: list[CandidateEvaluation],
        objective: SchedulingObjective,
        scenario_name: str = "",
    ) -> tuple[Optional[str], Optional[float], bool, Optional[str]]:
        # Deterministic choice executes
        return self.det_advisor.advise(candidates, evaluations, objective, scenario_name)

    def get_shadow_recommendation(
        self,
        candidates: list[WorkerCandidate],
        evaluations: list[CandidateEvaluation],
        objective: SchedulingObjective,
        scenario_name: str = "",
    ) -> tuple[Optional[str], Optional[float], bool, Optional[str]]:
        """Query Laya recommendation in shadow mode."""
        return self.laya_advisor.advise(candidates, evaluations, objective, scenario_name)
