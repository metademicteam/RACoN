"""Settings and Diagnostics panel."""
from __future__ import annotations

import json
import logging
import platform
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QGroupBox, QPushButton, QComboBox,
    QMessageBox, QFileDialog, QTextEdit,
)

from racon import __version__
from racon.config import get_data_dir, get_log_dir, get_db_path
from racon.scheduler.models import SchedulingObjective, SchedulingMode

logger = logging.getLogger(__name__)


class SettingsPanel(QWidget):
    """Settings, diagnostics, and application information."""

    setting_changed = Signal(str, str)  # key, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("⚙ Settings & Diagnostics")
        title.setObjectName("heading")
        layout.addWidget(title)

        # Default scheduling settings
        sched_group = QGroupBox("Default Scheduling Settings")
        sched_layout = QGridLayout(sched_group)

        sched_layout.addWidget(QLabel("Default Mode:"), 0, 0)
        self._default_mode = QComboBox()
        for mode in SchedulingMode:
            self._default_mode.addItem(
                mode.value.replace("_", " ").title(), mode.value
            )
        self._default_mode.currentIndexChanged.connect(
            lambda: self.setting_changed.emit(
                "default_mode", self._default_mode.currentData()
            )
        )
        sched_layout.addWidget(self._default_mode, 0, 1)

        sched_layout.addWidget(QLabel("Default Objective:"), 1, 0)
        self._default_objective = QComboBox()
        for obj in SchedulingObjective:
            self._default_objective.addItem(
                obj.value.replace("_", " ").title(), obj.value
            )
        self._default_objective.currentIndexChanged.connect(
            lambda: self.setting_changed.emit(
                "default_objective", self._default_objective.currentData()
            )
        )
        sched_layout.addWidget(self._default_objective, 1, 1)

        layout.addWidget(sched_group)

        # Laya settings
        laya_group = QGroupBox("Laya Integration")
        laya_layout = QVBoxLayout(laya_group)
        self._laya_info = QLabel("Laya status: Uninitialized")
        laya_layout.addWidget(self._laya_info)
        self._laya_version = QLabel("Version: Not loaded")
        laya_layout.addWidget(self._laya_version)
        note = QLabel(
            "ℹ Laya initialization downloads model checkpoints from Hugging Face\n"
            "  on first use (~400-800 MB). Internet access is required for the\n"
            "  initial download. Subsequent launches use the cached checkpoint."
        )
        note.setObjectName("metric-label")
        note.setWordWrap(True)
        laya_layout.addWidget(note)
        layout.addWidget(laya_group)

        # Paths and diagnostics
        diag_group = QGroupBox("Paths & Diagnostics")
        diag_layout = QGridLayout(diag_group)

        diag_layout.addWidget(QLabel("Data directory:"), 0, 0)
        data_path = QLabel(str(get_data_dir()))
        data_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        data_path.setWordWrap(True)
        diag_layout.addWidget(data_path, 0, 1)

        diag_layout.addWidget(QLabel("Log directory:"), 1, 0)
        log_path = QLabel(str(get_log_dir()))
        log_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        log_path.setWordWrap(True)
        diag_layout.addWidget(log_path, 1, 1)

        diag_layout.addWidget(QLabel("Database:"), 2, 0)
        db_path = QLabel(str(get_db_path()))
        db_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        db_path.setWordWrap(True)
        diag_layout.addWidget(db_path, 2, 1)

        layout.addWidget(diag_group)

        # Export and actions
        action_layout = QHBoxLayout()

        export_diag_btn = QPushButton("📋  Export Diagnostics")
        export_diag_btn.clicked.connect(self._on_export_diagnostics)
        action_layout.addWidget(export_diag_btn)

        action_layout.addStretch()
        layout.addLayout(action_layout)

        # Version info
        ver_group = QGroupBox("About")
        ver_layout = QVBoxLayout(ver_group)
        ver_layout.addWidget(QLabel(f"RACoN v{__version__}"))
        ver_layout.addWidget(QLabel(f"Python {platform.python_version()}"))

        # Dependencies
        deps = self._get_dependency_versions()
        dep_text = ", ".join(f"{k} {v}" for k, v in deps.items())
        dep_label = QLabel(f"Dependencies: {dep_text}")
        dep_label.setObjectName("metric-label")
        dep_label.setWordWrap(True)
        ver_layout.addWidget(dep_label)

        ver_layout.addWidget(QLabel(
            "⚠ Version 0.1 — Prototype. Scheduling scenarios use simulated data.\n"
            "  No real distributed inference is executed."
        ))
        layout.addWidget(ver_group)

        layout.addStretch()

    def update_laya_info(self, status: str, version: Optional[str] = None):
        self._laya_info.setText(f"Laya status: {status.replace('_', ' ').title()}")
        if version:
            self._laya_version.setText(f"Version: {version}")

    def load_settings(self, settings: dict):
        """Load saved settings into the UI."""
        mode = settings.get("default_mode", "deterministic")
        idx = self._default_mode.findData(mode)
        if idx >= 0:
            self._default_mode.setCurrentIndex(idx)

        objective = settings.get("default_objective", "balanced")
        idx = self._default_objective.findData(objective)
        if idx >= 0:
            self._default_objective.setCurrentIndex(idx)

    def _get_dependency_versions(self) -> dict:
        versions = {}
        for mod_name in ["PySide6", "psutil", "platformdirs", "laya"]:
            try:
                mod = __import__(mod_name)
                versions[mod_name] = getattr(mod, "__version__", "?")
            except ImportError:
                versions[mod_name] = "not installed"
        return versions

    def _on_export_diagnostics(self):
        diag = {
            "app_version": __version__,
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "os": platform.system(),
            "os_version": platform.version(),
            "data_dir": str(get_data_dir()),
            "log_dir": str(get_log_dir()),
            "db_path": str(get_db_path()),
            "dependencies": self._get_dependency_versions(),
        }
        text = json.dumps(diag, indent=2)

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Diagnostics", "racon_diagnostics.json",
            "JSON Files (*.json)"
        )
        if path:
            Path(path).write_text(text, encoding="utf-8")
            QMessageBox.information(self, "Export", f"Diagnostics exported to {path}")
