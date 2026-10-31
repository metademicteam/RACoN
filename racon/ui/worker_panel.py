"""Worker Agent Panel — Live worker state and contribution controls.

Fulfills Phase 3 (Gate 3) UI requirements:
Shows live local worker state (not scenario JSON), detects stop/restart,
provides enrollment with invite codes, and resource controls.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QProgressBar, QTextEdit,
    QMessageBox, QCheckBox, QScrollArea, QFrame,
)

from racon import netdefaults
from racon.agent.worker import RaconAgent
from racon.ui.styles import ACCENT_CYAN, TEXT_SECONDARY, CARD_BG, BORDER_COLOR

logger = logging.getLogger(__name__)


class WorkerAgentPanel(QWidget):
    """UI panel for managing the local RACoN Worker Agent."""

    status_changed = Signal(str)
    log_message = Signal(str)
    enrollment_finished = Signal(bool, str)
    startup_finished = Signal(bool, str)
    auto_connect_retry = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._agent = RaconAgent(coordinator_url=netdefaults.DEFAULT_COORDINATOR_URL)
        self._starting = False
        self._auto_connect_inflight = False
        self._shard_prep_inflight = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._refresh_status)

        self._setup_ui()
        self.log_message.connect(self._append_log)
        self.enrollment_finished.connect(self._on_enrollment_finished)
        self.startup_finished.connect(self._on_startup_finished)
        self.auto_connect_retry.connect(self._schedule_auto_retry)
        self._refresh_status()
        self._poll_timer.start()

        # Zero-config join: enroll and start the RPC worker shortly after launch.
        if os.environ.get("RACON_AUTO_CONNECT", "1") != "0":
            QTimer.singleShot(1200, self._auto_connect)

    def _setup_ui(self):
        # Keep every group readable when the window is small or the OS uses larger fonts.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Title & Disclaimer Banner
        title = QLabel("🖥 Local Worker Agent & Network Contribution")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #FFFFFF;")
        layout.addWidget(title)

        banner = QLabel(
            "🔒 Public Contributor Mode: Only public workloads accepted. "
            "Worker process runs outbound-only to coordinator without inbound ports."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            f"background-color: {CARD_BG}; border: 1px solid {BORDER_COLOR}; "
            f"padding: 8px 12px; border-radius: 4px; color: {TEXT_SECONDARY};"
        )
        layout.addWidget(banner)

        # Agent Identity & Status Group
        status_group = QGroupBox("Worker Identity & Trust State")
        status_form = QFormLayout(status_group)
        status_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._node_id_label = QLabel(self._agent.node_id)
        self._node_id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._node_id_label.setWordWrap(True)
        self._node_id_label.setStyleSheet("font-family: monospace; font-size: 13px; color: #64B5F6;")
        status_form.addRow("Node ID:", self._node_id_label)

        pub_display = self._agent._public_key_hex[:16] + "..." + self._agent._public_key_hex[-16:]
        self._pub_key_label = QLabel(pub_display)
        self._pub_key_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._pub_key_label.setWordWrap(True)
        self._pub_key_label.setToolTip(self._agent._public_key_hex)
        self._pub_key_label.setStyleSheet("font-family: monospace; color: #AAAAAA;")
        status_form.addRow("Ed25519 Key:", self._pub_key_label)

        self._trust_label = QLabel("OFFLINE")
        self._trust_label.setStyleSheet("font-weight: bold; color: #FF9800;")
        status_form.addRow("Cluster Trust State:", self._trust_label)

        self._run_state_label = QLabel("Stopped")
        self._run_state_label.setStyleSheet("font-weight: bold; color: #E57373;")
        status_form.addRow("Worker Execution:", self._run_state_label)

        self._shard_label = QLabel("Auto-select when the worker starts")
        self._shard_label.setWordWrap(True)
        self._shard_label.setStyleSheet("color: #89b4fa; font-size: 12px;")
        status_form.addRow("Model Layer Shard:", self._shard_label)

        self._rpc_label = QLabel("Not started")
        self._rpc_label.setWordWrap(True)
        self._rpc_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        status_form.addRow("RPC Worker Service:", self._rpc_label)

        self._rpc_detail_label = QLabel("Starts automatically after enrollment.")
        self._rpc_detail_label.setWordWrap(True)
        self._rpc_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._rpc_detail_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        status_form.addRow("RPC Detail:", self._rpc_detail_label)

        layout.addWidget(status_group)

        # Coordinator Connection & Enrollment Group
        conn_group = QGroupBox("Coordinator Enrollment")
        conn_layout = QVBoxLayout(conn_group)

        coord_row = QHBoxLayout()
        coord_row.addWidget(QLabel("Coordinator URL:"))
        self._coord_input = QLineEdit(netdefaults.DEFAULT_COORDINATOR_URL)
        coord_row.addWidget(self._coord_input)
        conn_layout.addLayout(coord_row)

        invite_row = QHBoxLayout()
        invite_row.addWidget(QLabel("Invite Code:"))
        self._invite_input = QLineEdit()
        self._invite_input.setPlaceholderText("Leave blank to join automatically")
        invite_row.addWidget(self._invite_input)

        self._enroll_btn = QPushButton("Enroll Node")
        self._enroll_btn.setStyleSheet("background-color: #0288D1; color: white; padding: 6px 14px; font-weight: bold;")
        self._enroll_btn.clicked.connect(self._on_enroll_clicked)
        invite_row.addWidget(self._enroll_btn)
        conn_layout.addLayout(invite_row)

        self._auto_connect_cb = QCheckBox("Auto-connect on launch (join and start RPC with no invite)")
        self._auto_connect_cb.setChecked(True)
        conn_layout.addWidget(self._auto_connect_cb)

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
        self._log_text.setMaximumHeight(180)
        log_layout.addWidget(self._log_text)
        layout.addWidget(log_group)
        layout.addStretch()

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

        shard_desc = status.get("assigned_shard")
        if shard_desc:
            self._shard_label.setText(shard_desc)
            self._shard_label.setStyleSheet("color: #a6e3a1; font-weight: bold; font-size: 12px;")
        elif self._shard_prep_inflight:
            self._shard_label.setText("Downloading layer shard… (RPC is active)")
            self._shard_label.setStyleSheet("color: #f9e2af; font-weight: bold; font-size: 12px;")
        else:
            self._shard_label.setText("Not downloaded yet — see the activity log")
            self._shard_label.setStyleSheet("color: #89b4fa; font-size: 12px;")

        rpc_end = status.get("rpc_endpoint")
        rpc_error = status.get("rpc_error")
        if rpc_end:
            self._rpc_label.setText(f"● Active ({rpc_end})")
            self._rpc_label.setStyleSheet("color: #a6e3a1; font-weight: bold; font-size: 12px;")
            self._rpc_detail_label.setText("llama.cpp is accepting layer connections from the cluster head.")
            self._rpc_detail_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        elif rpc_error:
            self._rpc_label.setText("● Not running")
            self._rpc_label.setStyleSheet("color: #f38ba8; font-weight: bold; font-size: 12px;")
            log_path = status.get("rpc_log_path") or "log unavailable"
            self._rpc_detail_label.setText(f"{rpc_error}\nLog: {log_path}")
            self._rpc_detail_label.setStyleSheet("color: #f38ba8; font-size: 11px;")
        else:
            self._rpc_label.setText("Not started")
            self._rpc_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
            self._rpc_detail_label.setText("Starts automatically after enrollment.")
            self._rpc_detail_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")

        if status["last_heartbeat_time"] > 0:
            ago = int(time.time() - status["last_heartbeat_time"])
            self._last_hb_label.setText(f"{ago}s ago")

        self._start_btn.setEnabled(not status["is_running"] and not self._starting)
        self._stop_btn.setEnabled(status["is_running"])
        self._pause_btn.setEnabled(status["is_running"] and not status["is_paused"])
        self._resume_btn.setEnabled(status["is_running"] and status["is_paused"])

    def _auto_connect(self):
        """Join and start contributing with no operator input."""
        if (
            not self._auto_connect_cb.isChecked()
            or self._agent.status.is_running
            or self._auto_connect_inflight
        ):
            return
        self._auto_connect_inflight = True
        self.log_message.emit("Auto-connecting to the RACoN network...")

        def _run():
            try:
                ok, msg = self._agent.auto_connect()
                if ok and not self._agent.status.is_running:
                    self._agent.start()
                self.log_message.emit(("Auto-connected: " if ok else "Auto-connect failed: ") + msg)
                if ok:
                    self._start_shard_prep()
                else:
                    self.auto_connect_retry.emit()
            except Exception as exc:
                self.log_message.emit(f"Auto-connect failed: {exc}")
                self.auto_connect_retry.emit()
            finally:
                self._auto_connect_inflight = False

        threading.Thread(target=_run, daemon=True).start()

    def _schedule_auto_retry(self):
        """Retry a failed auto-connect from the GUI thread, without stacking attempts."""
        if self._auto_connect_cb.isChecked() and not self._agent.status.is_running:
            QTimer.singleShot(60000, self._auto_connect)

    def _on_enroll_clicked(self):
        invite_code = self._invite_input.text().strip() or netdefaults.DEFAULT_JOIN_CODE
        coord_url = self._coord_input.text().strip() or netdefaults.DEFAULT_COORDINATOR_URL
        self._agent.coordinator_url = coord_url
        self._enroll_btn.setEnabled(False)
        self.log_message.emit(f"Attempting enrollment at {coord_url}...")

        def _run():
            try:
                success = self._agent.enroll(invite_code)
                err = self._agent.status.last_error or "Unknown error"
            except Exception as exc:
                success, err = False, str(exc)
            self.enrollment_finished.emit(success, err)

        threading.Thread(target=_run, daemon=True).start()

    def _on_enrollment_finished(self, success: bool, err: str):
        self._enroll_btn.setEnabled(True)
        if success:
            self._append_log(f"Enrollment successful! Status: {self._agent.status.trust_state.value.upper()}")
            if not self._agent.status.is_running:
                self._append_log("Starting RPC worker after enrollment...")
                threading.Thread(target=self._start_after_enrollment, daemon=True).start()
            QMessageBox.information(
                self,
                "Enrollment Successful",
                f"Node registered successfully!\nCurrent Status: {self._agent.status.trust_state.value.upper()}\n"
                "The RPC worker starts automatically; if the node is pending, wait for coordinator approval.",
            )
        else:
            self._append_log(f"Enrollment failed: {err}")
            QMessageBox.critical(self, "Enrollment Failed", f"Could not enroll with coordinator:\n{err}")
        self._refresh_status()

    def _start_after_enrollment(self):
        """Manual Enroll Node must also bring up the RPC worker, like auto-connect does."""
        bind = self._agent.detect_bind_ip()
        ok = self._agent.start_rpc_worker(host=bind)
        if ok:
            msg = f"RPC listening on {self._agent.rpc_endpoint}."
            if not self._agent.status.is_running:
                self._agent.start()
                msg += " Worker loop started."
            self.log_message.emit(msg)
            self._start_shard_prep()
            return
        self.log_message.emit(f"RPC did not start: {self._agent.rpc_last_error}")

    def _start_shard_prep(self):
        """Download and verify the layer shard in the background; RPC keeps running."""
        if self._shard_prep_inflight or self._agent.assigned_shard:
            return
        self._shard_prep_inflight = True
        self.log_message.emit("Preparing model layer shard (one-time download; RPC stays active)...")
        threading.Thread(target=self._run_shard_prep, daemon=True).start()

    def _run_shard_prep(self):
        last_reported_mb = 0

        def _progress(downloaded: int, total: int) -> None:
            nonlocal last_reported_mb
            mb = downloaded // (1024 * 1024)
            if mb - last_reported_mb < 25 and downloaded < total:
                return
            last_reported_mb = mb
            total_mb = total / (1024 * 1024) if total else 0.0
            pct = (downloaded / total * 100) if total else 0.0
            self.log_message.emit(
                f"Downloading layer shard: {mb:.0f} MB / {total_mb:.0f} MB ({pct:.0f}%)"
            )

        try:
            ok, msg = self._agent.prepare_worker_shard(progress_callback=_progress)
        except Exception as exc:
            ok, msg = False, f"Model layer shard download failed: {exc}"
        self._shard_prep_inflight = False
        self.log_message.emit(msg)
        if not ok:
            logger.warning(msg)

    def _on_start_clicked(self):
        self._agent.coordinator_url = self._coord_input.text().strip()
        self._starting = True
        self._append_log("Starting worker: preparing hardware and checking layer shard...")
        self._start_btn.setEnabled(False)

        def _worker_startup():
            def _progress(dl, total):
                if total > 0 and (dl % (1024 * 1024 * 25) < (1024 * 512) or dl >= total):
                    pct = (dl / total) * 100
                    self.log_message.emit(
                        f"📥 Downloading layer shard from Hugging Face: "
                        f"{dl / (1024*1024):.1f} MB / {total / (1024*1024):.1f} MB ({pct:.0f}%)"
                    )

            try:
                ok, msg = self._agent.prepare_and_start_rpc_worker(progress_callback=_progress)
            except Exception as exc:
                ok, msg = False, str(exc)
            self.startup_finished.emit(ok, msg)

        threading.Thread(target=_worker_startup, daemon=True).start()
        self._refresh_status()

    def _on_startup_finished(self, ok: bool, msg: str):
        self._starting = False
        self._append_log(f"Worker status: {msg}")
        if ok and not self._agent.status.is_running:
            self._agent.start()
            self._append_log("Worker loop started.")
        elif not ok:
            self._append_log("Worker did not start. Fix the error above and try again.")
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
        self._auto_connect_cb.setChecked(False)
        self._poll_timer.stop()
        self._agent.stop()
        super().closeEvent(event)
