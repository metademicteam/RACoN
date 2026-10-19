"""Deterministic scheduling engine with documented scoring rules."""
from __future__ import annotations

import logging
from typing import Optional

from racon.scheduler.models import (
    WorkerCandidate, CandidateEvaluation, SchedulingObjective,
    WorkerStatus, SchedulerFailure,
)

logger = logging.getLogger(__name__)


class DeterministicScheduler:
    """
    Rule-based scheduler with four objective functions.

    Scoring rules (all deterministic, higher is better):

    LOWEST_LATENCY:
        score = (1 / effective_latency) * availability_factor
        effective_latency = processing_time + network_latency
        processing_time = model_partition_mb / speed_factor
        speed_factor = max(processing_speed_tps, 1.0)
        availability_factor = (100 - current_workload_pct) / 100

    HIGHEST_THROUGHPUT:
        score = effective_throughput * availability_factor * memory_headroom
        effective_throughput = processing_speed_tps or 1.0
        memory_headroom = ram_available_mb / max(model_partition_required_mb, 1.0)

    RESOURCE_EFFICIENCY:
        score = (1 - resource_usage_ratio) * suitability
        resource_usage_ratio = model_partition_required_mb / ram_available_mb
        suitability = 1.0 if device matches, 0.5 otherwise

    BALANCED:
        score = 0.3 * norm_latency + 0.3 * norm_throughput + 0.2 * norm_efficiency + 0.2 * norm_availability
    """

    def validate_candidate(self, candidate: WorkerCandidate) -> CandidateEvaluation:
        """
        Validate a candidate against hard constraints.
        Returns an evaluation with is_feasible=False and reasons if rejected.
        """
        evaluation = CandidateEvaluation(
            worker_id=candidate.worker_id,
            is_feasible=True,
            is_simulated=candidate.is_simulated,
        )
        reasons: list[str] = []

        # Hard constraint 1: Worker must be available
        if candidate.status == WorkerStatus.UNAVAILABLE:
            reasons.append("Worker is unavailable")

        # Hard constraint 2: Worker must not be fully busy
        if candidate.status == WorkerStatus.BUSY and candidate.current_workload_pct >= 100:
            reasons.append("Worker is fully busy (workload >= 100%)")

        # Hard constraint 3: Sufficient RAM for model partition
        if candidate.ram_available_mb < candidate.model_partition_required_mb:
            reasons.append(
                f"Insufficient RAM: {candidate.ram_available_mb:.0f} MB available, "
                f"{candidate.model_partition_required_mb:.0f} MB required"
            )

        # Hard constraint 4: Compatible device type
        if candidate.compatible_runtimes and candidate.device_type not in candidate.compatible_runtimes:
            reasons.append(
                f"Incompatible runtime: worker has {candidate.device_type.value}, "
                f"requires one of {[r.value for r in candidate.compatible_runtimes]}"
            )

        # Hard constraint 5: Non-negative critical values
        if candidate.ram_total_mb <= 0:
            reasons.append("Invalid RAM total (must be > 0)")

        if candidate.model_partition_required_mb < 0:
            reasons.append("Invalid model partition requirement (must be >= 0)")

        if reasons:
            evaluation.is_feasible = False
            evaluation.rejection_reasons = reasons

        return evaluation

    def score_candidate(
        self, candidate: WorkerCandidate, objective: SchedulingObjective
    ) -> CandidateEvaluation:
        """
        Score a validated feasible candidate for the given objective.
        Candidate MUST have already passed validation.
        """
        evaluation = self.validate_candidate(candidate)
        if not evaluation.is_feasible:
            return evaluation

        if objective == SchedulingObjective.LOWEST_LATENCY:
            evaluation.score, evaluation.score_components = self._score_latency(candidate)
        elif objective == SchedulingObjective.HIGHEST_THROUGHPUT:
            evaluation.score, evaluation.score_components = self._score_throughput(candidate)
        elif objective == SchedulingObjective.RESOURCE_EFFICIENCY:
            evaluation.score, evaluation.score_components = self._score_efficiency(candidate)
        elif objective == SchedulingObjective.BALANCED:
            evaluation.score, evaluation.score_components = self._score_balanced(candidate)
        else:
            raise ValueError(f"Unknown objective: {objective}")

        return evaluation

    def select_best(
        self, candidates: list[WorkerCandidate], objective: SchedulingObjective
    ) -> tuple[Optional[CandidateEvaluation], list[CandidateEvaluation], Optional[SchedulerFailure]]:
        """
        Evaluate all candidates and select the best one.

        Returns:
            (best_evaluation, all_evaluations, failure_or_none)
        """
        if not candidates:
            failure = SchedulerFailure(
                reason="No candidates provided",
                details="The candidate list is empty",
                candidate_count=0,
                feasible_count=0,
            )
            return None, [], failure

        evaluations: list[CandidateEvaluation] = []
        for candidate in candidates:
            ev = self.score_candidate(candidate, objective)
            evaluations.append(ev)
            logger.info(
                "Candidate %s: feasible=%s score=%s reasons=%s",
                candidate.worker_id, ev.is_feasible, ev.score, ev.rejection_reasons,
            )

        feasible = [ev for ev in evaluations if ev.is_feasible and ev.score is not None]

        if not feasible:
            failure = SchedulerFailure(
                reason="No feasible candidates",
                details="All candidates were rejected by hard constraints",
                candidate_count=len(candidates),
                feasible_count=0,
            )
            return None, evaluations, failure

        # Select highest score; deterministic tie-breaking by worker_id
        best = max(feasible, key=lambda ev: (ev.score, ev.worker_id))
        logger.info(
            "Deterministic selection: %s (score=%.4f, objective=%s)",
            best.worker_id, best.score, objective.value,
        )
        return best, evaluations, None

    # --- Scoring functions ---

    def _score_latency(self, c: WorkerCandidate) -> tuple[float, dict[str, float]]:
        speed_factor = max(c.processing_speed_tps or 1.0, 1.0)
        processing_time = c.model_partition_required_mb / speed_factor
        network_lat = c.network_latency_ms or 0.0
        effective_latency = processing_time + network_lat
        availability = (100.0 - c.current_workload_pct) / 100.0
        score = (1.0 / max(effective_latency, 0.001)) * availability
        components = {
            "speed_factor": speed_factor,
            "processing_time": processing_time,
            "network_latency_ms": network_lat,
            "effective_latency": effective_latency,
            "availability_factor": availability,
        }
        return score, components

    def _score_throughput(self, c: WorkerCandidate) -> tuple[float, dict[str, float]]:
        effective_throughput = c.processing_speed_tps or 1.0
        availability = (100.0 - c.current_workload_pct) / 100.0
        memory_headroom = c.ram_available_mb / max(c.model_partition_required_mb, 1.0)
        score = effective_throughput * availability * min(memory_headroom, 10.0)
        components = {
            "effective_throughput": effective_throughput,
            "availability_factor": availability,
            "memory_headroom": memory_headroom,
        }
        return score, components

    def _score_efficiency(self, c: WorkerCandidate) -> tuple[float, dict[str, float]]:
        usage_ratio = c.model_partition_required_mb / max(c.ram_available_mb, 1.0)
        # Prefer workers where the model fits well without wasting too much
        efficiency = 1.0 - min(usage_ratio, 1.0)
        suitability = 1.0  # Base suitability
        score = efficiency * suitability
        components = {
            "resource_usage_ratio": usage_ratio,
            "efficiency": efficiency,
            "suitability": suitability,
        }
        return score, components

    def _score_balanced(self, c: WorkerCandidate) -> tuple[float, dict[str, float]]:
        _, lat_components = self._score_latency(c)
        _, tp_components = self._score_throughput(c)
        _, eff_components = self._score_efficiency(c)

        # Normalize each component to [0, 1] range using simple heuristics
        lat_score = min(1.0 / max(lat_components["effective_latency"], 0.001), 1.0)
        tp_score = min(tp_components["effective_throughput"] / 100.0, 1.0)
        eff_score = eff_components["efficiency"]
        avail_score = lat_components["availability_factor"]

        score = 0.3 * lat_score + 0.3 * tp_score + 0.2 * eff_score + 0.2 * avail_score
        components = {
            "latency_component": lat_score,
            "throughput_component": tp_score,
            "efficiency_component": eff_score,
            "availability_component": avail_score,
        }
        return score, components
