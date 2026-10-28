"""Head node: runs llama-server on loopback, pipelining layers across RPC workers."""
from __future__ import annotations

import logging
import os
import secrets
import subprocess
import time
from typing import Any, Dict, List, Optional

import httpx

from racon.cluster.config import ClusterConfig, WorkerEndpoint
from racon.cluster.planner import Plan
from racon.runtime.distributed_gguf import find_llama_tool

logger = logging.getLogger(__name__)


def build_head_command(
    cfg: ClusterConfig,
    server_bin: str,
    internal_key: str,
    plan: Optional[Plan] = None,
    active_workers: Optional[List[WorkerEndpoint]] = None,
) -> List[str]:
    """Build the llama-server command. When a plan is given, the RPC list and -ts ratios come from it."""
    workers = cfg.workers if active_workers is None else active_workers
    cmd = [
        server_bin,
        "-m", cfg.model_path,
        "--alias", cfg.model_alias,
        "--host", cfg.head_host,
        "--port", str(cfg.head_port),
        "-ngl", str(cfg.n_gpu_layers),
        "-c", str(cfg.context_size),
        "-t", str(cfg.threads),
        "--no-webui",
        "--jinja",
        "--api-key", internal_key,
    ]
    if workers:
        cmd.extend(["--rpc", ",".join(w.address for w in workers), "-sm", "layer"])
        if plan is not None and plan.tensor_split:
            cmd.extend(["-ts", ",".join(str(x) for x in plan.tensor_split)])
    return cmd


class HeadServer:
    """Owns the llama-server child process. Its API key is internal to the gateway."""

    def __init__(self, cfg: ClusterConfig, log_path: Optional[str] = None) -> None:
        self.cfg = cfg
        self.log_path = log_path
        self.internal_key = secrets.token_urlsafe(32)
        self.plan: Optional[Plan] = None
        self.active_workers: List[WorkerEndpoint] = list(cfg.workers)
        self._process: Optional[subprocess.Popen] = None
        self._log_handle: Any = None

    @property
    def base_url(self) -> str:
        return f"http://{self.cfg.head_host}:{self.cfg.head_port}"

    def start(
        self,
        startup_timeout: float = 300.0,
        plan: Optional[Plan] = None,
        active_workers: Optional[List[WorkerEndpoint]] = None,
    ) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        server_bin = find_llama_tool("llama-server")
        if not server_bin:
            raise RuntimeError("llama-server executable not found. Install llama.cpp (winget install ggml.llamacpp).")
        if not os.path.exists(self.cfg.model_path):
            raise FileNotFoundError(f"Model not found: {self.cfg.model_path}")

        self.plan = plan
        if active_workers is not None:
            self.active_workers = list(active_workers)
        cmd = build_head_command(self.cfg, server_bin, self.internal_key, plan=self.plan, active_workers=self.active_workers)
        logger.info("Starting llama-server head: %s", " ".join(c if c != self.internal_key else "***" for c in cmd))
        out: Any = subprocess.DEVNULL
        if self.log_path:
            self._log_handle = open(self.log_path, "w", encoding="utf-8")
            out = self._log_handle
        self._process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out)
        self._wait_ready(startup_timeout)

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {self._process.returncode}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2.0).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(1.0)
        self.stop()
        raise TimeoutError(f"llama-server did not become healthy within {timeout}s")

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, timeout: float = 5.0) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def restart(
        self,
        plan: Optional[Plan],
        active_workers: List[WorkerEndpoint],
        startup_timeout: float = 300.0,
    ) -> None:
        """Replace the running llama-server with one using a new plan and worker set."""
        self.stop()
        self.start(startup_timeout=startup_timeout, plan=plan, active_workers=active_workers)

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running(),
            "head": self.base_url,
            "configured_workers": [w.address for w in self.cfg.workers],
            "active_workers": [w.address for w in self.active_workers],
            "layers": {a.device: [a.layer_start, a.layer_end] for a in self.plan.assignments} if self.plan else {},
        }
