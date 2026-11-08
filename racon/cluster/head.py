"""Head node: runs llama-server on loopback, pipelining layers across RPC workers."""
from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import time
from typing import Any, Dict, List, Optional

import httpx

from racon.cluster.config import ClusterConfig, WorkerEndpoint
from racon.cluster.planner import HEAD, Plan
from racon.runtime.distributed_gguf import find_llama_tool, llama_install_hint, llama_process_env

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
    # Offload exactly the layers the plan gave to RPC workers. The head's CPU keeps the rest;
    # the head GPU is deliberately excluded from the device list so a Vulkan/CUDA + RPC split
    # cannot stall at model load. Head-only mode keeps the configured GPU offload.
    n_gpu_layers = cfg.n_gpu_layers
    worker_split: List[float] = []
    rpc_workers: List[WorkerEndpoint] = []
    if workers and plan is not None:
        worker_layers = sum(a.count for a in plan.assignments if a.device != HEAD)
        if worker_layers > 0:
            n_gpu_layers = worker_layers
            # --rpc order defines RPC0, RPC1, ... so it must match the plan, not the caller's list.
            by_name = {w.name: w for w in workers}
            rpc_workers = [by_name[name] for name in plan.device_order if name in by_name]
            shares = [plan.layers_for(w.name) for w in rpc_workers]
            total = sum(shares)
            worker_split = [round(share / total, 4) for share in shares] if total else []
    cmd = [
        server_bin,
        "-m", cfg.model_path,
        "--alias", cfg.model_alias,
        "--host", cfg.head_host,
        "--port", str(cfg.head_port),
        "-ngl", str(n_gpu_layers),
        "-c", str(cfg.context_size),
        "-t", str(cfg.threads),
        "--no-webui",
        "--jinja",
        "--api-key", internal_key,
    ]
    if getattr(cfg, "kv_cache_type", None) and cfg.kv_cache_type in ("q8_0", "q4_0", "q4_1", "f16"):
        cmd.extend(["-ctk", cfg.kv_cache_type, "-ctv", cfg.kv_cache_type])
    if getattr(cfg, "flash_attn", False):
        cmd.append("-fa")
    if rpc_workers:
        cmd.extend(["--rpc", ",".join(w.address for w in rpc_workers), "-sm", "layer"])
        cmd.extend(["-dev", ",".join(f"RPC{i}" for i in range(len(rpc_workers)))])
        if worker_split:
            cmd.extend(["-ts", ",".join(str(x) for x in worker_split)])
    return cmd


class HeadServer:
    """Owns the llama-server child process. Its API key is internal to the gateway."""

    def __init__(self, cfg: ClusterConfig, log_path: Optional[str] = None) -> None:
        self.cfg = cfg
        self.log_path = log_path
        self.internal_key = secrets.token_urlsafe(32)
        self.plan: Optional[Plan] = None
        self.active_workers: List[WorkerEndpoint] = list(cfg.workers)
        self.last_load: Dict[str, Any] = {}
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
        stall_timeout: float = 0.0,
    ) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        server_bin = find_llama_tool("llama-server")
        if not server_bin:
            raise RuntimeError(f"llama-server executable not found. {llama_install_hint().capitalize()}.")
        if not os.path.exists(self.cfg.model_path):
            raise FileNotFoundError(f"Model not found: {self.cfg.model_path}")

        self.plan = plan
        if active_workers is not None:
            self.active_workers = list(active_workers)
        cmd = build_head_command(self.cfg, server_bin, self.internal_key, plan=self.plan, active_workers=self.active_workers)
        logger.info("Starting llama-server head: %s", " ".join(c if c != self.internal_key else "***" for c in cmd))
        if not self.log_path:
            from racon.config import get_log_dir
            self.log_path = str(get_log_dir() / "llama-server.log")
        out: Any = subprocess.DEVNULL
        try:
            self._log_handle = open(self.log_path, "w", encoding="utf-8")
            out = self._log_handle
        except OSError as exc:
            logger.warning("Could not open llama-server log %s: %s", self.log_path, exc)
            self.log_path = None
        started = time.monotonic()
        self._process = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=llama_process_env()
        )
        self._wait_ready(startup_timeout, stall_timeout=stall_timeout)
        self.last_load = self._load_summary(started)

    def _log_size(self) -> int:
        if not self.log_path:
            return 0
        try:
            return os.path.getsize(self.log_path)
        except OSError:
            return 0

    def _log_text(self) -> str:
        if not self.log_path:
            return ""
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def _load_summary(self, started: float) -> Dict[str, Any]:
        """What the log says about the load that just finished, for logs and link measurements."""
        text = self._log_text()
        rpc_mb = None
        match = re.search(r"RPC\d+\[[^\]]*\] model buffer size =\s*([\d.]+) MiB", text)
        if match:
            rpc_mb = float(match.group(1))
        offloaded = re.search(r"offloaded (\d+)/(\d+) layers to GPU", text)
        dots = sum(len(line) for line in re.findall(r"^\.+$", text, re.MULTILINE))
        summary: Dict[str, Any] = {
            "seconds": round(time.monotonic() - started, 1),
            "used_rpc": "using device RPC" in text or rpc_mb is not None,
            "rpc_mb": rpc_mb,
            "tensors_transferred": dots,
        }
        if offloaded:
            summary["offloaded_layers"] = int(offloaded.group(1))
            summary["total_layers"] = int(offloaded.group(2))
        return summary

    def _wait_ready(self, timeout: float, stall_timeout: float = 0.0) -> None:
        """Wait for /health, aborting early when llama-server stops writing to its log.

        An RPC tensor transfer writes one progress dot per tensor. A fixed timeout either kills a
        slow-but-working transfer or lets a dead one hold the gateway at 503. Watching the log
        tells the two apart: no growth for stall_timeout means the load is stuck.
        """
        started = time.monotonic()
        deadline = started + timeout
        last_size = self._log_size()
        last_change = started
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {self._process.returncode}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2.0).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            size = self._log_size()
            now = time.monotonic()
            if size != last_size:
                last_size = size
                last_change = now
            elif stall_timeout > 0 and now - last_change > stall_timeout:
                self.stop()
                raise TimeoutError(
                    f"llama-server stalled for {stall_timeout:.0f}s while loading "
                    f"(log: {self.log_path}). The RPC link is too slow to transfer this split."
                )
            time.sleep(1.0)
        self.stop()
        raise TimeoutError(
            f"llama-server did not become healthy within {timeout}s (log: {self.log_path})"
        )

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
        stall_timeout: float = 0.0,
    ) -> None:
        """Replace the running llama-server with one using a new plan and worker set."""
        self.stop()
        self.start(
            startup_timeout=startup_timeout,
            plan=plan,
            active_workers=active_workers,
            stall_timeout=stall_timeout,
        )

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running(),
            "head": self.base_url,
            "configured_workers": [w.address for w in self.cfg.workers],
            "active_workers": [w.address for w in self.active_workers],
            "layers": {a.device: [a.layer_start, a.layer_end] for a in self.plan.assignments} if self.plan else {},
            "last_load": self.last_load,
        }
