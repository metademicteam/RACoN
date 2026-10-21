"""Dashboard panel — real-time hardware and application status."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QGroupBox, QProgressBar, QFrame,
)

from racon.hardware import HardwareSnapshot

logger = logging.getLogger(__name__)


class MetricCard(QFrame):
    """A small card showing a metric label and value."""

    def __init__(self, label: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background-color: #313244; border-radius: 8px; padding: 8px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self._value_label = QLabel(value)
        self._value_label.setObjectName("metric-value")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._label = QLabel(label)
        self._label.setObjectName("metric-label")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._value_label)
        layout.addWidget(self._label)

    def set_value(self, value: str):
        self._value_label.setText(value)


class DashboardPanel(QWidget):
    """Main dashboard showing hardware, app status, and last decision summary."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._last_result = None

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Title
        title = QLabel("⚡ RACoN Dashboard")
        title.setObjectName("heading")
        layout.addWidget(title)

        # Status row
        status_row = QHBoxLayout()
        self._status_label = QLabel("● Running")
        self._status_label.setObjectName("status-good")
        status_row.addWidget(self._status_label)
        status_row.addStretch()

        self._laya_status_label = QLabel("Laya: Uninitialized")
        self._laya_status_label.setObjectName("status-warn")
        status_row.addWidget(self._laya_status_label)
        layout.addLayout(status_row)

        # Metric cards
        metrics_layout = QGridLayout()
        metrics_layout.setSpacing(12)

        self._cpu_card = MetricCard("CPU Utilization")
        self._ram_card = MetricCard("RAM Usage")
        self._proc_mem_card = MetricCard("Process Memory")
        self._runs_card = MetricCard("Completed Runs", "0")

        metrics_layout.addWidget(self._cpu_card, 0, 0)
        metrics_layout.addWidget(self._ram_card, 0, 1)
        metrics_layout.addWidget(self._proc_mem_card, 0, 2)
        metrics_layout.addWidget(self._runs_card, 0, 3)
        layout.addLayout(metrics_layout)

        # CPU progress bar
        cpu_group = QGroupBox("CPU")
        cpu_layout = QVBoxLayout(cpu_group)
        self._cpu_name_label = QLabel("Detecting...")
        cpu_layout.addWidget(self._cpu_name_label)
        self._cpu_bar = QProgressBar()
        self._cpu_bar.setRange(0, 100)
        cpu_layout.addWidget(self._cpu_bar)
        self._cpu_detail_label = QLabel("")
        self._cpu_detail_label.setObjectName("metric-label")
        cpu_layout.addWidget(self._cpu_detail_label)
        layout.addWidget(cpu_group)

        # RAM progress bar
        ram_group = QGroupBox("System Memory")
        ram_layout = QVBoxLayout(ram_group)
        self._ram_bar = QProgressBar()
        self._ram_bar.setRange(0, 100)
        ram_layout.addWidget(self._ram_bar)
        self._ram_detail_label = QLabel("")
        self._ram_detail_label.setObjectName("metric-label")
        ram_layout.addWidget(self._ram_detail_label)
        layout.addWidget(ram_group)

        # Hardware info
        hw_group = QGroupBox("Hardware & System")
        hw_layout = QGridLayout(hw_group)
        hw_layout.setSpacing(6)

        labels = [
            "OS:", "Architecture:", "Python:", "CPU Cores:", "GPU:", "GPU Vendor:",
        ]
        self._hw_values: list[QLabel] = []
        for i, lbl in enumerate(labels):
            hw_layout.addWidget(QLabel(lbl), i, 0)
            val = QLabel("—")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._hw_values.append(val)
            hw_layout.addWidget(val, i, 1)
        layout.addWidget(hw_group)

        # Last decision summary
        dec_group = QGroupBox("Last Decision")
        dec_layout = QVBoxLayout(dec_group)
        self._decision_summary = QLabel("No scheduling runs yet")
        self._decision_summary.setWordWrap(True)
        dec_layout.addWidget(self._decision_summary)
        layout.addWidget(dec_group)

        layout.addStretch()

    def update_hardware_snapshot(self, snapshot: HardwareSnapshot):
        """Update with a full hardware snapshot (called once at startup)."""
        self._cpu_name_label.setText(snapshot.cpu_name)

        cores = f"{snapshot.cpu_logical_count} logical"
        if snapshot.cpu_physical_count:
            cores += f" / {snapshot.cpu_physical_count} physical"

        gpu_name = snapshot.gpu.name or "Not detected"
        gpu_vendor = snapshot.gpu.vendor or "—"
        if not snapshot.gpu.supported and snapshot.gpu.name:
            gpu_name += "  (telemetry unavailable)"

        values = [
            f"{snapshot.os_name} {snapshot.os_version}",
            snapshot.os_architecture,
            snapshot.python_version,
            cores,
            gpu_name,
            gpu_vendor,
        ]
        for label, value in zip(self._hw_values, values):
            label.setText(value)

        # Initial metrics
        self._update_cpu(snapshot.cpu_utilization_pct)
        self._update_ram(
            snapshot.ram_used_mb, snapshot.ram_total_mb, snapshot.ram_utilization_pct
        )
        self._proc_mem_card.set_value(f"{snapshot.process_memory_mb:.0f} MB")

    def update_metrics(self, data: dict):
        """Update with lightweight periodic metrics."""
        cpu = data.get("cpu_utilization_pct")
        if cpu is not None:
            self._update_cpu(cpu)

        ram_used = data.get("ram_used_mb")
        ram_pct = data.get("ram_utilization_pct")
        if ram_used is not None and ram_pct is not None:
            ram_avail = data.get("ram_available_mb", 0)
            total = ram_used + ram_avail
            self._update_ram(ram_used, total, ram_pct)

        proc = data.get("process_memory_mb")
        if proc is not None:
            self._proc_mem_card.set_value(f"{proc:.0f} MB")

    def _update_cpu(self, pct: float):
        self._cpu_bar.setValue(int(pct))
        self._cpu_card.set_value(f"{pct:.1f}%")
        self._cpu_detail_label.setText(f"{pct:.1f}% utilized")

    def _update_ram(self, used_mb: float, total_mb: float, pct: float):
        self._ram_bar.setValue(int(pct))
        self._ram_card.set_value(f"{pct:.1f}%")
        self._ram_detail_label.setText(
            f"{used_mb / 1024:.1f} GB used / {total_mb / 1024:.1f} GB total"
        )

    def update_run_count(self, count: int):
        self._runs_card.set_value(str(count))

    def update_laya_status(self, status: str):
        self._laya_status_label.setText(f"Laya: {status.replace('_', ' ').title()}")
        if status == "ready":
            self._laya_status_label.setObjectName("status-good")
        elif status in ("loading",):
            self._laya_status_label.setObjectName("status-warn")
        elif status in ("failed", "unavailable"):
            self._laya_status_label.setObjectName("status-bad")
        else:
            self._laya_status_label.setObjectName("status-warn")
        # Force style refresh
        self._laya_status_label.setStyleSheet(self._laya_status_label.styleSheet())
        self._laya_status_label.style().unpolish(self._laya_status_label)
        self._laya_status_label.style().polish(self._laya_status_label)

    def update_last_decision(self, result):
        """Update last decision summary from a SchedulerResult."""
        if not result:
            return
        if result.success:
            text = (
                f"Scenario: {result.scenario_name}\n"
                f"Mode: {result.mode.value} | Objective: {result.objective.value}\n"
                f"Selected: {result.final_choice} (score: {result.final_score:.4f})\n"
                f"Duration: {result.decision_duration_ms:.1f} ms"
            )
            if result.laya_recommendation:
                text += f"\nLaya recommended: {result.laya_recommendation}"
            if result.fallback_used:
                text += f"\n⚠ Fallback used: {result.fallback_reason}"
        else:
            text = (
                f"Scenario: {result.scenario_name}\n"
                f"❌ Failed: {result.failure_reason}"
            )
        self._decision_summary.setText(text)

    def update_mode(self, mode: str):
        """Update the displayed scheduling mode."""
        pass  # Mode shown in scheduler lab
