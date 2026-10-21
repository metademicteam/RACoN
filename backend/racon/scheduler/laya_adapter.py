"""Isolated Laya Router adapter for candidate selection via typed choice predictions."""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)


class LayaStatus(str, enum.Enum):
    """Laya runtime and checkpoint state."""
    UNINITIALIZED = "uninitialized"
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass
class LayaDecision:
    """Decision output produced by Laya Router."""
    selected_id: Optional[str] = None
    confidence: Optional[float] = None
    success: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class LayaAdapter:
    """
    Isolates Laya Router integration from the rest of RACoN.

    Key guarantees:
    - Lazy initialization: Router is never instantiated unless explicitly requested.
    - Low footprint: Uses Router(max_loaded=1) to prevent RAM exhaustion.
    - Typed Choice API: Uses Router.predict with 'choice' question schema.
    - Robust Validation: Validates returned choice matches known candidate IDs.
    - Graceful Fallback: Reports UNAVAILABLE or FAILED states without throwing uncaught exceptions.
    """

    def __init__(self):
        self._status: LayaStatus = LayaStatus.UNINITIALIZED
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
        """Check if the laya package is installed and importable without initializing models."""
        try:
            import laya
            self._laya_version = getattr(laya, "__version__", "unknown")
            return True
        except ImportError:
            self._status = LayaStatus.UNAVAILABLE
            self._error_message = "laya package is not installed. Install with: pip install laya"
            return False
        except Exception as e:
            self._status = LayaStatus.UNAVAILABLE
            self._error_message = f"laya import check failed: {e}"
            return False

    def initialize(self) -> bool:
        """
        Initialize the Laya Router.
        Downloads checkpoint on first invocation.
        Returns True on success, False otherwise.
        """
        if self._status == LayaStatus.READY:
            return True

        self._status = LayaStatus.LOADING
        self._error_message = None

        if not self.check_availability():
            return False

        try:
            from laya import Router
            logger.info("Initializing Laya Router with max_loaded=1...")
            self._router = Router(max_loaded=1)
            self._status = LayaStatus.READY
            logger.info("Laya Router ready (v%s)", self._laya_version)
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
        Ask Laya Router to select the best worker candidate using a typed choice prompt.

        Args:
            candidate_ids: List of candidate worker IDs.
            candidate_descriptions: Human/machine readable descriptions of candidate capabilities.
            scenario_context: Description of the workload and scenario.
            timeout_seconds: Execution timeout limit.

        Returns:
            LayaDecision with selected_id and confidence score.
        """
        if not self.is_ready or self._router is None:
            return LayaDecision(
                success=False,
                error=f"Laya not ready (status: {self._status.value})",
            )

        if not candidate_ids:
            return LayaDecision(
                success=False,
                error="No candidates provided for Laya decision",
            )

        if len(candidate_ids) == 1:
            return LayaDecision(
                selected_id=candidate_ids[0],
                confidence=1.0,
                success=True,
                duration_ms=0.0,
            )

        criteria = {}
        for cid in candidate_ids:
            criteria[cid] = candidate_descriptions.get(cid, f"Worker {cid}")

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

            answers = result.get("answers", {})
            best_worker = answers.get("best_worker", {})
            choice = best_worker.get("choice")
            confidence = best_worker.get("confidence")

            # Validate that choice corresponds to a real candidate
            if choice not in candidate_ids:
                logger.warning(
                    "Laya returned candidate '%s' which is not in candidate list: %s",
                    choice, candidate_ids,
                )
                return LayaDecision(
                    success=False,
                    error=f"Laya returned candidate not in candidate list: {choice!r}",
                    duration_ms=elapsed_ms,
                )

            logger.info("Laya chose %s (confidence=%.3f, elapsed=%.1fms)", choice, confidence or 0.0, elapsed_ms)
            return LayaDecision(
                selected_id=choice,
                confidence=confidence,
                success=True,
                duration_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error("Laya decision evaluation failed: %s", e, exc_info=True)
            return LayaDecision(
                success=False,
                error=f"Laya predict error: {e}",
                duration_ms=elapsed_ms,
            )

    def shutdown(self):
        """Unload Laya router checkpoint and release memory."""
        if self._router is not None:
            try:
                if hasattr(self._router, "unload"):
                    self._router.unload()
            except Exception as e:
                logger.debug("Error unloading Laya router: %s", e)
            self._router = None
        self._status = LayaStatus.UNINITIALIZED
        logger.info("Laya adapter shut down")


_GLOBAL_LAYA_ADAPTER: Optional[LayaAdapter] = None


def get_laya_adapter() -> LayaAdapter:
    """Retrieve singleton LayaAdapter instance."""
    global _GLOBAL_LAYA_ADAPTER
    if _GLOBAL_LAYA_ADAPTER is None:
        _GLOBAL_LAYA_ADAPTER = LayaAdapter()
    return _GLOBAL_LAYA_ADAPTER
