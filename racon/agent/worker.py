"""RACoN Worker Agent Implementation.

Decoupled background worker process executing allow-listed tasks (ADR-010).
Outbound connections only (ADR-005).
Verifies coordinator-signed task envelopes and returns signed results.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from racon import netdefaults

from protocols import (
    PROTOCOL_VERSION,
    NodeRole,
    TrustState,
    TaskType,
    TaskState,
    HardwareCapabilities,
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatMessage,
    HeartbeatAck,
    TaskEnvelope,
    TaskResultEnvelope,
    sign_message,
    verify_signature,
    load_public_key_from_hex,
)
from racon.hardware import discover_hardware, get_lightweight_update
from racon.agent.crypto_store import AgentKeyStore

logger = logging.getLogger(__name__)


class _OllamaUnavailable(RuntimeError):
    """Signal that the Ollama chat path cannot serve this request."""


class _OllamaModelIncompatible(RuntimeError):
    """The model exists but cannot accept this request (for example, tools)."""


def _ollama_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        payload = {
            key: value
            for key, value in message.items()
            if key in ("role", "content", "name", "tool_calls", "tool_call_id") and value is not None
        }
        content = payload.get("content")
        if isinstance(content, list):
            text_parts: List[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "")
                if part_type in ("text", "input_text") and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                elif part_type in ("image_url", "input_image", "image", "audio_url", "input_audio"):
                    raise _OllamaUnavailable("Ollama cannot accept OpenAI media content parts")
            payload["content"] = "\n".join(text_parts)
        converted.append(payload)
    return converted


class AgentStatus:
    def __init__(self) -> None:
        self.node_id: str = ""
        self.public_key_hex: str = ""
        self.trust_state: TrustState = TrustState.OFFLINE
        self.coordinator_url: str = "http://127.0.0.1:8765"
        self.is_running: bool = False
        self.is_paused: bool = False
        self.active_tasks_count: int = 0
        self.completed_tasks_count: int = 0
        self.failed_tasks_count: int = 0
        self.last_heartbeat_time: float = 0.0
        self.max_cpu_util_pct: float = 85.0
        self.min_free_ram_mb: float = 1024.0
        self.last_error: Optional[str] = None


class RaconAgent:
    def __init__(
        self,
        coordinator_url: str = "http://127.0.0.1:8765",
        key_store: Optional[AgentKeyStore] = None,
        node_id_override: Optional[str] = None,
    ) -> None:
        self.coordinator_url = coordinator_url.rstrip("/")
        self._key_store = key_store or AgentKeyStore()
        self._private_key, self._public_key_hex = self._key_store.load_or_create_keypair()

        # Deterministic node_id from public key hash if not overridden
        import hashlib
        short_hash = hashlib.sha256(self._public_key_hex.encode("utf-8")).hexdigest()[:12]
        self.node_id = node_id_override or f"node-{short_hash}"

        self.status = AgentStatus()
        self.status.node_id = self.node_id
        self.status.public_key_hex = self._public_key_hex
        self.status.coordinator_url = self.coordinator_url

        self._session_token: Optional[str] = None
        self._coordinator_public_key_hex: Optional[str] = None
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._current_task_id: Optional[str] = None
        self.rpc_server: Optional[Any] = None
        self.rpc_endpoint: Optional[str] = None
        self.rpc_last_error: Optional[str] = None
        self.assigned_shard: Optional[Any] = None
        self.assigned_shard_path: Optional[str] = None
        self._join_code: Optional[str] = None
        self._rpc_bind: Optional[str] = None
        self.gateway_key: Optional[str] = None

    def _advertised_models(self) -> list[str]:
        """Describe the models this worker can serve.

        Ollama tags are read from the local daemon; GGUF ids come from the RACoN model
        manager. The list is a routing hint, bounded to the protocol's 64 entries.
        """
        models: list[str] = []
        try:
            response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.5)
            if response.status_code == 200:
                for item in response.json().get("models", []) or []:
                    name = item.get("name") or item.get("model")
                    if name and str(name) not in models:
                        models.append(str(name))
        except Exception:
            pass
        try:
            from racon.models.manager import ModelManager
            from racon.settings import get_settings

            for installed in ModelManager(get_settings().resolved_models_dir).installed():
                if installed.id not in models:
                    models.append(installed.id)
        except Exception:
            pass
        return models[:64]

    def enroll(self, invite_code: str) -> bool:
        """Enroll with coordinator using an invite code."""
        # Accept the base URL even if the user pasted it with /enroll or a trailing slash.
        base = self.coordinator_url.strip().rstrip("/")
        if base.lower().endswith("/enroll"):
            base = base[: -len("/enroll")]
        self.coordinator_url = base
        self.status.coordinator_url = base

        hw = discover_hardware()
        caps = HardwareCapabilities(
            cpu_name=hw.cpu_name,
            cpu_cores=max(1, hw.cpu_physical_count or hw.cpu_logical_count or 1),
            ram_total_mb=max(hw.ram_total_mb, 1.0),
            ram_available_mb=max(hw.ram_available_mb, 0.0),
            gpu_name=hw.gpu.name,
            gpu_vram_mb=hw.gpu.vram_total_mb,
            runtimes=["ollama", "local"],
            os_name=hw.os_name[:128],
            is_simulated=False,
            rpc_endpoint=(
                self.rpc_endpoint
                or (f"{self._rpc_bind}:{netdefaults.rpc_port()}" if self._rpc_bind else None)
            ),
        )

        self._join_code = invite_code
        now = time.time()
        # Build the request first, then sign exactly the bytes the coordinator verifies.
        # The model inventory is included only when non-empty, so older agents keep a
        # compatible canonical payload.
        req = EnrollmentRequest(
            node_id=self.node_id,
            public_key_hex=self._public_key_hex,
            invite_code=invite_code,
            role=NodeRole.CONTRIBUTOR,
            capabilities=caps,
            protocol_version=PROTOCOL_VERSION,
            timestamp=now,
            models=self._advertised_models(),
            features=["text_generation", "streaming_chat"],
            signature="0" * 128,
        )
        req.signature = sign_message(self._private_key, req.payload_bytes())

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(f"{self.coordinator_url}/enroll", json=req.model_dump())
                if resp.status_code == 200:
                    data = resp.json()
                    enroll_resp = EnrollmentResponse(**data)
                    self.status.trust_state = enroll_resp.trust_state
                    self._session_token = enroll_resp.session_token
                    self._coordinator_public_key_hex = enroll_resp.coordinator_public_key
                    self.status.last_error = None
                    if enroll_resp.gateway_key:
                        self.gateway_key = enroll_resp.gateway_key
                        self._save_gateway_config(enroll_resp.gateway_key)
                    logger.info("Enrollment succeeded: state=%s", enroll_resp.trust_state.value)
                    return True
                else:
                    try:
                        body = resp.json()
                        detail = body.get("detail") or body.get("message") or ""
                    except Exception:
                        detail = resp.text.strip()
                    detail = detail or resp.reason_phrase
                    if resp.status_code >= 500:
                        self.status.last_error = (
                            f"Coordinator unavailable (HTTP {resp.status_code} {detail}). "
                            "The RACoN head node may be offline; retrying automatically."
                        )
                    else:
                        self.status.last_error = (
                            f"Enrollment HTTP error: {resp.status_code} {detail}"
                        )
                    logger.error(self.status.last_error)
                    return False
        except Exception as e:
            self.status.last_error = f"Enrollment connection error: {e}"
            logger.error(self.status.last_error)
            return False

    def _save_gateway_config(self, api_key: str) -> None:
        """Write gateway.json so chat works on this PC without manual setup."""
        from racon.config import get_data_dir

        try:
            path = get_data_dir() / "gateway.json"
            path.write_text(
                json.dumps({"url": netdefaults.DEFAULT_GATEWAY_URL, "api_key": api_key}, indent=2),
                encoding="utf-8",
            )
            logger.info("Saved gateway API key to %s", path)
        except Exception as exc:
            logger.warning("Could not save gateway config: %s", exc)

    @staticmethod
    def detect_bind_ip() -> str:
        """Bind address for rpc-server: the Tailscale IP, else the LAN IP, never 0.0.0.0."""
        exe = shutil.which("tailscale")
        if exe:
            try:
                out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5)
                cgnat = ipaddress.ip_network("100.64.0.0/10")
                for line in out.stdout.splitlines():
                    ip = line.strip()
                    try:
                        if ipaddress.ip_address(ip) in cgnat:
                            return ip
                    except ValueError:
                        continue
            except Exception:
                pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        return "127.0.0.1"

    def auto_connect(
        self,
        join_code: Optional[str] = None,
        coordinator_url: Optional[str] = None,
        rpc_bind: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Enroll and start the RPC worker with no operator input.

        Used on app launch and at desktop login. The coordinator URL and join code default to the
        values baked into the installer and can be overridden by environment variables.
        """
        if coordinator_url:
            self.coordinator_url = coordinator_url.strip().rstrip("/")
        else:
            self.coordinator_url = netdefaults.default_coordinator_url()
        self.status.coordinator_url = self.coordinator_url

        code = join_code or os.environ.get("RACON_JOIN_CODE") or netdefaults.DEFAULT_JOIN_CODE
        bind = rpc_bind or self.detect_bind_ip()
        if bind in ("0.0.0.0", "::", ""):
            bind = "127.0.0.1"
        self._rpc_bind = bind

        if not self.enroll(code):
            return False, f"Auto-join failed: {self.status.last_error or 'unknown error'}"

        message = f"Joined {self.coordinator_url} as {self.node_id} ({self.status.trust_state.value})."
        if self.start_rpc_worker(host=bind, port=netdefaults.rpc_port()):
            message += f" RPC listening on {self.rpc_endpoint}."
        else:
            reason = (self.rpc_last_error or "unknown reason").splitlines()[0]
            message += (
                f" RPC server did not start: {reason} "
                "The node can still run coordinator tasks."
            )
        return True, message

    def send_heartbeat(self) -> bool:
        """Send periodic heartbeat to coordinator."""
        hw_update = get_lightweight_update()
        now = time.time()
        nonce = secrets.token_hex(16)

        active_tasks = [self._current_task_id] if self._current_task_id else []
        msg = HeartbeatMessage(
            node_id=self.node_id,
            timestamp=now,
            nonce=nonce,
            cpu_util_pct=hw_update["cpu_utilization_pct"],
            ram_available_mb=hw_update["ram_available_mb"],
            active_tasks=active_tasks,
            models=self._advertised_models(),
            models_reported=True,
            signature="0" * 128,
        )
        msg.signature = sign_message(self._private_key, msg.payload_bytes())

        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(f"{self.coordinator_url}/heartbeat", json=msg.model_dump())
                if resp.status_code == 200:
                    ack = HeartbeatAck(**resp.json())
                    self.status.last_heartbeat_time = time.time()
                    if ack.status == "revoked":
                        self.status.trust_state = TrustState.REVOKED
                    elif ack.status == "ok" and self.status.trust_state == TrustState.OFFLINE:
                        self.status.trust_state = TrustState.APPROVED
                    if "re_enroll" in ack.commands and self._join_code:
                        # The coordinator restarted and forgot this node; rejoin automatically.
                        logger.info("Coordinator asked for re-enrollment; rejoining.")
                        self.enroll(self._join_code)
                    return True
        except Exception as e:
            logger.debug("Heartbeat error: %s", e)
        return False

    def check_and_execute_task(self) -> bool:
        """Poll coordinator for assigned tasks and execute allow-listed workload."""
        if not self._session_token or self.status.is_paused:
            return False

        try:
            with httpx.Client(timeout=5.0) as client:
                claim_resp = client.post(
                    f"{self.coordinator_url}/tasks/claim",
                    json={"node_id": self.node_id, "session_token": self._session_token},
                )
                if claim_resp.status_code == 401:
                    # Session tokens expire after 15 minutes. Re-enroll to refresh instead of
                    # silently returning 401 forever while the worker still shows as running.
                    logger.warning("Task claim rejected (session expired); re-enrolling.")
                    if self._join_code:
                        self.enroll(self._join_code)
                    return False
                if claim_resp.status_code != 200:
                    return False
                data = claim_resp.json()
                if not data.get("has_task"):
                    return False

                task_data = data["task"]
                task_id = task_data["task_id"]
                task_type = task_data["task_type"]
                model_name = task_data["model_name"]
                parameters = task_data["parameters"]

                if not self._verify_coordinator_task(task_data):
                    logger.error("Rejected task %s: coordinator signature is invalid", task_id)
                    return False

                logger.info("Claimed task %s (type=%s)", task_id, task_type)
                self.status.active_tasks_count += 1
                self._current_task_id = task_id
                start_time = time.monotonic()

                # Execute allow-listed task
                try:
                    result_data, error = self._execute_task_workload(
                        task_type, model_name, parameters, task_id=task_id
                    )
                finally:
                    self._current_task_id = None
                duration_ms = (time.monotonic() - start_time) * 1000

                # Formulate signed TaskResultEnvelope
                status_enum = TaskState.FAILED if error else TaskState.COMPLETED
                from protocols.crypto import canonicalize_json
                payload = canonicalize_json({
                    "task_id": task_id,
                    "node_id": self.node_id,
                    "status": status_enum.value,
                    "result_data": result_data,
                    "execution_time_ms": duration_ms,
                    "error": error,
                })
                worker_sig = sign_message(self._private_key, payload)

                result_envelope = TaskResultEnvelope(
                    task_id=task_id,
                    node_id=self.node_id,
                    status=status_enum,
                    result_data=result_data,
                    execution_time_ms=duration_ms,
                    error=error,
                    worker_signature=worker_sig,
                )

                # Report result
                report_resp = client.post(f"{self.coordinator_url}/tasks/report", json=result_envelope.model_dump())
                self.status.active_tasks_count -= 1
                if not error and report_resp.status_code == 200:
                    self.status.completed_tasks_count += 1
                    logger.info("Successfully completed and reported task %s", task_id)
                    return True
                else:
                    self.status.failed_tasks_count += 1
                    return False
        except Exception as e:
            logger.error("Task execution/claim cycle error: %s", e)
            return False

    def _verify_coordinator_task(self, task_data: Dict[str, Any]) -> bool:
        """Verify the coordinator's Ed25519 signature over the claimed task envelope."""
        if not self._coordinator_public_key_hex:
            logger.warning("No coordinator public key stored; rejecting unverifiable task")
            return False
        try:
            envelope = TaskEnvelope.model_validate(task_data)
            coordinator_pub = load_public_key_from_hex(self._coordinator_public_key_hex)
            return verify_signature(
                coordinator_pub,
                envelope.payload_bytes(),
                envelope.coordinator_signature,
            )
        except Exception as exc:
            logger.error("Coordinator signature verification failed: %s", exc)
            return False

    def _execute_task_workload(
        self,
        task_type: str,
        model_name: str,
        parameters: Dict[str, Any],
        task_id: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Strictly allow-listed task execution (ADR-007, Rule 2).
        
        Zero arbitrary code execution or unpickling.
        """
        # 0. Streaming OpenAI chat completion
        if task_type == TaskType.CHAT_COMPLETION.value or task_type == "chat_completion":
            if not task_id:
                return {}, "chat_completion requires a task id"
            return self._chat_completion(model_name, parameters, task_id)

        # 1. Benchmark Probe
        if task_type == TaskType.BENCHMARK_PROBE.value or task_type == "benchmark_probe":
            # Deterministic integer loop: a reproducible CPU throughput probe.
            iterations = int(parameters.get("iterations", 500000))
            start = time.perf_counter()
            checksum = sum(i * i for i in range(iterations))
            elapsed = time.perf_counter() - start
            return {
                "probe_result": checksum,
                "iterations": iterations,
                "compute_time_seconds": elapsed,
                "metric": "integer_loop_throughput",
                "mode": "MEASURED",
            }, None

        # 2. Text Completion
        elif task_type == TaskType.TEXT_COMPLETION.value or task_type == "text_completion":
            prompt = parameters.get("prompt", "Hello RACoN")
            max_tokens = int(parameters.get("max_tokens", 512) or 512)
            timeout_s = float(parameters.get("timeout_s", 300.0))

            # Preferred path: a local Ollama daemon. This keeps the worker contract
            # identical on machines that already run Ollama.
            ollama_error: Optional[str] = None
            try:
                resp = httpx.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={
                        "model": model_name,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": max_tokens},
                    },
                    timeout=timeout_s,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get("response", "")
                    if text:
                        return {
                            "response": text,
                            "eval_count": data.get("eval_count", 0),
                            "prompt_eval_count": data.get("prompt_eval_count", 0),
                            "runtime": "ollama",
                            "mode": "OBSERVED",
                        }, None
                    ollama_error = "Ollama returned an empty completion"
                else:
                    ollama_error = f"Ollama returned HTTP {resp.status_code}"
            except Exception as exc:
                ollama_error = f"Ollama unreachable: {exc}"

            # Fallback: this worker's own RACoN engine (llama.cpp GGUF or Ollama
            # adapter). Without this a Windows worker with llama.cpp installed could join
            # the network but never complete a task. A failure is still reported as a
            # failure; no placeholder text is ever produced.
            try:
                text, usage, runtime = self._engine_completion(
                    model_name, prompt, max_tokens, timeout_s
                )
            except Exception as exc:
                return {}, (
                    f"No inference runtime reachable on this worker. {ollama_error}. "
                    f"Local engine error: {type(exc).__name__}: {exc}"
                )

            if not text:
                return {}, f"Local engine returned an empty completion ({ollama_error})."

            return {
                "response": text,
                "eval_count": int(usage.get("completion_tokens") or 0),
                "prompt_eval_count": int(usage.get("prompt_tokens") or 0),
                "runtime": runtime,
                "mode": "OBSERVED",
            }, None

        # 3. Distributed Layer Inference (ADR-012)
        elif task_type == TaskType.DISTRIBUTED_INFERENCE.value or task_type == "distributed_inference":
            from racon.runtime.rpc_worker import DistributedPipelineEngine
            rpc_endpoints = parameters.get("rpc_endpoints", [])
            prompt = parameters.get("prompt", "Hello RACoN")
            model_path = parameters.get("model_path", model_name)
            max_tokens = int(parameters.get("max_tokens", 32))
            n_gpu_layers = int(parameters.get("n_gpu_layers", 26))

            engine = DistributedPipelineEngine()
            result = engine.execute(
                model_path=model_path,
                rpc_endpoints=rpc_endpoints,
                prompt=prompt,
                max_tokens=max_tokens,
                n_gpu_layers=n_gpu_layers,
            )

            if result.error:
                return {}, result.error

            return {
                "response": result.response,
                "prompt_tps": result.prompt_tokens_per_second,
                "eval_tps": result.generation_tokens_per_second,
                "total_duration_s": result.total_duration_seconds,
                "memory_breakdown": result.memory_breakdown,
                "rpc_endpoints": result.rpc_endpoints,
                "layers_offloaded": result.layers_offloaded,
                "runtime": "llama_rpc_distributed",
                "mode": "OBSERVED",
            }, None

        # 4. Shard Verification (ADR-012)
        elif task_type == TaskType.SHARD_VERIFICATION.value or task_type == "shard_verification":
            from racon.runtime.distributed_gguf import verify_shard
            shard_path = parameters.get("shard_path", "")
            expected_sha256 = parameters.get("expected_sha256", "")

            valid = verify_shard(shard_path, expected_sha256)
            return {
                "shard_path": shard_path,
                "valid": valid,
                "mode": "OBSERVED",
            }, None if valid else f"Shard integrity check failed for {shard_path}"

        else:
            return {}, f"Disallowed task type: '{task_type}'. Workers accept allow-listed tasks only."

    def _chat_completion(
        self,
        model_name: str,
        parameters: Dict[str, Any],
        task_id: str,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Stream an OpenAI chat completion and push deltas to the coordinator."""
        import asyncio

        messages = parameters.get("messages") or []
        options = parameters.get("options") or {}
        if not messages:
            return {}, "chat_completion requires messages"
        timeout_s = float(parameters.get("timeout_s", 600.0) or 600.0)

        # Preferred path: the worker's Ollama daemon. It is already resident, streams
        # natively, and avoids binding an async HTTP client to a transient event loop.
        ollama_error: Optional[str] = None
        try:
            return self._ollama_chat_stream(
                model_name, messages, options, task_id, timeout_s
            )
        except _OllamaModelIncompatible as exc:
            # The model exists but cannot satisfy this request. Falling back to the
            # engine would only produce a misleading "model not installed" error.
            return {}, str(exc)
        except _OllamaUnavailable as exc:
            ollama_error = str(exc)
            logger.debug("Ollama chat streaming unavailable for %s: %s", task_id, ollama_error)
        except Exception as exc:
            ollama_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Ollama chat streaming failed for %s: %s", task_id, ollama_error)

        async def _run() -> tuple[Dict[str, Any], Optional[str]]:
            import httpx

            from racon.runtime.types import ChatMessage
            from racon.services.engine_service import get_engine

            engine = get_engine()
            chat_messages = [
                ChatMessage(
                    role=item.get("role", "user"),
                    content=item.get("content"),
                    name=item.get("name"),
                    tool_calls=item.get("tool_calls"),
                    tool_call_id=item.get("tool_call_id"),
                )
                for item in messages
                if isinstance(item, dict)
            ]

            kwargs = {
                "model": model_name,
                "max_tokens": int(options.get("max_tokens", 512) or 512),
                "temperature": float(options.get("temperature", 0.7) or 0.7),
                "top_p": float(options.get("top_p", 0.95) or 0.95),
                "stop": options.get("stop"),
                "tools": options.get("tools"),
                "tool_choice": options.get("tool_choice"),
                "response_format": options.get("response_format"),
                "seed": options.get("seed"),
                "presence_penalty": options.get("presence_penalty"),
                "frequency_penalty": options.get("frequency_penalty"),
                "parallel_tool_calls": options.get("parallel_tool_calls"),
            }

            text_parts: list[str] = []
            tool_calls: list[Dict[str, Any]] = []
            usage: Dict[str, Any] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "reported": False,
            }
            finish_reason: Optional[str] = None
            seq = 0
            buffer = ""
            last_flush = time.monotonic()

            async with httpx.AsyncClient(timeout=5.0) as client:
                async def send(delta: Dict[str, Any], finish: Optional[str] = None) -> None:
                    nonlocal seq, last_flush
                    if not delta and not finish:
                        return
                    try:
                        await client.post(
                            f"{self.coordinator_url}/tasks/progress",
                            json={
                                "node_id": self.node_id,
                                "task_id": task_id,
                                "session_token": self._session_token or "",
                                "seq": seq,
                                "delta": delta,
                                "finish_reason": finish,
                            },
                        )
                    except Exception as exc:
                        logger.debug("Progress post failed for %s: %s", task_id, exc)
                    seq += 1
                    last_flush = time.monotonic()

                async def flush(force: bool = False, finish: Optional[str] = None) -> None:
                    nonlocal buffer
                    if not buffer and not finish:
                        return
                    delta: Dict[str, Any] = {}
                    if buffer:
                        delta["content"] = buffer
                        buffer = ""
                    await send(delta, finish)

                async def _stream() -> None:
                    nonlocal finish_reason, usage
                    async for chunk in engine.chat_stream(chat_messages, **kwargs):
                        if chunk.delta:
                            text_parts.append(chunk.delta)
                            buffer += chunk.delta
                            if len(buffer) >= 400 or (time.monotonic() - last_flush) >= 0.08:
                                await flush()
                        if chunk.tool_calls:
                            if buffer:
                                await flush()
                            tool_calls.extend(chunk.tool_calls)
                            await send({"tool_calls": chunk.tool_calls})
                        if chunk.usage is not None:
                            usage = chunk.usage.to_dict()
                        if chunk.finish_reason:
                            finish_reason = chunk.finish_reason
                    await flush(finish=finish_reason or "stop")

                try:
                    await asyncio.wait_for(_stream(), timeout=timeout_s)
                except asyncio.TimeoutError:
                    return {}, f"chat completion timed out after {timeout_s:.0f}s"
                except Exception as exc:
                    return {}, f"{type(exc).__name__}: {exc}"

            text = "".join(text_parts)
            if not text and not tool_calls:
                return {}, "runtime returned an empty chat completion"
            return (
                {
                    "response": text,
                    "tool_calls": tool_calls or None,
                    "usage": usage,
                    "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
                    "runtime": engine.runtime_name,
                    "mode": "OBSERVED",
                },
                None,
            )

        try:
            result = asyncio.run(_run())
            if result[1] and ollama_error:
                return {}, f"{result[1]} (Ollama: {ollama_error})"
            return result
        except Exception as exc:
            logger.exception("chat_completion task %s failed", task_id)
            detail = f"{type(exc).__name__}: {exc}"
            if ollama_error:
                detail += f" (Ollama: {ollama_error})"
            return {}, detail

    def _ollama_chat_stream(
        self,
        model_name: str,
        messages: List[Dict[str, Any]],
        options: Dict[str, Any],
        task_id: str,
        timeout_s: float,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Stream a chat completion through the local Ollama daemon."""
        import json

        from racon.models.catalog import ollama_tag_for
        from racon.runtime.tool_calls import normalise_tool_calls

        ollama_model = ollama_tag_for(model_name) or model_name
        body: Dict[str, Any] = {
            "model": ollama_model,
            "messages": _ollama_messages(messages),
            "stream": True,
            "options": {
                "temperature": float(options.get("temperature", 0.7) or 0.7),
                "top_p": float(options.get("top_p", 0.95) or 0.95),
                "num_predict": int(options.get("max_tokens", 512) or 512),
            },
        }
        if options.get("stop"):
            body["options"]["stop"] = options["stop"]
        if options.get("seed") is not None:
            body["options"]["seed"] = options["seed"]
        if options.get("tools"):
            body["tools"] = options["tools"]
        response_format = options.get("response_format") or {}
        if response_format.get("type") == "json_object":
            body["format"] = "json"
        elif response_format.get("type") == "json_schema":
            schema = (response_format.get("json_schema") or {}).get("schema")
            if schema:
                body["format"] = schema

        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        usage: Dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "reported": False,
        }
        finish_reason: Optional[str] = None
        seq = 0
        buffer = ""
        last_flush = time.monotonic()

        progress_client = httpx.Client(timeout=5.0)

        def send(delta: Dict[str, Any], finish: Optional[str] = None) -> None:
            nonlocal seq, last_flush
            if not delta and not finish:
                return
            try:
                response = progress_client.post(
                    f"{self.coordinator_url}/tasks/progress",
                    json={
                        "node_id": self.node_id,
                        "task_id": task_id,
                        "session_token": self._session_token or "",
                        "seq": seq,
                        "delta": delta,
                        "finish_reason": finish,
                    },
                )
                if response.status_code == 401:
                    # The heartbeat renews the token, so this only happens if the token was
                    # invalidated or the coordinator restarted. Re-enrol before the next
                    # delta instead of dropping the whole stream silently.
                    logger.warning("Progress rejected (session expired); re-enrolling.")
                    if self._join_code:
                        self.enroll(self._join_code)
            except Exception as exc:
                logger.debug("Progress post failed for %s: %s", task_id, exc)
            seq += 1
            last_flush = time.monotonic()

        def flush(force: bool = False, finish: Optional[str] = None) -> None:
            nonlocal buffer
            if not buffer and not finish:
                return
            delta: Dict[str, Any] = {}
            if buffer:
                delta["content"] = buffer
                buffer = ""
            send(delta, finish)

        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10.0)) as client:
                with client.stream(
                    "POST", "http://127.0.0.1:11434/api/chat", json=body
                ) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", "replace")[:512]
                        if (
                            response.status_code == 400
                            and "does not support tools" in body.lower()
                        ):
                            raise _OllamaModelIncompatible(
                                f"Ollama model '{ollama_model}' does not support tool "
                                "calling. Use a tool-capable model such as "
                                "qwen2.5:0.5b or qwen2.5:1.5b, or send the request "
                                "without tools."
                            )
                        raise _OllamaUnavailable(
                            f"Ollama returned HTTP {response.status_code}: {body}"
                        )
                    for line in response.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        message = event.get("message") or {}
                        content = message.get("content") or ""
                        calls = message.get("tool_calls")
                        if content:
                            text_parts.append(str(content))
                            buffer += str(content)
                            if len(buffer) >= 400 or (time.monotonic() - last_flush) >= 0.08:
                                flush()
                        if calls:
                            if buffer:
                                flush()
                            normalised_calls = normalise_tool_calls(calls)
                            tool_calls.extend(normalised_calls)
                            send({"tool_calls": normalised_calls})
                        if event.get("done"):
                            prompt_tokens = int(event.get("prompt_eval_count") or 0)
                            completion_tokens = int(event.get("eval_count") or 0)
                            usage = {
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": prompt_tokens + completion_tokens,
                                "reported": True,
                            }
                            finish_reason = event.get("done_reason") or "stop"
                            break

            text = "".join(text_parts)
            if not text and not tool_calls:
                raise _OllamaUnavailable("Ollama returned an empty chat completion")
            final_reason = "tool_calls" if tool_calls else (finish_reason or "stop")
            flush(finish=final_reason)
            return (
                {
                    "response": text,
                    "tool_calls": tool_calls or None,
                    "usage": usage,
                    "finish_reason": final_reason,
                    "runtime": "ollama",
                    "mode": "OBSERVED",
                },
                None,
            )
        except httpx.HTTPError as exc:
            raise _OllamaUnavailable(f"Ollama unreachable: {exc}") from exc
        finally:
            progress_client.close()

    def _engine_completion(
        self, model_name: str, prompt: str, max_tokens: int, timeout_s: float
    ) -> tuple[str, Dict[str, Any], str]:
        """Run a completion through this worker's local RACoN engine.

        The engine selects llama.cpp or Ollama from this machine's settings, so a worker
        with a GGUF installed can serve distributed tasks without a separate Ollama daemon.
        """
        import asyncio

        from racon.runtime.types import ChatMessage
        from racon.services.engine_service import get_engine

        async def _run():
            engine = get_engine()
            result = await asyncio.wait_for(
                engine.chat(
                    [ChatMessage(role="user", content=prompt)],
                    model=model_name,
                    max_tokens=max_tokens,
                ),
                timeout=timeout_s,
            )
            return result, engine.runtime_name

        result, runtime_name = asyncio.run(_run())
        return result.text or "", result.usage.to_dict(), result.runtime or runtime_name

    def start(self, heartbeat_interval_s: float = 5.0) -> None:
        """Start the background worker thread."""
        if self.status.is_running:
            return

        self._stop_event.clear()
        self.status.is_running = True

        def _heartbeat_loop():
            logger.info("RACoN Agent heartbeat loop started for node %s", self.node_id)
            while not self._stop_event.is_set():
                try:
                    self.send_heartbeat()
                except Exception as exc:  # heartbeat must never kill the loop
                    logger.debug("Heartbeat loop error: %s", exc)
                self._stop_event.wait(timeout=heartbeat_interval_s)
            logger.info("RACoN Agent heartbeat loop stopped")

        def _task_loop():
            logger.info("RACoN Agent task loop started for node %s", self.node_id)
            poll_interval = max(0.25, heartbeat_interval_s / 5.0)
            while not self._stop_event.is_set():
                try:
                    self.check_and_execute_task()
                except Exception as exc:
                    logger.error("Task loop error: %s", exc)
                self._stop_event.wait(timeout=poll_interval)
            logger.info("RACoN Agent task loop stopped")

        self._heartbeat_thread = threading.Thread(
            target=_heartbeat_loop, daemon=True, name="RACoNAgentHeartbeat"
        )
        self._worker_thread = threading.Thread(
            target=_task_loop, daemon=True, name="RACoNAgentWorker"
        )
        self._heartbeat_thread.start()
        self._worker_thread.start()

    def prepare_worker_shard(self, progress_callback: Optional[Any] = None) -> tuple[bool, str]:
        """Download and verify this worker's layer shard without interrupting RPC."""
        from racon.runtime.distributed_gguf import ensure_worker_shard
        from racon.hardware import discover_hardware

        try:
            hw = discover_hardware()
            avail_ram = hw.ram_available_mb
            logger.info("Preparing worker layer shard with available RAM: %.1f MB", avail_ram)
            shard, shard_path = ensure_worker_shard(
                available_ram_mb=avail_ram,
                is_master=False,
                progress_callback=progress_callback,
            )
            self.assigned_shard = shard
            self.assigned_shard_path = shard_path
            msg = (
                f"Model layer shard ready: {shard.filename} "
                f"(layers {shard.layer_start}-{shard.layer_end})."
            )
            logger.info(msg)
            return True, msg
        except Exception as exc:
            msg = f"Model layer shard download failed: {exc}"
            logger.error(msg)
            return False, msg

    def prepare_and_start_rpc_worker(
        self,
        host: Optional[str] = None,
        port: int = 50052,
        threads: int = 4,
        progress_callback: Optional[Any] = None,
    ) -> tuple[bool, str]:
        """Discover, download, and verify the optimal layer shard, then start rpc-server."""
        if not host or host in ("0.0.0.0", "::"):
            host = self.detect_bind_ip()
        self._rpc_bind = host

        ok, shard_msg = self.prepare_worker_shard(progress_callback=progress_callback)
        if not ok:
            return False, shard_msg

        if self.start_rpc_worker(host=host, port=port, threads=threads):
            msg = f"{shard_msg} RPC listening on {self.rpc_endpoint}."
            logger.info(msg)
            return True, msg
        return False, (
            f"RPC server did not start: {self.rpc_last_error or 'unknown reason'}. "
            f"Log: {getattr(self.rpc_server, 'log_path', None) or 'unavailable'}."
        )

    def start_rpc_worker(
        self,
        host: str = "127.0.0.1",
        port: int = 50052,
        threads: int = 4,
        enable_cache: Optional[bool] = None,
    ) -> bool:
        """Start the native llama.cpp RPC server for remote layer execution.

        The disk cache (rpc-server -c) is off by default: it writes every model tensor to disk
        before the head can use it, which on an old laptop turns a network load into a disk load.
        Set RACON_RPC_CACHE=1 to trade first-load time for faster reloads on a fast SSD.
        """
        from racon.runtime.rpc_worker import RpcServerProcess
        if self.rpc_server and self.rpc_server.is_running():
            self.rpc_last_error = None
            return True
        self.rpc_server = RpcServerProcess(
            host=host, port=port, threads=threads, enable_cache=enable_cache
        )
        ok = self.rpc_server.start()
        if ok:
            self.rpc_endpoint = f"{host}:{port}"
            self.rpc_last_error = None
        else:
            self.rpc_endpoint = None
            self.rpc_last_error = self.rpc_server.last_error or "rpc-server did not start."
        return ok

    def stop_rpc_worker(self) -> None:
        """Stop the RPC worker process."""
        if self.rpc_server:
            self.rpc_server.stop()
            self.rpc_server = None
            self.rpc_endpoint = None

    def stop(self) -> None:
        """Gracefully stop the background worker thread and any running RPC servers."""
        self._stop_event.set()
        self.status.is_running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=3.0)
        self.stop_rpc_worker()

    def pause(self) -> None:
        self.status.is_paused = True

    def resume(self) -> None:
        self.status.is_paused = False

    def get_status(self) -> dict:
        shard_desc = None
        if self.assigned_shard:
            shard_desc = f"{self.assigned_shard.filename} (Layers {self.assigned_shard.layer_start}-{self.assigned_shard.layer_end})"

        return {
            "node_id": self.status.node_id,
            "public_key_hex": self.status.public_key_hex,
            "trust_state": self.status.trust_state.value,
            "coordinator_url": self.status.coordinator_url,
            "is_running": self.status.is_running,
            "is_paused": self.status.is_paused,
            "active_tasks_count": self.status.active_tasks_count,
            "completed_tasks_count": self.status.completed_tasks_count,
            "failed_tasks_count": self.status.failed_tasks_count,
            "last_heartbeat_time": self.status.last_heartbeat_time,
            "last_error": self.status.last_error,
            "rpc_endpoint": self.rpc_endpoint,
            "rpc_error": self.rpc_last_error,
            "rpc_log_path": getattr(self.rpc_server, "log_path", None),
            "assigned_shard": shard_desc,
            "assigned_shard_path": self.assigned_shard_path,
        }
