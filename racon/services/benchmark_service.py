"""Benchmark service — runs scheduler in a background thread."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from racon.scheduler.models import (
    SchedulerResult, SchedulingObjective, SchedulingMode, WorkerCandidate,
)
from racon.scheduler.evaluation import SchedulingEngine
from racon.scheduler.laya_adapter import LayaAdapter, LayaStatus
from racon.storage.repository import BenchmarkRepository

logger = logging.getLogger(__name__)


class _SchedulerWorker(QObject):
    """Worker that runs scheduling in a background thread."""

    finished = Signal(object)  # SchedulerResult
    error = Signal(str)

    def __init__(
        self,
        engine: SchedulingEngine,
        candidates: list[WorkerCandidate],
        objective: SchedulingObjective,
        mode: SchedulingMode,
        scenario_name: str,
    ):
        super().__init__()
        self.engine = engine
        self.candidates = candidates
        self.objective = objective
        self.mode = mode
        self.scenario_name = scenario_name

    def run(self):
        try:
            result = self.engine.run(
                self.candidates, self.objective, self.mode, self.scenario_name,
            )
            self.finished.emit(result)
        except Exception as e:
            logger.error("Scheduler worker failed: %s", e, exc_info=True)
            self.error.emit(str(e))


class _LayaInitWorker(QObject):
    """Worker that initializes Laya in a background thread."""

    finished = Signal(bool, str)  # success, message

    def __init__(self, adapter: LayaAdapter):
        super().__init__()
        self.adapter = adapter

    def run(self):
        try:
            success = self.adapter.initialize()
            if success:
                self.finished.emit(True, f"Laya initialized (v{self.adapter.version})")
            else:
                self.finished.emit(False, self.adapter.error_message or "Unknown error")
        except Exception as e:
            self.finished.emit(False, str(e))


class BenchmarkService(QObject):
    """Service that manages scheduler execution and Laya initialization.

    All long-running operations execute on background QThreads to keep the UI
    responsive. Signals report completion back to the main thread.
    """

    scheduler_started = Signal()
    scheduler_finished = Signal(object)  # SchedulerResult
    scheduler_error = Signal(str)
    laya_status_changed = Signal(str)  # LayaStatus value
    run_saved = Signal(str)  # run_id

    def __init__(
        self,
        repository: BenchmarkRepository,
        laya_adapter: Optional[LayaAdapter] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._repository = repository
        self._laya = laya_adapter or LayaAdapter()
        self._engine = SchedulingEngine(self._laya)
        self._scheduler_thread: Optional[QThread] = None
        self._laya_thread: Optional[QThread] = None
        self._busy = False

    @property
    def laya_adapter(self) -> LayaAdapter:
        return self._laya

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def repository(self) -> BenchmarkRepository:
        return self._repository

    def run_scheduler(
        self,
        candidates: list[WorkerCandidate],
        objective: SchedulingObjective,
        mode: SchedulingMode,
        scenario_name: str,
    ):
        """Run the scheduler in a background thread."""
        if self._busy:
            self.scheduler_error.emit("A scheduling operation is already running")
            return

        self._busy = True
        self.scheduler_started.emit()

        self._scheduler_thread = QThread()
        worker = _SchedulerWorker(
            self._engine, candidates, objective, mode, scenario_name,
        )
        worker.moveToThread(self._scheduler_thread)

        self._scheduler_thread.started.connect(worker.run)
        worker.finished.connect(self._on_scheduler_finished)
        worker.error.connect(self._on_scheduler_error)
        worker.finished.connect(self._scheduler_thread.quit)
        worker.error.connect(self._scheduler_thread.quit)
        self._scheduler_thread.finished.connect(self._scheduler_thread.deleteLater)
        # prevent worker from being garbage collected
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)

        self._scheduler_thread.start()

    def _on_scheduler_finished(self, result: SchedulerResult):
        """Handle completed scheduling run."""
        self._busy = False
        # Save to database
        saved = self._repository.save_run(result)
        if saved:
            self.run_saved.emit(result.run_id)
        self.scheduler_finished.emit(result)

    def _on_scheduler_error(self, error_msg: str):
        """Handle scheduler error."""
        self._busy = False
        self.scheduler_error.emit(error_msg)

    def initialize_laya(self):
        """Initialize Laya in a background thread."""
        if self._laya.is_ready:
            self.laya_status_changed.emit(LayaStatus.READY.value)
            return

        if self._laya_thread is not None and self._laya_thread.isRunning():
            return

        self.laya_status_changed.emit(LayaStatus.LOADING.value)

        self._laya_thread = QThread()
        worker = _LayaInitWorker(self._laya)
        worker.moveToThread(self._laya_thread)

        self._laya_thread.started.connect(worker.run)
        worker.finished.connect(self._on_laya_init_finished)
        worker.finished.connect(self._laya_thread.quit)
        self._laya_thread.finished.connect(self._laya_thread.deleteLater)
        worker.finished.connect(worker.deleteLater)

        self._laya_thread.start()

    def _on_laya_init_finished(self, success: bool, message: str):
        """Handle Laya initialization result."""
        self.laya_status_changed.emit(self._laya.status.value)
        if success:
            logger.info("Laya initialized: %s", message)
        else:
            logger.warning("Laya initialization failed: %s", message)

    def get_run_count(self) -> int:
        return self._repository.get_run_count()

    def get_all_runs(self) -> list[dict]:
        return self._repository.get_all_runs()

    def get_run(self, run_id: str) -> Optional[dict]:
        return self._repository.get_run(run_id)

    def export_csv(self) -> str:
        return self._repository.export_runs_csv()

    def export_json(self) -> str:
        return self._repository.export_runs_json()

    def clear_history(self) -> bool:
        return self._repository.clear_all_runs()

    def cancel_or_wait(self, timeout_ms: int = 3000):
        """Orderly shutdown of active background threads."""
        for thread in [self._scheduler_thread, self._laya_thread]:
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(timeout_ms)
        self._busy = False
