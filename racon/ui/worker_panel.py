"""Worker Agent Panel — Live worker state and contribution controls.

Fulfills Phase 3 (Gate 3) UI requirements:
Shows live local worker state (not scenario JSON), detects stop/restart,
provides enrollment with invite codes, and resource controls.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QProgressBar, QTextEdit,
    QMessageBox,
)

from racon.agent.worker import RaconAgent
from racon.ui.styles import ACCENT_CYAN, TEXT_SECONDARY, CARD_BG, BORDER_COLOR

logger = logging.getLogger(__name__)


class WorkerAgentPanel(QWidget):
    """UI panel for managing the local RACoN Worker Agent."""

    status_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._agent = RaconAgent(coordinator_url="http://127.0.0.1:8765")
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._refresh_status)

        self._setup_ui()
        self._refresh_status()
        self._poll_timer.start()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Title & Disclaimer Banner
        title = QLabel("🖥 Local Worker Agent & Network Contribution")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #FFFFFF;")
        layout.addWidget(title)

        banner = QLabel(
            "🔒 Public Contributor Mode: Only public workloads accepted. "
            "Worker process runs outbound-only to coordinator without inbound ports."
        )
        banner.setStyleSheet(
            f"background-color: {CARD_BG}; border: 1px solid {BORDER_COLOR}; "
            f"padding: 8px 12px; border-radius: 4px; color: {TEXT_SECONDARY};"
        )
        layout.addWidget(banner)

        # Agent Identity & Status Group
        status_group = QGroupBox("Worker Identity & Trust State")
        status_form = QFormLayout(status_group)

        self._node_id_label = QLabel(self._agent.node_id)
        self._node_id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._node_id_label.setStyleSheet("font-family: monospace; font-size: 13px; color: #64B5F6;")
        status_form.addRow("Node ID:", self._node_id_label)

        pub_display = self._agent._public_key_hex[:16] + "..." + self._agent._public_key_hex[-16:]
        self._pub_key_label = QLabel(pub_display)
        self._pub_key_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._pub_key_label.setStyleSheet("font-family: monospace; color: #AAAAAA;")
        status_form.addRow("Ed25519 Key:", self._pub_key_label)

        self._trust_label = QLabel("OFFLINE")
        self._trust_label.setStyleSheet("font-weight: bold; color: #FF9800;")
        status_form.addRow("Cluster Trust State:", self._trust_label)

        self._run_state_label = QLabel("Stopped")
        self._run_state_label.setStyleSheet("font-weight: bold; color: #E57373;")
        status_form.addRow("Worker Execution:", self._run_state_label)

        layout.addWidget(status_group)

        # Coordinator Connection & Enrollment Group
        conn_group = QGroupBox("Coordinator Enrollment")
        conn_layout = QVBoxLayout(conn_group)

        coord_row = QHBoxLayout()
        coord_row.addWidget(QLabel("Coordinator URL:"))
        self._coord_input = QLineEdit("http://127.0.0.1:8765")
        coord_row.addWidget(self._coord_input)
        conn_layout.addLayout(coord_row)

        invite_row = QHBoxLayout()
        invite_row.addWidget(QLabel("Invite Code:"))
        self._invite_input = QLineEdit()
        self._invite_input.setPlaceholderText("e.g. RCN-ABC123XYZ456")
        invite_row.addWidget(self._invite_input)

        self._enroll_btn = QPushButton("Enroll Node")
        self._enroll_btn.setStyleSheet("background-color: #0288D1; color: white; padding: 6px 14px; font-weight: bold;")
        self._enroll_btn.clicked.connect(self._on_enroll_clicked)
        invite_row.addWidget(self._enroll_btn)
        conn_layout.addLayout(invite_row)

        layout.addWidget(conn_group)

        # Controls & Metrics Group
        control_group = QGroupBox("Worker Lifecycle & Performance Ceilings")
        control_layout = QVBoxLayout(control_group)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("▶ Start Worker")
        self._start_btn.setStyleSheet("background-color: #2E7D32; color: white; padding: 6px 16px; font-weight: bold;")
        self._start_btn.clicked.connect(self._on_start_clicked)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("⏹ Stop Worker")
        self._stop_btn.setStyleSheet("background-color: #C62828; color: white; padding: 6px 16px; font-weight: bold;")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        btn_row.addWidget(self._stop_btn)

        self._pause_btn = QPushButton("⏸ Pause")
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        btn_row.addWidget(self._pause_btn)

        self._resume_btn = QPushButton("⏵ Resume")
        self._resume_btn.clicked.connect(self._on_resume_clicked)
        btn_row.addWidget(self._resume_btn)
        control_layout.addLayout(btn_row)

        # Metrics display
        metrics_form = QFormLayout()
        self._tasks_count_label = QLabel("0 completed / 0 active")
        metrics_form.addRow("Tasks Processed:", self._tasks_count_label)
        self._last_hb_label = QLabel("Never")
        metrics_form.addRow("Last Heartbeat:", self._last_hb_label)
        control_layout.addLayout(metrics_form)

        layout.addWidget(control_group)

        # Event Log
        log_group = QGroupBox("Worker Activity Log")
        log_layout = QVBoxLayout(log_group)
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet("background-color: #121212; font-family: monospace; font-size: 11px;")
        self._log_text.setMaximumHeight(150)
        log_layout.addWidget(self._log_text)
        layout.addWidget(log_group)

        self._append_log("Worker Agent initialized. Ready to enroll or connect.")

    def _append_log(self, text: str):
        self._log_text.append(f"[{time.strftime('%H:%M:%S')}] {text}")

    def _refresh_status(self):
        status = self._agent.get_status()
        self._trust_label.setText(status["trust_state"].upper())

        if status["trust_state"] == "approved":
            self._trust_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        elif status["trust_state"] == "pending":
            self._trust_label.setStyleSheet("font-weight: bold; color: #FF9800;")
        elif status["trust_state"] == "revoked":
            self._trust_label.setStyleSheet("font-weight: bold; color: #F44336;")
        else:
            self._trust_label.setStyleSheet("font-weight: bold; color: #9E9E9E;")

        if status["is_running"]:
            if status["is_paused"]:
                self._run_state_label.setText("Paused")
                self._run_state_label.setStyleSheet("font-weight: bold; color: #FFC107;")
            else:
                self._run_state_label.setText("Running")
                self._run_state_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        else:
            self._run_state_label.setText("Stopped")
            self._run_state_label.setStyleSheet("font-weight: bold; color: #E57373;")

        self._tasks_count_label.setText(
            f"{status['completed_tasks_count']} completed, {status['failed_tasks_count']} failed, "
            f"{status['active_tasks_count']} active"
        )

        if status["last_heartbeat_time"] > 0:
            ago = int(time.time() - status["last_heartbeat_time"])
            self._last_hb_label.setText(f"{ago}s ago")

        self._start_btn.setEnabled(not status["is_running"])
        self._stop_btn.setEnabled(status["is_running"])
        self._pause_btn.setEnabled(status["is_running"] and not status["is_paused"])
        self._resume_btn.setEnabled(status["is_running"] and status["is_paused"])

    def _on_enroll_clicked(self):
        invite_code = self._invite_input.text().strip()
        if not invite_code:
            QMessageBox.warning(self, "Missing Invite Code", "Please enter an invite code to enroll.")
            return

        coord_url = self._coord_input.text().strip()
        self._agent.coordinator_url = coord_url
        self._append_log(f"Attempting enrollment at {coord_url}...")

        success = self._agent.enroll(invite_code)
        if success:
            self._append_log(f"Enrollment successful! Status: {self._agent.status.trust_state.value.upper()}")
            QMessageBox.information(
                self,
                "Enrollment Successful",
                f"Node registered successfully!\nCurrent Status: {self._agent.status.trust_state.value.upper()}\n"
                "If pending approval, wait for the coordinator operator to approve your machine.",
            )
        else:
            err = self._agent.status.last_error or "Unknown error"
            self._append_log(f"Enrollment failed: {err}")
            QMessageBox.critical(self, "Enrollment Failed", f"Could not enroll with coordinator:\n{err}")

        self._refresh_status()

    def _on_start_clicked(self):
        self._agent.coordinator_url = self._coord_input.text().strip()
        self._agent.start()
        self._append_log("Worker started. Emitting heartbeats and polling for tasks.")
        self._refresh_status()

    def _on_stop_clicked(self):
        self._agent.stop()
        self._append_log("Worker stopped.")
        self._refresh_status()

    def _on_pause_clicked(self):
        self._agent.pause()
        self._append_log("Worker paused (no new tasks will be accepted).")
        self._refresh_status()

    def _on_resume_clicked(self):
        self._agent.resume()
        self._append_log("Worker resumed.")
        self._refresh_status()

    def closeEvent(self, event):
        self._poll_timer.stop()
        self._agent.stop()
        super().closeEvent(event)
