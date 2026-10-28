"""Main window — connects all panels with services."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel,
)

from racon.scheduler.models import (
    SchedulerResult, SchedulingObjective, SchedulingMode,
)
from racon.scenarios import get_scenario
from racon.services.benchmark_service import BenchmarkService
from racon.services.monitoring_service import MonitoringService
from racon.storage.database import Database
from racon.storage.repository import BenchmarkRepository
from racon.ui.dashboard import DashboardPanel
from racon.ui.chat_panel import ChatPanel
from racon.ui.scheduler_lab import SchedulerLabPanel
from racon.ui.benchmark_history import BenchmarkHistoryPanel
from racon.ui.settings import SettingsPanel
from racon.ui.worker_panel import WorkerAgentPanel
from racon.ui.styles import DARK_STYLESHEET

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """RACoN main window with unified dashboard, chat, and orchestration panels."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RACoN v1.0.1 — Resource-Aware Compute Orchestration")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        self.setStyleSheet(DARK_STYLESHEET)

        # Initialize backend
        self._db = Database()
        self._db.initialize()
        self._repo = BenchmarkRepository(self._db)
        self._benchmark_service = BenchmarkService(self._repo)
        self._monitoring = MonitoringService(interval_ms=2000)

        # Build UI
        self._setup_ui()
        self._connect_signals()
        self._load_settings()

        # Start monitoring
        self._monitoring.start()

        logger.info("MainWindow initialized")

    def _setup_ui(self):
        # Tab widget
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        # Panels
        self._dashboard = DashboardPanel()
        self._chat_panel = ChatPanel()
        self._worker_panel = WorkerAgentPanel()
        self._scheduler_lab = SchedulerLabPanel()
        self._history = BenchmarkHistoryPanel()
        self._settings = SettingsPanel()

        self._tabs.addTab(self._dashboard, "📊 Dashboard")
        self._tabs.addTab(self._chat_panel, "💬 Chat")
        self._tabs.addTab(self._worker_panel, "🖥 Worker Agent")
        self._tabs.addTab(self._scheduler_lab, "🔬 Scheduler Lab")
        self._tabs.addTab(self._history, "📈 History")
        self._tabs.addTab(self._settings, "⚙ Settings")

        # Status bar
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._status_label = QLabel("Ready")
        self._statusbar.addWidget(self._status_label)

        # Set export callbacks for history panel
        self._history.set_export_callbacks(
            self._benchmark_service.export_csv,
            self._benchmark_service.export_json,
        )

    def _connect_signals(self):
        # Monitoring
        self._monitoring.full_snapshot_ready.connect(
            self._dashboard.update_hardware_snapshot
        )
        self._monitoring.metrics_updated.connect(
            self._dashboard.update_metrics
        )

        # Scheduler lab
        self._scheduler_lab.run_requested.connect(self._on_run_requested)
        self._scheduler_lab.laya_init_requested.connect(self._on_init_laya)

        # Benchmark service
        self._benchmark_service.scheduler_started.connect(
            lambda: self._scheduler_lab.set_busy(True)
        )
        self._benchmark_service.scheduler_finished.connect(self._on_scheduler_finished)
        self._benchmark_service.scheduler_error.connect(self._on_scheduler_error)
        self._benchmark_service.laya_status_changed.connect(self._on_laya_status_changed)

        # History
        self._history.refresh_requested.connect(self._refresh_history)
        self._history.clear_requested.connect(self._on_clear_history)

        # Settings
        self._settings.setting_changed.connect(self._on_setting_changed)

    def _load_settings(self):
        """Load persisted settings."""
        settings = {
            "default_mode": self._repo.get_setting("default_mode", "deterministic"),
            "default_objective": self._repo.get_setting("default_objective", "balanced"),
        }
        self._settings.load_settings(settings)
        self._update_run_count()
        self._refresh_history()

    @Slot(str, str, str)
    def _on_run_requested(self, scenario_name: str, objective_str: str, mode_str: str):
        """Handle scheduler run request from the lab panel."""
        scenario = get_scenario(scenario_name)
        if not scenario:
            self._scheduler_lab.display_error(f"Scenario '{scenario_name}' not found")
            return

        try:
            objective = SchedulingObjective(objective_str)
            mode = SchedulingMode(mode_str)
        except ValueError as e:
            self._scheduler_lab.display_error(f"Invalid parameter: {e}")
            return

        self._status_label.setText(f"Running: {scenario_name}...")
        self._benchmark_service.run_scheduler(
            candidates=scenario["workers"],
            objective=objective,
            mode=mode,
            scenario_name=scenario_name,
        )

    @Slot(object)
    def _on_scheduler_finished(self, result: SchedulerResult):
        """Handle completed scheduler run."""
        self._scheduler_lab.set_busy(False)
        self._scheduler_lab.display_result(result)
        self._dashboard.update_last_decision(result)
        self._update_run_count()
        self._refresh_history()

        status = "✓" if result.success else "✗"
        self._status_label.setText(
            f"{status} {result.scenario_name}: {result.final_choice or result.failure_reason} "
            f"({result.decision_duration_ms:.1f} ms)"
        )
        logger.info("Scheduler run displayed: %s", result.run_id)

    @Slot(str)
    def _on_scheduler_error(self, error_msg: str):
        """Handle scheduler error."""
        self._scheduler_lab.set_busy(False)
        self._scheduler_lab.display_error(error_msg)
        self._status_label.setText(f"Error: {error_msg}")

    @Slot()
    def _on_init_laya(self):
        """Initialize Laya in background."""
        self._status_label.setText("Initializing Laya...")
        self._benchmark_service.initialize_laya()

    @Slot(str)
    def _on_laya_status_changed(self, status: str):
        """Handle Laya status change."""
        self._dashboard.update_laya_status(status)
        self._scheduler_lab.update_laya_status(status)
        self._settings.update_laya_info(
            status, self._benchmark_service.laya_adapter.version
        )
        self._status_label.setText(f"Laya: {status}")

    @Slot()
    def _refresh_history(self):
        """Refresh the benchmark history panel."""
        runs = self._benchmark_service.get_all_runs()
        self._history.update_runs(runs)

    @Slot()
    def _on_clear_history(self):
        """Clear benchmark history."""
        if self._benchmark_service.clear_history():
            self._refresh_history()
            self._update_run_count()
            self._status_label.setText("History cleared")

    @Slot(str, str)
    def _on_setting_changed(self, key: str, value: str):
        """Persist a settings change."""
        self._repo.set_setting(key, value)
        logger.info("Setting changed: %s = %s", key, value)

    def _update_run_count(self):
        count = self._benchmark_service.get_run_count()
        self._dashboard.update_run_count(count)

    def closeEvent(self, event):
        """Clean up on close."""
        logger.info("RACoN closing")
        if hasattr(self, "_worker_panel"):
            self._worker_panel.closeEvent(event)
        self._monitoring.stop()
        if hasattr(self._benchmark_service, "cancel_or_wait"):
            self._benchmark_service.cancel_or_wait()
        self._benchmark_service.laya_adapter.shutdown()
        self._db.close()
        super().closeEvent(event)
