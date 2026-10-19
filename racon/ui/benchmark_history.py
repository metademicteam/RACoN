"""Benchmark History panel — view, inspect, and export past runs."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QGroupBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QFileDialog, QMessageBox, QSplitter,
)

logger = logging.getLogger(__name__)


class BenchmarkHistoryPanel(QWidget):
    """Displays benchmark run history with inspection and export capabilities."""

    refresh_requested = Signal()
    clear_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._runs: list[dict] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("📊 Benchmark History")
        title.setObjectName("heading")
        layout.addWidget(title)

        # Action buttons
        btn_layout = QHBoxLayout()

        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        btn_layout.addWidget(refresh_btn)

        self._export_csv_btn = QPushButton("📄  Export CSV")
        self._export_csv_btn.clicked.connect(self._on_export_csv)
        btn_layout.addWidget(self._export_csv_btn)

        self._export_json_btn = QPushButton("📋  Export JSON")
        self._export_json_btn.clicked.connect(self._on_export_json)
        btn_layout.addWidget(self._export_json_btn)

        btn_layout.addStretch()

        clear_btn = QPushButton("🗑  Clear History")
        clear_btn.setObjectName("danger")
        clear_btn.clicked.connect(self._on_clear_clicked)
        btn_layout.addWidget(clear_btn)

        layout.addLayout(btn_layout)

        # Split: table + detail
        splitter = QSplitter(Qt.Orientation.Vertical)

        # History table
        table_group = QGroupBox(f"Runs (0)")
        self._table_group = table_group
        table_layout = QVBoxLayout(table_group)

        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        headers = [
            "Timestamp", "Scenario", "Objective", "Mode",
            "Selected", "Duration (ms)", "Laya Used", "Fallback", "Success",
        ]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        table_layout.addWidget(self._table)
        splitter.addWidget(table_group)

        # Detail view
        detail_group = QGroupBox("Run Details")
        detail_layout = QVBoxLayout(detail_group)
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setPlaceholderText("Select a run to see details")
        detail_layout.addWidget(self._detail_text)
        splitter.addWidget(detail_group)

        layout.addWidget(splitter, 1)

        # Store export callbacks (set by main window)
        self._export_csv_callback = None
        self._export_json_callback = None

    def set_export_callbacks(self, csv_fn, json_fn):
        """Set the export data provider callbacks."""
        self._export_csv_callback = csv_fn
        self._export_json_callback = json_fn

    def update_runs(self, runs: list[dict]):
        """Populate the table with benchmark run data."""
        self._runs = runs
        self._table_group.setTitle(f"Runs ({len(runs)})")
        self._table.setRowCount(len(runs))

        for row, run in enumerate(runs):
            ts = run.get("timestamp", "")[:19]  # Trim to seconds
            items = [
                ts,
                run.get("scenario_name", ""),
                run.get("objective", "").replace("_", " ").title(),
                run.get("mode", "").replace("_", " ").title(),
                run.get("final_choice", "—") or "—",
                f"{run.get('decision_duration_ms', 0):.1f}",
                "✓" if run.get("laya_used_successfully") else "—",
                "⚠" if run.get("fallback_used") else "—",
                "✓" if run.get("success") else "✗",
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(str(text))
                if not run.get("success"):
                    item.setForeground(Qt.GlobalColor.darkGray)
                self._table.setItem(row, col, item)

    def _on_selection_changed(self):
        rows = self._table.selectedIndexes()
        if not rows:
            self._detail_text.clear()
            return

        row = rows[0].row()
        if row < len(self._runs):
            run = self._runs[row]
            self._show_detail(run)

    def _show_detail(self, run: dict):
        lines = []
        lines.append(f"Run ID: {run.get('run_id', '—')}")
        lines.append(f"Timestamp: {run.get('timestamp', '—')}")
        lines.append(f"Scenario: {run.get('scenario_name', '—')}")
        lines.append(f"Objective: {run.get('objective', '—')}")
        lines.append(f"Mode: {run.get('mode', '—')}")
        lines.append(f"Candidates: {run.get('candidate_count', 0)} total, "
                     f"{run.get('feasible_count', 0)} feasible")
        lines.append("")

        lines.append(f"Deterministic choice: {run.get('deterministic_choice', '—')}")
        if run.get('deterministic_score') is not None:
            lines.append(f"Deterministic score: {run['deterministic_score']:.4f}")
        lines.append("")

        if run.get('laya_recommendation'):
            lines.append(f"Laya recommendation: {run['laya_recommendation']}")
            if run.get('laya_confidence') is not None:
                lines.append(f"Laya confidence: {run['laya_confidence']:.3f}")
        lines.append("")

        lines.append(f"Final choice: {run.get('final_choice', '—')}")
        if run.get('final_score') is not None:
            lines.append(f"Final score: {run['final_score']:.4f}")
        lines.append(f"Decision duration: {run.get('decision_duration_ms', 0):.1f} ms")
        lines.append("")

        if run.get('fallback_used'):
            lines.append(f"⚠ Fallback reason: {run.get('fallback_reason', '—')}")
        if run.get('failure_reason'):
            lines.append(f"❌ Failure: {run['failure_reason']}")
        lines.append(f"Success: {'Yes' if run.get('success') else 'No'}")

        # Show evaluations if available
        evals = run.get("evaluations", [])
        if evals:
            lines.append("")
            lines.append("--- Candidate Evaluations ---")
            for ev in evals:
                feasible = "✓" if ev.get("is_feasible") else "✗"
                sim = "SIM" if ev.get("is_simulated") else "LIVE"
                score = f"{ev['score']:.4f}" if ev.get("score") is not None else "—"
                rejection = ev.get("rejection_reasons", "")
                lines.append(f"  {ev.get('worker_id', '?')} [{sim}] {feasible} score={score}")
                if rejection:
                    lines.append(f"    Rejected: {rejection}")

        self._detail_text.setPlainText("\n".join(lines))

    def _on_export_csv(self):
        if not self._export_csv_callback:
            return
        data = self._export_csv_callback()
        if not data:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "racon_benchmarks.csv", "CSV Files (*.csv)"
        )
        if path:
            Path(path).write_text(data, encoding="utf-8")
            QMessageBox.information(self, "Export", f"Exported to {path}")

    def _on_export_json(self):
        if not self._export_json_callback:
            return
        data = self._export_json_callback()
        if not data:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export JSON", "racon_benchmarks.json", "JSON Files (*.json)"
        )
        if path:
            Path(path).write_text(data, encoding="utf-8")
            QMessageBox.information(self, "Export", f"Exported to {path}")

    def _on_clear_clicked(self):
        reply = QMessageBox.question(
            self, "Clear History",
            "Are you sure you want to delete all benchmark history?\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_requested.emit()
