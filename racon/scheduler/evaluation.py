"""Scheduling engine that coordinates deterministic and Laya-assisted scheduling."""
from __future__ import annotations

import logging
import time
from typing import Optional

from racon.scheduler.models import (
    WorkerCandidate, CandidateEvaluation, SchedulerResult,
    SchedulingObjective, SchedulingMode,
)
from racon.scheduler.deterministic import DeterministicScheduler
from racon.scheduler.laya_adapter import LayaAdapter, LayaStatus

logger = logging.getLogger(__name__)


class SchedulingEngine:
    """
    Coordinates scheduling across three modes:
    - Deterministic: pure rule-based scheduling
    - Laya Shadow: deterministic decides, Laya recommendation recorded separately
    - Laya Assisted: Laya selects from feasible candidates, with deterministic fallback
    """

    def __init__(self, laya_adapter: Optional[LayaAdapter] = None):
        self.deterministic = DeterministicScheduler()
        self.laya = laya_adapter or LayaAdapter()

    def run(
        self,
        candidates: list[WorkerCandidate],
        objective: SchedulingObjective,
        mode: SchedulingMode,
        scenario_name: str = "",
    ) -> SchedulerResult:
        """
        Execute a complete scheduling run.

        Steps:
        1. Validate all candidates
        2. Score feasible candidates
        3. Apply the selected mode (deterministic, shadow, assisted)
        4. Validate the final selection
        5. Record the complete result
        """
        start_time = time.monotonic()
        result = SchedulerResult(
            scenario_name=scenario_name,
            objective=objective,
            mode=mode,
            candidate_count=len(candidates),
        )

        logger.info(
            "Scheduling run: scenario=%s objective=%s mode=%s candidates=%d",
            scenario_name, objective.value, mode.value, len(candidates),
        )

        # Step 1-2: Deterministic evaluation
        best_det, evaluations, failure = self.deterministic.select_best(candidates, objective)
        result.evaluations = evaluations
        result.feasible_count = sum(1 for ev in evaluations if ev.is_feasible)

        if failure:
            result.success = False
            result.failure_reason = failure.reason
            result.decision_duration_ms = (time.monotonic() - start_time) * 1000
            logger.warning("No feasible candidates: %s", failure.reason)
            return result

        assert best_det is not None  # guaranteed by no failure
        result.deterministic_choice = best_det.worker_id
        result.deterministic_score = best_det.score

        # Step 3: Apply mode
        if mode == SchedulingMode.DETERMINISTIC:
            result.final_choice = best_det.worker_id
            result.final_score = best_det.score
            result.success = True

        elif mode == SchedulingMode.LAYA_SHADOW:
            # Deterministic decides; Laya provides advisory recommendation
            result.final_choice = best_det.worker_id
            result.final_score = best_det.score
            result.success = True

            # Get Laya recommendation (does NOT change the result)
            self._get_laya_recommendation(result, candidates, evaluations, scenario_name)

        elif mode == SchedulingMode.LAYA_ASSISTED:
            # Laya selects from feasible candidates
            laya_decided = self._laya_assisted_selection(
                result, candidates, evaluations, scenario_name, objective
            )
            if not laya_decided:
                # Fallback to deterministic
                result.final_choice = best_det.worker_id
                result.final_score = best_det.score
                result.fallback_used = True
                if not result.fallback_reason:
                    result.fallback_reason = "Laya decision unavailable, using deterministic fallback"
                result.success = True

        result.decision_duration_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "Scheduling complete: final=%s score=%s duration=%.1fms laya_used=%s fallback=%s",
            result.final_choice, result.final_score,
            result.decision_duration_ms, result.laya_used_successfully, result.fallback_used,
        )
        return result

    def _get_laya_recommendation(
        self,
        result: SchedulerResult,
        candidates: list[WorkerCandidate],
        evaluations: list[CandidateEvaluation],
        scenario_name: str,
    ):
        """Get Laya's recommendation without changing the final decision."""
        if not self.laya.is_ready:
            result.fallback_reason = f"Laya not ready (status: {self.laya.status.value})"
            return

        feasible_ids = [ev.worker_id for ev in evaluations if ev.is_feasible]
        descriptions = self._build_candidate_descriptions(candidates, evaluations)
        context = self._build_scenario_context(scenario_name, result.objective)

        decision = self.laya.decide(feasible_ids, descriptions, context)
        if decision.success:
            result.laya_recommendation = decision.selected_id
            result.laya_confidence = decision.confidence
            result.laya_checkpoint = decision.checkpoint_name
            result.laya_used_successfully = True
        else:
            result.fallback_reason = decision.error

    def _laya_assisted_selection(
        self,
        result: SchedulerResult,
        candidates: list[WorkerCandidate],
        evaluations: list[CandidateEvaluation],
        scenario_name: str,
        objective: SchedulingObjective,
    ) -> bool:
        """Ask Laya to select from feasible candidates. Returns True if Laya made a valid selection."""
        if not self.laya.is_ready:
            result.fallback_reason = f"Laya not ready (status: {self.laya.status.value})"
            return False

        feasible_ids = [ev.worker_id for ev in evaluations if ev.is_feasible]
        descriptions = self._build_candidate_descriptions(candidates, evaluations)
        context = self._build_scenario_context(scenario_name, objective)

        decision = self.laya.decide(feasible_ids, descriptions, context)
        if not decision.success:
            result.fallback_reason = decision.error
            return False

        # Validate: Laya's choice must be in the feasible set
        if decision.selected_id not in feasible_ids:
            result.fallback_reason = (
                f"Laya selected invalid candidate '{decision.selected_id}' "
                f"(not in feasible set: {feasible_ids})"
            )
            return False

        # Find the score for the selected candidate
        selected_eval = next(
            (ev for ev in evaluations if ev.worker_id == decision.selected_id), None
        )

        result.laya_recommendation = decision.selected_id
        result.laya_confidence = decision.confidence
        result.laya_checkpoint = decision.checkpoint_name
        result.laya_used_successfully = True
        result.final_choice = decision.selected_id
        result.final_score = selected_eval.score if selected_eval else None
        result.success = True
        return True

    def _build_candidate_descriptions(
        self, candidates: list[WorkerCandidate], evaluations: list[CandidateEvaluation]
    ) -> dict[str, str]:
        """Build human-readable descriptions for Laya."""
        descriptions = {}
        for c in candidates:
            ev = next((e for e in evaluations if e.worker_id == c.worker_id), None)
            if ev and ev.is_feasible:
                parts = [
                    f"{'Simulated' if c.is_simulated else 'Live'} worker",
                    f"Device: {c.device_type.value}",
                    f"RAM: {c.ram_available_mb:.0f}/{c.ram_total_mb:.0f} MB",
                    f"Speed: {c.processing_speed_tps or 'unmeasured'} tokens/s",
                    f"Workload: {c.current_workload_pct:.0f}%",
                ]
                if c.network_latency_ms is not None:
                    parts.append(f"Latency: {c.network_latency_ms:.0f} ms")
                if ev.score is not None:
                    parts.append(f"Score: {ev.score:.4f}")
                descriptions[c.worker_id] = "; ".join(parts)
        return descriptions

    def _build_scenario_context(self, scenario_name: str, objective: SchedulingObjective) -> str:
        return (
            f"Scenario: {scenario_name}. "
            f"Objective: {objective.value.replace('_', ' ')}. "
            f"Select the worker best suited for this workload considering "
            f"resource availability, processing capability, and constraints."
        )
