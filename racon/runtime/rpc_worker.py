"""RACoN Distributed RPC Worker and Pipeline Engine.

Manages native llama.cpp rpc-server processes on contributor PCs and
orchestrates distributed layer offloading over high-speed LAN / WireGuard (ADR-012).
"""
from __future__ import annotations

import logging
import os
import re
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from racon.runtime.distributed_gguf import find_llama_tool, llama_process_env

logger = logging.getLogger(__name__)


def _env_cache_default() -> bool:
    return os.environ.get("RACON_RPC_CACHE", "").strip().lower() in ("1", "true", "yes", "on")


def describe_rpc_exit(code: int) -> str:
    """Explain an rpc-server exit code, including POSIX signals such as SIGILL."""
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        hint = ""
        if name == "SIGILL":
            hint = (
                " The CPU does not support an instruction used by this llama.cpp build; "
                "install RACoN v1.0.4 or newer, which includes portable CPU backends."
            )
        elif name == "SIGSEGV":
            hint = " The process crashed with a segmentation fault."
        elif name == "SIGABRT":
            hint = " The process aborted."
        return f"rpc-server was killed by {name} (exit {code}).{hint}"
    if code == 0xC000001D:
        return (
            "rpc-server was killed by an illegal instruction (0xC000001D). "
            "The CPU does not support this llama.cpp build."
        )
    return f"rpc-server exited with code {code}."


@dataclass
class DistributedInferenceResult:
    response: str
    prompt_tokens_per_second: float
    generation_tokens_per_second: float
    total_duration_seconds: float
    memory_breakdown: Dict[str, Any]
    rpc_endpoints: List[str]
    layers_offloaded: int
    exit_code: int
    error: Optional[str] = None
    mode: str = "OBSERVED"


