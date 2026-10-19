"""Scheduler Lab panel — main scheduling interface."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QGroupBox, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QTextEdit, QSplitter, QFrame,
)

from racon.scheduler.models import (
    SchedulerResult, SchedulingObjective, SchedulingMode,
    CandidateEvaluation, WorkerCandidate,
)
from racon.scenarios import load_scenarios

logger = logging.getLogger(__name__)


class SchedulerLabPanel(QWidget):
    """Main scheduler interface with scenario selection, execution, and results."""

    run_requested = Signal(str, str, str)  # scenario_name, objective, mode
    laya_init_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scenarios = load_scenarios()
        self._current_result: Optional[SchedulerResult] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("🔬 Scheduler Lab")
        title.setObjectName("heading")
        layout.addWidget(title)

        # Controls
        controls_group = QGroupBox("Configuration")
        controls_layout = QGridLayout(controls_group)
        controls_layout.setSpacing(8)

        # Scenario selector
        controls_layout.addWidget(QLabel("Scenario:"), 0, 0)
        self._scenario_combo = QComboBox()
        for s in self._scenarios:
            self._scenario_combo.addItem(s["name"])
        self._scenario_combo.currentTextChanged.connect(self._on_scenario_changed)
        controls_layout.addWidget(self._scenario_combo, 0, 1)

        # Scenario description
        self._scenario_desc = QLabel("")
        self._scenario_desc.setObjectName("metric-label")
        self._scenario_desc.setWordWrap(True)
        controls_layout.addWidget(self._scenario_desc, 0, 2, 1, 2)

        # Objective selector
        controls_layout.addWidget(QLabel("Objective:"), 1, 0)
        self._objective_combo = QComboBox()
        for obj in SchedulingObjective:
            self._objective_combo.addItem(
                obj.value.replace("_", " ").title(), obj.value
            )
        controls_layout.addWidget(self._objective_combo, 1, 1)

        # Mode selector
        controls_layout.addWidget(QLabel("Mode:"), 1, 2)
        self._mode_combo = QComboBox()
        for mode in SchedulingMode:
            self._mode_combo.addItem(
                mode.value.replace("_", " ").title(), mode.value
            )
        controls_layout.addWidget(self._mode_combo, 1, 3)

        layout.addWidget(controls_group)

        # Action buttons
        btn_layout = QHBoxLayout()

        self._run_btn = QPushButton("▶  Run Scheduler")
        self._run_btn.setObjectName("primary")
        self._run_btn.clicked.connect(self._on_run_clicked)
        btn_layout.addWidget(self._run_btn)

        self._init_laya_btn = QPushButton("🧠  Initialize Laya")
        self._init_laya_btn.clicked.connect(self._on_init_laya_clicked)
        btn_layout.addWidget(self._init_laya_btn)

        self._laya_status = QLabel("Laya: Uninitialized")
        self._laya_status.setObjectName("status-warn")
        btn_layout.addWidget(self._laya_status)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # Results area (splitter)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Candidate evaluation table
        table_group = QGroupBox("Candidate Evaluations")
        table_layout = QVBoxLayout(table_group)
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        headers = [
            "Worker ID", "Type", "Feasible", "Score", "Status",
            "RAM (Avail/Req)", "Speed (tps)", "Latency (ms)",
            "Workload %", "Rejection Reasons",
        ]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self._table)
        splitter.addWidget(table_group)

        # Decision summary
        decision_group = QGroupBox("Decision Summary")
        decision_layout = QVBoxLayout(decision_group)
        self._decision_text = QTextEdit()
        self._decision_text.setReadOnly(True)
        self._decision_text.setMaximumHeight(200)
        decision_layout.addWidget(self._decision_text)
        splitter.addWidget(decision_group)

        layout.addWidget(splitter, 1)  # stretch factor

        # Initialize scenario description
        if self._scenarios:
            self._on_scenario_changed(self._scenarios[0]["name"])

    def _on_scenario_changed(self, name: str):
        scenario = next((s for s in self._scenarios if s["name"] == name), None)
        if scenario:
            workers = scenario["workers"]
            sim_count = sum(1 for w in workers if w.is_simulated)
            live_count = len(workers) - sim_count
            desc = scenario["description"]
            desc += f"  ({len(workers)} workers: {sim_count} simulated"
            if live_count:
                desc += f", {live_count} live"
            desc += f", model: {scenario['model_partition_required_mb']:.0f} MB)"
            self._scenario_desc.setText(desc)

    def _on_run_clicked(self):
        scenario_name = self._scenario_combo.currentText()
        objective = self._objective_combo.currentData()
        mode = self._mode_combo.currentData()
        if scenario_name:
            self.run_requested.emit(scenario_name, objective, mode)

    def _on_init_laya_clicked(self):
        self.laya_init_requested.emit()

    def set_busy(self, busy: bool):
        """Update UI state during scheduler execution."""
        self._run_btn.setEnabled(not busy)
        if busy:
            self._run_btn.setText("⏳  Running...")
        else:
            self._run_btn.setText("▶  Run Scheduler")

    def update_laya_status(self, status: str):
        self._laya_status.setText(f"Laya: {status.replace('_', ' ').title()}")
        if status == "ready":
            self._laya_status.setObjectName("status-good")
            self._init_laya_btn.setEnabled(False)
            self._init_laya_btn.setText("✓  Laya Ready")
        elif status == "loading":
            self._laya_status.setObjectName("status-warn")
            self._init_laya_btn.setEnabled(False)
            self._init_laya_btn.setText("⏳  Loading Laya...")
        elif status in ("failed", "unavailable"):
            self._laya_status.setObjectName("status-bad")
            self._init_laya_btn.setEnabled(True)
            self._init_laya_btn.setText("🧠  Initialize Laya")
        else:
            self._laya_status.setObjectName("status-warn")
            self._init_laya_btn.setEnabled(True)
        # Force style refresh
        self._laya_status.setStyleSheet(self._laya_status.styleSheet())
        self._laya_status.style().unpolish(self._laya_status)
        self._laya_status.style().polish(self._laya_status)

    def display_result(self, result: SchedulerResult):
        """Show the scheduling result in the table and summary."""
        self._current_result = result

        # Update table
        self._table.setRowCount(len(result.evaluations))
        for row, ev in enumerate(result.evaluations):
            # Find matching worker for more details
            items = [
                ev.worker_id,
                "SIM" if ev.is_simulated else "LIVE",
                "✓" if ev.is_feasible else "✗",
                f"{ev.score:.4f}" if ev.score is not None else "—",
            ]

            # Status indicator
            if ev.worker_id == result.final_choice:
                status = "★ SELECTED"
            elif ev.worker_id == result.laya_recommendation and ev.worker_id != result.final_choice:
                status = "🧠 Laya Pick"
            elif ev.worker_id == result.deterministic_choice and result.mode != SchedulingMode.DETERMINISTIC:
                status = "📐 Det. Pick"
            elif ev.is_feasible:
                status = "Feasible"
            else:
                status = "Rejected"

            items.append(status)

            # We'll add more detail columns from score_components
            ram_info = ""
            speed_info = ""
            latency_info = ""
            workload_info = ""
            if ev.score_components:
                sc = ev.score_components
                ram_info = f"{sc.get('memory_headroom', '—')}"
                speed_info = f"{sc.get('effective_throughput', sc.get('speed_factor', '—'))}"
                latency_info = f"{sc.get('network_latency_ms', sc.get('effective_latency', '—'))}"
                workload_info = f"{sc.get('availability_factor', '—')}"

            items.extend([ram_info, speed_info, latency_info, workload_info])
            items.append("; ".join(ev.rejection_reasons) if ev.rejection_reasons else "")

            for col, text in enumerate(items):
                item = QTableWidgetItem(str(text))
                if not ev.is_feasible:
                    item.setForeground(Qt.GlobalColor.darkGray)
                elif ev.worker_id == result.final_choice:
                    item.setBackground(Qt.GlobalColor.darkGreen)
                self._table.setItem(row, col, item)

        # Update decision summary
        lines = []
        lines.append(f"Run ID: {result.run_id}")
        lines.append(f"Scenario: {result.scenario_name}")
        lines.append(f"Objective: {result.objective.value.replace('_', ' ').title()}")
        lines.append(f"Mode: {result.mode.value.replace('_', ' ').title()}")
        lines.append(f"Candidates: {result.candidate_count} total, {result.feasible_count} feasible")
        lines.append(f"Duration: {result.decision_duration_ms:.1f} ms")
        lines.append("")

        if result.success:
            lines.append(f"✓ Deterministic choice: {result.deterministic_choice} "
                        f"(score: {result.deterministic_score:.4f})")
            if result.laya_recommendation:
                agree = result.laya_recommendation == result.deterministic_choice
                lines.append(f"🧠 Laya recommendation: {result.laya_recommendation} "
                           f"(confidence: {result.laya_confidence:.3f})"
                           f" {'✓ AGREES' if agree else '✗ DISAGREES'}")
            lines.append(f"★ Final selection: {result.final_choice} "
                        f"(score: {result.final_score:.4f})")
            if result.fallback_used:
                lines.append(f"⚠ Fallback used: {result.fallback_reason}")
        else:
            lines.append(f"❌ FAILED: {result.failure_reason}")

        # Note about simulation
        sim_workers = [ev.worker_id for ev in result.evaluations if ev.is_simulated]
        if sim_workers:
            lines.append("")
            lines.append(f"ℹ Simulated workers: {', '.join(sim_workers)}")
            lines.append("  (All metrics for simulated workers are estimated/synthetic values)")

        self._decision_text.setPlainText("\n".join(lines))

    def display_error(self, error_msg: str):
        """Show an error message."""
        self._decision_text.setPlainText(f"❌ Error: {error_msg}")

    def get_selected_scenario_workers(self) -> tuple[str, list[WorkerCandidate]]:
        """Get the selected scenario name and its workers."""
        name = self._scenario_combo.currentText()
        scenario = next((s for s in self._scenarios if s["name"] == name), None)
        if scenario:
            return name, scenario["workers"]
        return name, []
