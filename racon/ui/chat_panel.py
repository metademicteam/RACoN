"""Interactive Chat Panel for RACoN.

Provides a responsive desktop interface to chat with local GGUF models (e.g. Gemma-3-1B),
distributed RPC worker clusters, or Ollama models without freezing the UI.
"""
from __future__ import annotations

import html
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from racon.runtime.rpc_worker import DistributedPipelineEngine

logger = logging.getLogger(__name__)

DEFAULT_GGUF_MODEL = r"F:\RACoN\Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_Q4_k_m.gguf"
DEFAULT_GATEWAY_URL = "https://racon.metademic.org"


def resolve_local_model_path() -> Optional[str]:
    """Find a usable GGUF model on this PC.

    Order: RACON_GGUF_MODEL, the project path, then the per-user models folder. Split models use
    their first shard, which llama.cpp loads as a whole model.
    """
    candidates: List[Path] = []
    env_path = os.environ.get("RACON_GGUF_MODEL")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path(DEFAULT_GGUF_MODEL))

    try:
        from racon.config import get_data_dir

        models_dir = get_data_dir() / "models"
    except Exception:
        models_dir = None
    if models_dir and models_dir.exists():
        ggufs = sorted(models_dir.rglob("*.gguf"))
        first_shards = [p for p in ggufs if "-00001-of-" in p.name]
        candidates.extend(first_shards)
        candidates.extend(p for p in ggufs if p not in first_shards and "split" not in p.name)

    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def gateway_config() -> Tuple[Optional[str], Optional[str]]:
    """Remote gateway URL and API key from environment or %LOCALAPPDATA%\\RACoN\\racon\\gateway.json."""
    url = os.environ.get("RACON_GATEWAY_URL")
    key = os.environ.get("RACON_API_KEY")
    if url and key:
        return url.rstrip("/"), key

    try:
        from racon.config import get_data_dir

        cfg = get_data_dir() / "gateway.json"
        if cfg.exists():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            url = url or data.get("url")
            key = key or data.get("api_key")
    except Exception as exc:
        logger.warning("Could not read gateway.json: %s", exc)

    return (url.rstrip("/") if url else None), key


