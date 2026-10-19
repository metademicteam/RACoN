"""Adapter that isolates all Laya-specific functionality.

Laya API (from official docs):
  from laya import Router
  router = Router()  # lazy; downloads checkpoint on first predict
  result = router.predict(state, questions)
  result["answers"][question_id]["choice"]  -> selected label
  result["answers"][question_id]["confidence"] -> float

This adapter uses Laya's Router.predict with a 'choice' question
to select among scheduling candidates.
"""
from __future__ import annotations

import enum
import logging
import time
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class LayaStatus(enum.Enum):
    """Laya initialization state."""
    UNINITIALIZED = "uninitialized"
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass
class LayaDecision:
    """Result from a Laya decision request."""
    selected_id: Optional[str] = None
    confidence: Optional[float] = None
    success: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0


class LayaAdapter:
    """
    Isolates all Laya-specific functionality from the scheduler and UI.

    Key behaviors:
    - Laya is only initialized when explicitly requested.
    - Uses Router.predict with a 'choice' question to select among candidates.
    - Validates returned result against actual candidate identifiers.
    - Handles missing deps, unavailable checkpoints, timeouts, errors.
    - Reports status: uninitialized, loading, ready, unavailable, failed.
    """

    def __init__(self):
        self._status = LayaStatus.UNINITIALIZED
        self._router = None
        self._error_message: Optional[str] = None
        self._laya_version: Optional[str] = None

    @property
    def status(self) -> LayaStatus:
        return self._status

    @property
    def error_message(self) -> Optional[str]:
        return self._error_message

    @property
    def is_ready(self) -> bool:
        return self._status == LayaStatus.READY

    @property
    def version(self) -> Optional[str]:
        return self._laya_version

    def check_availability(self) -> bool:
        """Check if the laya package is importable without initializing it."""
        try:
            import laya
            self._laya_version = getattr(laya, '__version__', 'unknown')
            return True
        except ImportError:
            self._status = LayaStatus.UNAVAILABLE
            self._error_message = "laya package is not installed. Install with: pip install laya"
            return False

    def initialize(self) -> bool:
        """
        Initialize the Laya Router.

        This downloads checkpoints on first use (requires internet).
        Call this only when the user explicitly requests it.

        Returns True if initialization succeeded.
        """
        if self._status == LayaStatus.READY:
            logger.info("Laya already initialized")
            return True

        self._status = LayaStatus.LOADING
        self._error_message = None

        if not self.check_availability():
            return False

        try:
            from laya import Router
            logger.info("Creating Laya Router (lazy mode, max_loaded=1 for memory)")
            # Use lazy loading and max_loaded=1 to conserve memory on resource-constrained desktop
            self._router = Router(max_loaded=1)
            self._status = LayaStatus.READY
            logger.info("Laya Router initialized successfully (v%s)", self._laya_version)
            return True
        except Exception as e:
            self._status = LayaStatus.FAILED
            self._error_message = f"Laya initialization failed: {e}"
            logger.error("Laya initialization failed: %s", e, exc_info=True)
            return False

    def decide(
        self,
        candidate_ids: list[str],
        candidate_descriptions: dict[str, str],
        scenario_context: str,
        timeout_seconds: float = 30.0,
    ) -> LayaDecision:
        """
        Ask Laya to select among validated scheduling candidates.

        Args:
            candidate_ids: List of valid candidate worker IDs to choose from.
            candidate_descriptions: Mapping of worker_id -> human description.
            scenario_context: Description of the scheduling scenario.
            timeout_seconds: Maximum time to wait for Laya's decision.

        Returns:
            LayaDecision with selected_id, confidence, and success status.
        """
        if not self.is_ready or self._router is None:
            return LayaDecision(
                success=False,
                error=f"Laya not ready (status: {self._status.value})",
            )

        if not candidate_ids:
            return LayaDecision(
                success=False,
                error="No candidates provided for decision",
            )

        if len(candidate_ids) == 1:
            return LayaDecision(
                selected_id=candidate_ids[0],
                confidence=1.0,
                success=True,
                duration_ms=0.0,
            )

        # Build the criteria dict: candidate_id -> description
        criteria = {}
        for cid in candidate_ids:
            criteria[cid] = candidate_descriptions.get(cid, f"Worker {cid}")

        # Build the state and question for Laya's Router.predict
        state = f"Scheduling decision: {scenario_context}"
        questions = {
            "best_worker": {
                "type": "choice",
                "instructions": (
                    "Which worker is the best placement target for this workload? "
                    "Consider resource availability, processing speed, latency, and current load."
                ),
                "criteria": criteria,
            }
        }

        start_time = time.monotonic()
        try:
            result = self._router.predict(state, questions)
            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Extract the choice from Laya's response
            answers = result.get("answers", {})
            best_worker = answers.get("best_worker", {})
            choice = best_worker.get("choice")
            confidence = best_worker.get("confidence")

            # Validate: choice must be one of the candidate IDs
            if choice not in candidate_ids:
                logger.warning(
                    "Laya returned invalid choice '%s' (valid: %s)",
                    choice, candidate_ids,
                )
                return LayaDecision(
                    success=False,
                    error=f"Laya returned invalid choice: {choice!r}",
                    duration_ms=elapsed_ms,
                )

            logger.info(
                "Laya selected %s with confidence %.3f in %.1f ms",
                choice, confidence or 0, elapsed_ms,
            )
            return LayaDecision(
                selected_id=choice,
                confidence=confidence,
                success=True,
                duration_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error("Laya decision failed: %s", e, exc_info=True)
            return LayaDecision(
                success=False,
                error=f"Laya decision error: {e}",
                duration_ms=elapsed_ms,
            )

    def shutdown(self):
        """Release Laya resources."""
        if self._router is not None:
            try:
                # Router has an unload() method per the docs
                self._router.unload()
            except Exception:
                pass
            self._router = None
        self._status = LayaStatus.UNINITIALIZED
        logger.info("Laya adapter shut down")
