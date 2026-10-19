"""Background monitoring service using Qt threading."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from racon.hardware import get_lightweight_update, discover_hardware, HardwareSnapshot

logger = logging.getLogger(__name__)


class MonitoringService(QObject):
    """Periodically collects hardware metrics without blocking the UI.

    Uses a QTimer (fires on the main thread) to call psutil.
    psutil.cpu_percent(interval=None) is non-blocking when called after
    an initial call with interval > 0, so this is safe for the GUI thread.
    """

    metrics_updated = Signal(dict)  # Emits lightweight update dict
    full_snapshot_ready = Signal(object)  # Emits HardwareSnapshot

    def __init__(self, interval_ms: int = 2000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._collect_metrics)
        self._running = False
        self._initial_snapshot: Optional[HardwareSnapshot] = None

    @property
    def initial_snapshot(self) -> Optional[HardwareSnapshot]:
        return self._initial_snapshot

    def start(self):
        """Start periodic monitoring."""
        if self._running:
            return
        # Do the initial full discovery
        try:
            self._initial_snapshot = discover_hardware()
            self.full_snapshot_ready.emit(self._initial_snapshot)
            logger.info("Initial hardware discovery completed")
        except Exception as e:
            logger.error("Initial hardware discovery failed: %s", e)

        self._running = True
        self._timer.start(self._interval_ms)
        logger.info("Monitoring started (interval=%dms)", self._interval_ms)

    def stop(self):
        """Stop periodic monitoring."""
        self._timer.stop()
        self._running = False
        logger.info("Monitoring stopped")

    def _collect_metrics(self):
        """Collect lightweight metrics (runs on timer)."""
        try:
            update = get_lightweight_update()
            self.metrics_updated.emit(update)
        except Exception as e:
            logger.error("Metrics collection failed: %s", e)