def _chat_request(url: str, key: str, model: str, messages: List[Dict[str, str]],
                  max_tokens: int, temperature: float) -> Optional[Dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = httpx.post(
        f"{url}/v1/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages, "max_tokens": max_tokens,
              "temperature": temperature, "stream": False},
        timeout=300.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
    return resp.json()


class PromptTextEdit(QTextEdit):
    """Custom text edit that sends prompt on Enter, inserts newline on Shift+Enter."""
    send_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
                event.accept()
        else:
            super().keyPressEvent(event)


class InferenceWorker(QThread):
    """Background worker thread for generating responses without blocking the UI."""
    token_generated = Signal(str)
    completed = Signal(str, dict)
    failed = Signal(str)

    def __init__(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        inference_mode: str,
        temperature: float = 0.7,
        max_tokens: int = 128,
        rpc_endpoint: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.model_name = model_name
        self.messages = messages
        self.inference_mode = inference_mode
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.rpc_endpoint = rpc_endpoint
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        start_time = time.time()
        # Format prompt
        formatted_prompt = ""
        for m in self.messages:
            role = m.get("role", "user").capitalize()
            content = m.get("content", "")
            formatted_prompt += f"{role}: {content}\n"
        formatted_prompt += "Assistant: "

        reasons: List[str] = []

        # 1. Local GGUF model (GPU, then CPU)
        model_path = resolve_local_model_path()
        if model_path:
            try:
                engine = DistributedPipelineEngine()
                rpc_list = [self.rpc_endpoint] if self.rpc_endpoint else []
                res = engine.execute(
                    model_path=model_path,
                    rpc_endpoints=rpc_list,
                    prompt=formatted_prompt,
                    max_tokens=self.max_tokens,
                    n_gpu_layers=26,
                    timeout_seconds=120.0,
                )
                if res.exit_code != 0:
                    logger.warning("GPU run failed (%s); retrying on CPU.", res.error)
                    res = engine.execute(
                        model_path=model_path,
                        rpc_endpoints=[],
                        prompt=formatted_prompt,
                        max_tokens=self.max_tokens,
                        timeout_seconds=180.0,
                        cpu_only=True,
                    )
                duration = time.time() - start_time
                if res.exit_code == 0:
                    stats = {
                        "prompt_tps": res.prompt_tokens_per_second,
                        "eval_tps": res.generation_tokens_per_second,
                        "duration_s": round(duration, 2),
                        "device": "Distributed RPC" if rpc_list else "Local Host / Vulkan",
                        "memory": res.memory_breakdown,
                    }
                    self.completed.emit(res.response, stats)
                    return
                reasons.append(f"local model failed: {res.error or 'unknown error'}")
            except Exception as e:
                logger.warning("Direct engine execution error: %s", e)
                reasons.append(f"local model error: {e}")
        else:
            reasons.append(f"no GGUF model on this PC (set RACON_GGUF_MODEL, expected {DEFAULT_GGUF_MODEL})")

        # 2. Remote RACoN gateway with an API key (env or gateway.json)
        gw_url, gw_key = gateway_config()
        if gw_url and gw_key:
            try:
                data = _chat_request(gw_url, gw_key, self.model_name, self.messages,
                                     self.max_tokens, self.temperature)
                duration = time.time() - start_time
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                eval_tokens = usage.get("completion_tokens", len(content.split()))
                stats = {
                    "prompt_tps": 0.0,
                    "eval_tps": round(eval_tokens / max(duration, 0.01), 1),
                    "duration_s": round(duration, 2),
                    "device": f"Remote Gateway ({gw_url})",
                    "memory": {},
                }
                self.completed.emit(content, stats)
                return
            except Exception as e:
                logger.warning("Remote gateway failed: %s", e)
                reasons.append(f"remote gateway {gw_url}: {e}")
        else:
            reasons.append("no remote gateway configured (set RACON_API_KEY or %LOCALAPPDATA%\\RACoN\\racon\\gateway.json)")

        # 3. Local backend on this PC (OpenAI-compatible, usually backed by Ollama)
        try:
            data = _chat_request("http://127.0.0.1:8765", "", self.model_name, self.messages,
                                 self.max_tokens, self.temperature)
            duration = time.time() - start_time
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            eval_tokens = usage.get("completion_tokens", len(content.split()))
            stats = {
                "prompt_tps": 0.0,
                "eval_tps": round(eval_tokens / max(duration, 0.01), 1),
                "duration_s": round(duration, 2),
                "device": "RACoN Coordinator Gateway",
                "memory": {},
            }
            self.completed.emit(content, stats)
            return
        except Exception as e:
            reasons.append(f"local coordinator gateway: {e}")

        # 4. Direct Ollama REST fallback
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={
                        "model": self.model_name,
                        "prompt": formatted_prompt,
                        "stream": False,
                    },
                )
                duration = time.time() - start_time
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("response", "")
                    eval_count = data.get("eval_count", len(content.split()))
                    tps = round(eval_count / max(duration, 0.01), 1)
                    stats = {
                        "prompt_tps": 0.0,
                        "eval_tps": tps,
                        "duration_s": round(duration, 2),
                        "device": "Ollama Live Daemon",
                        "memory": {},
                    }
                    self.completed.emit(content, stats)
                    return
                reasons.append(f"ollama HTTP {resp.status_code}")
        except Exception as e:
            reasons.append(f"ollama: {e}")

        duration = time.time() - start_time
        fallback_msg = (
            "[RACoN] Could not generate an answer on this PC. Tried:\n"
            + "\n".join(f"  - {r}" for r in reasons)
            + "\nSet RACON_API_KEY (and optionally RACON_GATEWAY_URL) to use the public gateway, "
              "or place a GGUF model and set RACON_GGUF_MODEL."
        )
        self.completed.emit(fallback_msg, {"eval_tps": 0.0, "duration_s": round(duration, 2), "device": "No backend"})


class ChatPanel(QWidget):
    """Modern dark-themed Chat Panel for RACoN."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._history: List[Dict[str, str]] = []
        self._worker: Optional[InferenceWorker] = None
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header controls
        header_card = QFrame()
        header_card.setObjectName("card")
        header_card.setStyleSheet("background-color: #181825; border: 1px solid #313244; border-radius: 8px; padding: 10px;")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(10, 6, 10, 6)

        title = QLabel("💬 RACoN Chat")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #cba6f7;")
        header_layout.addWidget(title)
        header_layout.addSpacing(20)

        # Model Selector
        header_layout.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems([
            "gemma-3-1b (GGUF Local / Sharded)",
            "llama3.2:1b",
            "qwen2.5:1.5b",
            "deepseek-r1:1.5b",
            "smollm2:360m",
        ])
        self._model_combo.setMinimumWidth(220)
        header_layout.addWidget(self._model_combo)

        # Mode Selector
        header_layout.addWidget(QLabel("Execution Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "Auto / Local Hardware",
            "Distributed RPC Worker",
            "Coordinator Gateway",
        ])
        header_layout.addWidget(self._mode_combo)

        # Max Tokens
        header_layout.addWidget(QLabel("Max Tokens:"))
        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(16, 2048)
        self._max_tokens_spin.setValue(128)
        self._max_tokens_spin.setSingleStep(32)
        header_layout.addWidget(self._max_tokens_spin)

        header_layout.addStretch()

        self._clear_btn = QPushButton("Clear Chat")
        self._clear_btn.clicked.connect(self._on_clear_chat)
        header_layout.addWidget(self._clear_btn)

        main_layout.addWidget(header_card)

        # Conversation Browser
        self._chat_browser = QTextBrowser()
        self._chat_browser.setOpenExternalLinks(True)
        self._chat_browser.setStyleSheet(
            "background-color: #11111b; border: 1px solid #313244; border-radius: 8px; padding: 12px; font-size: 14px;"
        )
        main_layout.addWidget(self._chat_browser, stretch=1)

        # Status / Generating Indicator
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        main_layout.addWidget(self._status_label)

        # Prompt Input Row
        input_container = QFrame()
        input_container.setStyleSheet("background-color: #181825; border: 1px solid #313244; border-radius: 8px; padding: 8px;")
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(10)

        self._prompt_edit = PromptTextEdit()
        self._prompt_edit.setPlaceholderText("Type your prompt or question here... (Enter to send, Shift+Enter for newline)")
        self._prompt_edit.setMaximumHeight(80)
        self._prompt_edit.send_requested.connect(self._on_send)
        self._prompt_edit.setStyleSheet(
            "background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 8px; font-size: 13px;"
        )
        input_layout.addWidget(self._prompt_edit, stretch=1)

        btn_vbox = QVBoxLayout()
        self._send_btn = QPushButton("Send ➤")
        self._send_btn.setObjectName("primary")
        self._send_btn.setStyleSheet(
            "background-color: #cba6f7; color: #11111b; font-weight: bold; font-size: 13px; border-radius: 6px; padding: 8px 18px;"
        )
        self._send_btn.clicked.connect(self._on_send)
        btn_vbox.addWidget(self._send_btn)

        self._stop_btn = QPushButton("Stop ⏹")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet("border-radius: 6px; padding: 4px 10px; font-size: 11px;")
        self._stop_btn.clicked.connect(self._on_stop)
        btn_vbox.addWidget(self._stop_btn)

        input_layout.addLayout(btn_vbox)
        main_layout.addWidget(input_container)

        self._render_welcome()

    def _render_welcome(self):
        welcome_html = """
        <div style="text-align: center; margin-top: 30px; margin-bottom: 20px;">
            <h2 style="color: #cba6f7; margin-bottom: 6px;">Welcome to RACoN Chat</h2>
            <p style="color: #a6adc8; font-size: 13px; max-width: 600px; margin: auto;">
                Chat with on-device models, quantized GGUF weights, or distributed RPC worker clusters.
                Your prompts remain private and processed locally on your Windows PC.
            </p>
        </div>
        """
        self._chat_browser.setHtml(welcome_html)

    def _on_clear_chat(self):
        self._history.clear()
        self._render_welcome()
        self._status_label.setText("Chat cleared. Ready.")

    def _on_stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.terminate()
            self._status_label.setText("Generation stopped by user.")
            self._send_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)

    def _on_send(self):
        text = self._prompt_edit.toPlainText().strip()
        if not text:
            return

        self._prompt_edit.clear()
        self._history.append({"role": "user", "content": text})
        self._append_message_html("user", text)

        model_raw = self._model_combo.currentText().split(" ")[0].strip()
        mode_text = self._mode_combo.currentText()
        max_tokens = self._max_tokens_spin.value()

        self._status_label.setText(f"⚡ Generating response via {model_raw} ({mode_text})...")
        self._send_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        # Launch background worker
        self._worker = InferenceWorker(
            model_name=model_raw,
            messages=list(self._history),
            inference_mode=mode_text,
            max_tokens=max_tokens,
            rpc_endpoint="127.0.0.1:50052" if "rpc" in mode_text.lower() else None,
            parent=self,
        )
        self._worker.completed.connect(self._on_inference_completed)
        self._worker.failed.connect(self._on_inference_failed)
        self._worker.start()

    @Slot(str, dict)
    def _on_inference_completed(self, response: str, stats: dict):
        self._history.append({"role": "assistant", "content": response})
        self._append_message_html("assistant", response, stats)
        self._status_label.setText(
            f"Completed in {stats.get('duration_s', 0)}s ({stats.get('eval_tps', 0)} tokens/s) • {stats.get('device', 'Local')}"
        )
        self._send_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    @Slot(str)
    def _on_inference_failed(self, error: str):
        self._append_message_html("assistant", f"⚠️ Error: {error}")
        self._status_label.setText(f"Error: {error}")
        self._send_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _append_message_html(self, role: str, content: str, stats: Optional[dict] = None):
        escaped = html.escape(content).replace("\n", "<br/>")

        if role == "user":
            msg_html = f"""
            <div style="margin-top: 10px; margin-bottom: 10px; display: flex; justify-content: flex-end;">
                <div style="background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; 
                            border-radius: 10px 10px 2px 10px; padding: 10px 14px; max-width: 80%; display: inline-block;">
                    <b style="color: #cba6f7;">You:</b><br/>
                    {escaped}
                </div>
            </div>
            """
        else:
            badge_html = ""
            if stats:
                tps = stats.get("eval_tps", 0)
                dur = stats.get("duration_s", 0)
                dev = stats.get("device", "Local")
                badge_html = f"""
                <div style="margin-top: 6px; font-size: 11px; color: #a6adc8; border-top: 1px solid #262738; padding-top: 4px;">
                    ⚡ <b>{tps} t/s</b> • {dur}s • <span style="color: #a6e3a1;">{dev}</span>
                </div>
                """

            msg_html = f"""
            <div style="margin-top: 10px; margin-bottom: 10px;">
                <div style="background-color: #181825; color: #cdd6f4; border: 1px solid #313244; 
                            border-radius: 10px 10px 10px 2px; padding: 12px 16px; max-width: 90%; display: inline-block;">
                    <b style="color: #a6e3a1;">RACoN:</b><br/>
                    {escaped}
                    {badge_html}
                </div>
            </div>
            """

        self._chat_browser.append(msg_html)
        # Scroll to bottom
        cursor = self._chat_browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._chat_browser.setTextCursor(cursor)
