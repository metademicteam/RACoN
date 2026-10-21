"""Central SchedulingEngine coordinating deterministic, shadow, and assisted scheduling modes."""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Optional

from .models import (
    WorkerCandidate,
    SchedulerResult,
    SchedulingObjective,
    SchedulingMode,
)
from .deterministic import DeterministicScheduler
from .laya_adapter import LayaAdapter, get_laya_adapter
from .advisor import (
    SchedulerAdvisor,
    DeterministicSchedulerAdvisor,
    ShadowSchedulerAdvisor,
    LayaSchedulerAdvisor,
)
from ..storage.db import Database, get_database

logger = logging.getLogger(__name__)


class SchedulingEngine:
    """
    Central coordinator for RACoN scheduling decisions.

    Coordinates among:
    - Deterministic mode (Rule-based multi-objective optimization)
    - Laya Shadow mode (Deterministic execution + background Laya recommendation)
    - Laya Assisted mode (Laya typed choice selection + deterministic safety fallback)

    Maintains a full in-memory audit trail and automatically logs runs to the database.
    """

    def __init__(
        self,
        deterministic: Optional[DeterministicScheduler] = None,
        laya_adapter: Optional[LayaAdapter] = None,
        database: Optional[Database] = None,
        max_audit_records: int = 200,
    ):
        self.deterministic = deterministic or DeterministicScheduler()
        self.laya = laya_adapter or get_laya_adapter()
        self.db = database or get_database()

        # Mode advisors
        self._deterministic_advisor = DeterministicSchedulerAdvisor(self.deterministic)
        self._shadow_advisor = ShadowSchedulerAdvisor(self.deterministic, self.laya)
        self._assisted_advisor = LayaSchedulerAdvisor(self.laya, self.deterministic)

        # Config state
        self.default_objective: SchedulingObjective = SchedulingObjective.BALANCED
        self.default_mode: SchedulingMode = SchedulingMode.DETERMINISTIC
        self.auto_fallback: bool = True

        # In-memory audit trail (ring buffer)
        self._audit_trail: deque[SchedulerResult] = deque(maxlen=max_audit_records)

    def advisor_for_mode(self, mode: SchedulingMode) -> SchedulerAdvisor:
        """Get the advisor responsible for the specified mode."""
        if mode == SchedulingMode.DETERMINISTIC:
            return self._deterministic_advisor
        elif mode == SchedulingMode.LAYA_SHADOW:
            return self._shadow_advisor
        elif mode == SchedulingMode.LAYA_ASSISTED:
            return self._assisted_advisor
        raise ValueError(f"Unsupported scheduling mode: {mode}")

    def run(
        self,
        candidates: list[WorkerCandidate],
        objective: Optional[SchedulingObjective] = None,
        mode: Optional[SchedulingMode] = None,
        scenario_name: str = "",
    ) -> SchedulerResult:
        """
        Execute a complete scheduling run across the chosen mode and objective.

        1. Validates candidates against hard constraints and computes baseline scores.
        2. Applies the mode policy (deterministic, shadow, or assisted).
        3. Enforces deterministic fallback if assisted selection fails or is unavailable.
        4. Records decision to in-memory audit buffer and persists to SQLite database.
        """
        start_time = time.monotonic()
        target_objective = objective or self.default_objective
        target_mode = mode or self.default_mode

        result = SchedulerResult(
            scenario_name=scenario_name,
            objective=target_objective,
            mode=target_mode,
            candidate_count=len(candidates),
        )

        # 1. Deterministic evaluation for constraint validation
        best_det, evaluations, failure = self.deterministic.select_best(candidates, target_objective)
        result.evaluations = evaluations
        result.feasible_count = sum(1 for ev in evaluations if ev.is_feasible)

        if failure or best_det is None:
            result.success = False
            result.failure_reason = failure.reason if failure else "No candidate selected"
            result.decision_duration_ms = (time.monotonic() - start_time) * 1000
            self._audit_trail.append(result)
            return result

        result.deterministic_choice = best_det.worker_id
        result.deterministic_score = best_det.score

        # 2. Apply Mode Policy
        if target_mode == SchedulingMode.DETERMINISTIC:
            choice, score, success, err = self._deterministic_advisor.advise(
                candidates, evaluations, target_objective, scenario_name
            )
            result.final_choice = choice
            result.final_score = score
            result.success = success
            result.failure_reason = err

        elif target_mode == SchedulingMode.LAYA_SHADOW:
            # Deterministic placement executes
            result.final_choice = best_det.worker_id
            result.final_score = best_det.score
            result.success = True

            # Parallel / background Laya advisory recommendation
            rec_id, conf, ok, err = self._shadow_advisor.get_shadow_recommendation(
                candidates, evaluations, target_objective, scenario_name
            )
            if ok:
                result.laya_recommendation = rec_id
                result.laya_confidence = conf
                result.laya_used_successfully = True
            else:
                result.fallback_reason = err

        elif target_mode == SchedulingMode.LAYA_ASSISTED:
            choice, score, ok, err = self._assisted_advisor.advise(
                candidates, evaluations, target_objective, scenario_name
            )
            if ok and choice is not None:
                result.final_choice = choice
                result.final_score = score
                result.laya_recommendation = choice
                result.laya_confidence = score
                result.laya_used_successfully = True
                result.success = True
            else:
                # Deterministic fallback
                result.final_choice = best_det.worker_id
                result.final_score = best_det.score
                result.fallback_used = True
                result.fallback_reason = err or "Laya decision unavailable, deterministic fallback applied"
                result.success = True

        result.decision_duration_ms = (time.monotonic() - start_time) * 1000

        # 3. Store in in-memory audit ring buffer
        self._audit_trail.append(result)

        # 4. Persist to database
        try:
            eval_dicts = [ev.to_dict() for ev in result.evaluations]
            self.db.save_benchmark_run(result.to_dict(), eval_dicts)
            self.db.log_audit(
                event_type="SCHEDULER_RUN",
                level="INFO" if result.success else "WARN",
                message=f"Scheduled {result.final_choice or 'NONE'} for scenario '{scenario_name}'",
                details=f"objective={result.objective.value}, mode={result.mode.value}, duration={result.decision_duration_ms:.1f}ms",
            )
        except Exception as e:
            logger.warning("Failed to persist scheduling run to database: %s", e)

        return result

    def get_history(self, limit: int = 50) -> list[SchedulerResult]:
        """Get recent scheduling results from audit buffer."""
        items = list(self._audit_trail)
        items.reverse()
        return items[:limit]

    def get_audit_trail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get audit trail as serialized dictionary list."""
        return [r.to_dict() for r in self.get_history(limit)]

    def clear_history(self) -> None:
        """Clear in-memory audit trail."""
        self._audit_trail.clear()

    def get_config(self) -> dict[str, Any]:
        """Return current engine configuration and Laya status."""
        return {
            "default_objective": self.default_objective.value if hasattr(self.default_objective, "value") else str(self.default_objective),
            "default_mode": self.default_mode.value if hasattr(self.default_mode, "value") else str(self.default_mode),
            "auto_fallback": self.auto_fallback,
            "laya_status": self.laya.status.value if hasattr(self.laya.status, "value") else str(self.laya.status),
            "laya_version": self.laya.version,
            "laya_ready": self.laya.is_ready,
        }

    def update_config(
        self,
        default_objective: Optional[SchedulingObjective] = None,
        default_mode: Optional[SchedulingMode] = None,
        auto_fallback: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Update scheduler engine configuration."""
        if default_objective is not None:
            self.default_objective = default_objective
        if default_mode is not None:
            self.default_mode = default_mode
        if auto_fallback is not None:
            self.auto_fallback = auto_fallback

        return self.get_config()


_GLOBAL_ENGINE: Optional[SchedulingEngine] = None


def get_scheduling_engine() -> SchedulingEngine:
    """Retrieve singleton SchedulingEngine instance."""
    global _GLOBAL_ENGINE
    if _GLOBAL_ENGINE is None:
        _GLOBAL_ENGINE = SchedulingEngine()
    return _GLOBAL_ENGINE