class RpcServerProcess:
    """Manages the background rpc-server process on a contributor node."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 50052,
        threads: int = 4,
        enable_cache: Optional[bool] = None,
        log_file: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.threads = threads
        # llama.cpp's -c local cache writes every received tensor to disk before the head can use
        # it. On an old laptop or HDD that turns a network load into a disk load and can push the
        # transfer past the head's startup window. Memory-only is the default; RACON_RPC_CACHE=1
        # (or --cache) re-enables it for fast disks where re-loading the same split is common.
        self.enable_cache = _env_cache_default() if enable_cache is None else enable_cache
        self.log_file = log_file
        self._process: Optional[subprocess.Popen] = None
        self._bin_path: Optional[str] = None
        self._log_handle: Optional[Any] = None
        self.last_error: Optional[str] = None
        self.log_path: Optional[str] = log_file

    def build_command(self) -> List[str]:
        """rpc-server argv. Shared with tests so the cache flag cannot drift."""
        cmd = [
            self._bin_path or "rpc-server",
            "-H", self.host,
            "-p", str(self.port),
            "-t", str(self.threads),
        ]
        if self.enable_cache:
            cmd.append("-c")
        return cmd

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    def _log_tail(self, lines: int = 12) -> str:
        """Return the last lines written by rpc-server, for actionable error messages."""
        if not self.log_path:
            return ""
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                content = [line.rstrip() for line in fh if line.strip()]
            return " | ".join(content[-lines:])
        except OSError:
            return ""

    def _fail(self, message: str) -> bool:
        self.last_error = message
        logger.error(message)
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception:
                pass
        self._close_log()
        self._process = None
        return False

    def start(self) -> bool:
        """Start rpc-server and confirm it is actually listening on the requested address."""
        self.last_error = None
        if self.is_running():
            logger.info("rpc-server already running on %s:%d", self.host, self.port)
            return True

        self._bin_path = find_llama_tool("rpc-server")
        if not self._bin_path or not os.path.exists(self._bin_path):
            return self._fail(
                "rpc-server executable not found. Reinstall RACoN or set RACON_LLAMA_DIR to a "
                "folder that contains rpc-server."
            )

        cmd = self.build_command()

        logger.info(
            "Starting rpc-server: %s (local cache: %s)",
            " ".join(cmd), "on" if self.enable_cache else "off",
        )
        try:
            if not self.log_path:
                from racon.config import get_log_dir
                self.log_path = str(get_log_dir() / "rpc-server.log")
            try:
                self._log_handle = open(self.log_path, "w", encoding="utf-8")
                out_target = self._log_handle
            except OSError:
                self.log_path = None
                out_target = subprocess.DEVNULL

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=out_target,
                stderr=out_target,
                env=llama_process_env(),
            )

            # Wait for the socket, not just for the process to survive. A server that cannot bind
            # or load a shared library otherwise looks healthy for the first second.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    code = self._process.returncode
                    tail = self._log_tail()
                    detail = f" Last output: {tail}" if tail else ""
                    return self._fail(f"{describe_rpc_exit(code)}{detail}")
                if self._check_socket_open(self.host, self.port):
                    logger.info(
                        "rpc-server successfully running (PID %d) on %s:%d",
                        self._process.pid, self.host, self.port,
                    )
                    return True
                time.sleep(0.25)

            tail = self._log_tail()
            detail = f" Last output: {tail}" if tail else ""
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception:
                pass
            return self._fail(
                f"rpc-server did not listen on {self.host}:{self.port} within 5 seconds.{detail}"
            )
        except Exception as e:
            return self._fail(f"Failed to start rpc-server: {e}")

    def is_running(self) -> bool:
        """Check if process is active and port is responding."""
        if self._process is None or self._process.poll() is not None:
            return False
        return self._check_socket_open(self.host, self.port)

    @staticmethod
    def _check_socket_open(host: str, port: int, timeout: float = 0.5) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def stop(self, timeout: float = 3.0) -> None:
        """Gracefully stop the rpc-server process."""
        if self._process is not None:
            logger.info("Stopping rpc-server (PID %d)...", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("rpc-server did not terminate; killing process.")
                self._process.kill()
            self._process = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None


class DistributedPipelineEngine:
    """Master/Coordinator engine that invokes distributed layer inference across RPC workers."""

    def __init__(self, cli_bin: Optional[str] = None) -> None:
        self.cli_bin = cli_bin or find_llama_tool("llama-cli")

    def execute(
        self,
        model_path: str,
        rpc_endpoints: List[str],
        prompt: str,
        max_tokens: int = 32,
        n_gpu_layers: int = 26,
        split_mode: str = "layer",
        tensor_split: Optional[List[float]] = None,
        threads: int = 4,
        timeout_seconds: float = 120.0,
        cpu_only: bool = False,
    ) -> DistributedInferenceResult:
        """Execute distributed inference across specified RPC endpoints.

        cpu_only runs llama-cli without the Vulkan GPU backend, for hosts whose GPU runs out of memory.
        """
        if not self.cli_bin or not os.path.exists(self.cli_bin):
            return DistributedInferenceResult(
                response="",
                prompt_tokens_per_second=0.0,
                generation_tokens_per_second=0.0,
                total_duration_seconds=0.0,
                memory_breakdown={},
                rpc_endpoints=rpc_endpoints,
                layers_offloaded=n_gpu_layers,
                exit_code=-1,
                error=f"llama-cli executable not found at '{self.cli_bin}'",
                mode="FAILED",
            )

        if not os.path.exists(model_path):
            return DistributedInferenceResult(
                response="",
                prompt_tokens_per_second=0.0,
                generation_tokens_per_second=0.0,
                total_duration_seconds=0.0,
                memory_breakdown={},
                rpc_endpoints=rpc_endpoints,
                layers_offloaded=n_gpu_layers,
                exit_code=-1,
                error=f"Model file not found: {model_path}",
                mode="FAILED",
            )

        cmd = [
            self.cli_bin,
            "-m", model_path,
            "-ngl", "0" if cpu_only else str(n_gpu_layers),
            "-p", prompt,
            "-n", str(max_tokens),
            "--no-warmup",
            "--single-turn",
            "-t", str(threads),
        ]
        if cpu_only:
            cmd.extend(["--device", "none"])

        if rpc_endpoints and not cpu_only:
            rpc_arg = ",".join(rpc_endpoints)
            cmd.extend(["--rpc", rpc_arg, "-sm", split_mode])

        if tensor_split:
            ts_str = ",".join(str(x) for x in tensor_split)
            cmd.extend(["-ts", ts_str])

        logger.info("Executing distributed pipeline: %s", " ".join(cmd))
        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                env=llama_process_env(),
            )
            duration = time.time() - start_time

            stdout = proc.stdout
            stderr = proc.stderr

            # Parse speeds: "[ Prompt: 0.8 t/s | Generation: 1.1 t/s ]"
            prompt_tps = 0.0
            gen_tps = 0.0
            speed_match = re.search(r"Prompt:\s*([\d\.]+)\s*t/s\s*\|\s*Generation:\s*([\d\.]+)\s*t/s", stdout)
            if speed_match:
                prompt_tps = float(speed_match.group(1))
                gen_tps = float(speed_match.group(2))

            # Parse memory breakdown from stdout/stderr:
            # llama_memory_breakdown_print: |   - RPC0 (127.0.0.1:50052) |  8125 = 5700 + (1407 =   743 +     149 +     514)
            memory_breakdown: Dict[str, Any] = {}
            for line in (stdout + "\n" + stderr).splitlines():
                if "RPC" in line or "Host" in line or "Vulkan" in line or "CUDA" in line:
                    if "llama_memory_breakdown_print" in line:
                        parts = line.split("|")
                        if len(parts) >= 3:
                            dev_name = parts[1].strip().lstrip("- ")
                            breakdown = parts[2].strip()
                            memory_breakdown[dev_name] = breakdown

            # Clean output response
            # Remove header banner and memory print
            clean_resp = stdout
            # Extract content after prompt
            if prompt in clean_resp:
                clean_resp = clean_resp.split(prompt, 1)[-1]
            # Strip memory print and trailing instructions
            if "llama_memory_breakdown_print" in clean_resp:
                clean_resp = clean_resp.split("llama_memory_breakdown_print", 1)[0]
            if "[ Prompt:" in clean_resp:
                clean_resp = clean_resp.split("[ Prompt:", 1)[0]
            clean_resp = clean_resp.strip()

            error_msg = None if proc.returncode == 0 else f"CLI exited with code {proc.returncode}"

            return DistributedInferenceResult(
                response=clean_resp,
                prompt_tokens_per_second=prompt_tps,
                generation_tokens_per_second=gen_tps,
                total_duration_seconds=round(duration, 2),
                memory_breakdown=memory_breakdown,
                rpc_endpoints=rpc_endpoints,
                layers_offloaded=n_gpu_layers,
                exit_code=proc.returncode,
                error=error_msg,
                mode="OBSERVED",
            )
        except subprocess.TimeoutExpired:
            return DistributedInferenceResult(
                response="",
                prompt_tokens_per_second=0.0,
                generation_tokens_per_second=0.0,
                total_duration_seconds=timeout_seconds,
                memory_breakdown={},
                rpc_endpoints=rpc_endpoints,
                layers_offloaded=n_gpu_layers,
                exit_code=-1,
                error=f"Distributed inference timed out after {timeout_seconds}s",
                mode="FAILED",
            )
        except Exception as e:
            return DistributedInferenceResult(
                response="",
                prompt_tokens_per_second=0.0,
                generation_tokens_per_second=0.0,
                total_duration_seconds=0.0,
                memory_breakdown={},
                rpc_endpoints=rpc_endpoints,
                layers_offloaded=n_gpu_layers,
                exit_code=-1,
                error=str(e),
                mode="FAILED",
            )
