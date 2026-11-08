"""Cluster supervisor: measures workers, plans layer placement, and replans when a worker drops.

Policy:
- A worker is live when its RPC port accepts a connection AND llama.cpp reports its memory.
- Membership changes (a worker joins or drops) trigger a replan. Layers from a dropped worker
  move to the head, and the head restarts with the new split. Restarts are the only way to change
  llama.cpp device membership, so a replan costs one restart.
- Plans are only recomputed on membership change, not on every probe, to avoid restart churn.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from racon.cluster.config import ClusterConfig, WorkerEndpoint
from racon.cluster.discovery import is_local_endpoint, local_ip_addresses
from racon.cluster.head import HeadServer
from racon.cluster.planner import HEAD, Plan, plan_layers
from racon.cluster.probe import ProbeResult, probe_devices
from racon.cluster.throughput import layers_that_fit, load_rates, record_load, slowest_rate
from racon.runtime.distributed_gguf import find_llama_tool

logger = logging.getLogger(__name__)

DEFAULT_PROBE_INTERVAL_S = 15.0
DEFAULT_FAILURES_BEFORE_DROP = 2


@dataclass
class WorkerState:
    endpoint: WorkerEndpoint
    consecutive_failures: int = 0
    reachable: bool = False
    latency_ms: Optional[float] = None


def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tcp_latency_ms(host: str, port: int, timeout: float = 2.0) -> Optional[float]:
    """TCP handshake time, used to keep high-latency workers out of the layer split."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.perf_counter() - start) * 1000.0
    except OSError:
        return None


def tailscale_rtt_ms(host: str, timeout: float = 5.0) -> Optional[float]:
    """Tailscale path RTT for 100.64/10 peers. Catches slow relays that a fast TCP accept hides."""
    try:
        if ipaddress.ip_address(host) not in ipaddress.ip_network("100.64.0.0/10"):
            return None
    except ValueError:
        return None
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "ping", "--c", "1", host],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"in\s+([\d.]+)\s*ms", proc.stdout + proc.stderr)
    return float(match.group(1)) if match else None


def worker_latency_ms(host: str, port: int) -> Optional[float]:
    rtt = tailscale_rtt_ms(host)
    return rtt if rtt is not None else tcp_latency_ms(host, port)


def bytes_per_layer_mb(model_path: str, total_layers: int) -> float:
    """Average on-disk size per transformer block. Embeddings and output head are amortized in.

    Each layer's share is the file size divided by the block count. This is a conservative
    estimate used for capacity, not an exact tensor-level measurement.
    """
    size_mb = Path(model_path).stat().st_size / (1024 * 1024)
    return size_mb / max(total_layers, 1)


class ClusterSupervisor:
    def __init__(
        self,
        cfg: ClusterConfig,
        head: HeadServer,
        probe: Callable[[str, List[str]], ProbeResult] = None,
        reachable: Callable[[str, int], bool] = tcp_reachable,
        latency: Callable[[str, int], Optional[float]] = worker_latency_ms,
        failures_before_drop: int = DEFAULT_FAILURES_BEFORE_DROP,
        rates_path: Optional[Path] = None,
    ) -> None:
        self.cfg = cfg
        self.head = head
        self._probe = probe or self._default_probe
        self._reachable = reachable
        self._latency = latency
        self._failures_before_drop = failures_before_drop
        self._rates_path = Path(rates_path) if rates_path else None
        self._slow_logged: set = set()
        self._disabled_workers: set = set()
        cfg.workers = self._remote_workers(cfg.workers)
        self._states: Dict[str, WorkerState] = {
            w.name: WorkerState(endpoint=w) for w in cfg.workers
        }
        self._lock = threading.Lock()
        self._last_members: Optional[frozenset] = None
        self._last_live: Optional[frozenset] = None

    @staticmethod
    def _remote_workers(endpoints: List[WorkerEndpoint]) -> List[WorkerEndpoint]:
        """Drop endpoints that are this PC. The head enrolls as a contributor and reports its own
        rpc-server; splitting to it doubles the model onto two local processes for no gain."""
        local = local_ip_addresses()
        kept: List[WorkerEndpoint] = []
        for w in endpoints:
            if is_local_endpoint(w.host, local):
                logger.warning(
                    "Ignoring worker %s (%s): that address belongs to this head PC. "
                    "Remove it from cluster.json; RACoN now filters it automatically.",
                    w.name, w.address,
                )
                continue
            kept.append(w)
        return kept

    def _default_probe(self, cli_bin: str, endpoints: List[str]) -> ProbeResult:
        return probe_devices(cli_bin, endpoints)

    def _cli_bin(self) -> str:
        found = find_llama_tool("llama-cli")
        if not found:
            raise RuntimeError("llama-cli not found; required for device probing")
        return found

    def _update_reachability(self) -> List[WorkerEndpoint]:
        """Mark workers up or down. A worker drops only after repeated failures, not one blip."""
        live: List[WorkerEndpoint] = []
        for state in self._states.values():
            ok = self._reachable(state.endpoint.host, state.endpoint.port)
            if ok:
                state.consecutive_failures = 0
                state.reachable = True
                if state.endpoint.name in self._disabled_workers:
                    continue
                rtt = self._latency(state.endpoint.host, state.endpoint.port)
                state.latency_ms = rtt
                if (
                    not self.cfg.allow_slow_workers
                    and rtt is not None
                    and rtt > self.cfg.max_worker_rtt_ms
                ):
                    if state.endpoint.name not in self._slow_logged:
                        logger.warning(
                            "Worker %s excluded from layer splitting: RPC latency %.0f ms "
                            "exceeds %.0f ms. Put both PCs on a fast LAN, or set "
                            "allow_slow_workers=true to force a split.",
                            state.endpoint.name, rtt, self.cfg.max_worker_rtt_ms,
                        )
                        self._slow_logged.add(state.endpoint.name)
                    continue
                self._slow_logged.discard(state.endpoint.name)
                live.append(state.endpoint)
            else:
                state.consecutive_failures += 1
                if state.consecutive_failures >= self._failures_before_drop:
                    state.reachable = False
                elif state.reachable:
                    live.append(state.endpoint)
        return live

    def _total_layers(self) -> int:
        if self.cfg.total_layers:
            return self.cfg.total_layers
        from racon.runtime.distributed_gguf import inspect_gguf_metadata
        blocks = int(inspect_gguf_metadata(self.cfg.model_path).get("block_count") or 0)
        if blocks <= 0:
            raise RuntimeError("Could not read block_count from GGUF metadata")
        return blocks

    def _probe_with_retries(self, endpoints: List[str], attempts: int = 3) -> ProbeResult:
        probe = None
        for attempt in range(attempts):
            probe = self._probe(self._cli_bin(), endpoints)
            if probe.local_free_mb is not None:
                break
            # The GPU driver can briefly report no devices right after another process exits.
            if attempt + 1 < attempts:
                logger.warning("Device probe returned no head memory; retrying in 2s.")
                time.sleep(2.0)
        if probe is None or probe.local_free_mb is None:
            raise RuntimeError("Could not read head device memory from llama-cli --list-devices")
        return probe

    def _head_holds_model(self, head_free_mb: float, per_layer_mb: float, total_layers: int) -> bool:
        """True when the head alone can hold the whole model inside the planner's memory budget."""
        from racon.cluster.planner import DEFAULT_MEMORY_FRACTION

        return per_layer_mb * total_layers <= head_free_mb * DEFAULT_MEMORY_FRACTION

    def build_plan(self, live: List[WorkerEndpoint], attempts: int = 3) -> Tuple[Plan, List[WorkerEndpoint]]:
        """Measure devices and assign layers. Returns the plan and the workers llama.cpp actually reached."""
        total_layers = self._total_layers()
        per_layer_mb = bytes_per_layer_mb(self.cfg.model_path, total_layers)
        policy = (self.cfg.worker_layer_policy or "auto").lower()

        # The head's own capacity is cheap to probe (no RPC). Use it first: when the head holds the
        # model there is nothing to gain from a split, and a full RPC device probe on a slow link
        # costs tens of seconds in blocked startup.
        if policy != "always":
            local = self._probe_with_retries([], attempts)
            if policy == "never" or self._head_holds_model(local.local_free_mb, per_layer_mb, total_layers):
                if policy == "never":
                    logger.info("Worker layer policy is 'never'; the head runs the whole model.")
                else:
                    logger.info(
                        "Head holds all %d layers (%.0f MiB free, model %.0f MiB); keeping workers "
                        "out of the split. Set worker_layer_policy=always to force it.",
                        total_layers, local.local_free_mb, per_layer_mb * total_layers,
                    )
                plan = plan_layers(
                    total_layers=total_layers,
                    head_free_mb=local.local_free_mb,
                    worker_free_mb={},
                    bytes_per_layer_mb=per_layer_mb,
                )
                return plan, []

        probe = self._probe_with_retries([w.address for w in live] if live else [], attempts)
        measured = [w for w in live if w.address in probe.rpc_free_mb]
        worker_free = {w.name: probe.rpc_free_mb[w.address] for w in measured}
        plan = plan_layers(
            total_layers=total_layers,
            head_free_mb=probe.local_free_mb,
            worker_free_mb=worker_free,
            bytes_per_layer_mb=per_layer_mb,
            worker_order=[w.name for w in measured],
            max_worker_layers=self._layer_cap(measured, per_layer_mb),
        )
        active = [w for w in measured if w.name in plan.device_order]
        return plan, active

    def _layer_cap(self, measured: List[WorkerEndpoint], per_layer_mb: float) -> int:
        """Layers per worker. An explicit max_worker_layers wins; otherwise the measured link rate
        decides how many layers fit inside the load window."""
        explicit = int(self.cfg.max_worker_layers or 0)
        if explicit > 0:
            return explicit
        rate = slowest_rate([w.address for w in measured], load_rates(self._rates_path))
        if rate is None:
            return 0
        cap = layers_that_fit(rate, per_layer_mb, self.cfg.rpc_startup_timeout_s)
        logger.info(
            "Measured RPC link to %s: %.2f MiB/s, so capping each worker to %d layer(s) to fit the "
            "%.0f s load window.",
            ", ".join(w.address for w in measured), rate, cap, self.cfg.rpc_startup_timeout_s,
        )
        return cap

    def update_workers(self, endpoints: List[WorkerEndpoint]) -> bool:
        """Replace the worker set (e.g. from registry discovery). Returns True if membership changed."""
        endpoints = self._remote_workers(endpoints)
        with self._lock:
            if {w.name for w in endpoints} == set(self._states):
                return False
            new_states: Dict[str, WorkerState] = {}
            for w in endpoints:
                old = self._states.get(w.name)
                new_states[w.name] = WorkerState(
                    endpoint=w,
                    consecutive_failures=old.consecutive_failures if old else 0,
                    reachable=old.reachable if old else False,
                )
            self._states = new_states
            self.cfg.workers = list(endpoints)
            logger.info("Worker set updated from discovery: %s", [w.address for w in endpoints])
            return True

    def reconcile(self) -> Optional[Plan]:
        """Restart the head only when the set of usable workers changes, or the head is down.

        Returns the new plan when a restart happened, or None when nothing changed.
        """
        with self._lock:
            live = self._update_reachability()
            live_names = frozenset(w.name for w in live)
            if live_names == self._last_live and self.head.is_running():
                return None

            try:
                plan, active = self.build_plan(live)
            except RuntimeError as exc:
                if "device memory" not in str(exc).lower():
                    raise
                # Never refuse to serve because a GPU probe failed. Run head-only: starting an
                # RPC split without a measured plan can offload an unsafe number of layers.
                logger.warning("Device probe failed (%s); starting the head without RPC workers.", exc)
                plan, active = None, []

            self._last_live = live_names
            active_names = frozenset(w.name for w in active)
            if active_names == self._last_members and self.head.is_running():
                # Live membership changed but the split did not (e.g. the auto policy keeps the
                # head alone). Restarting here would bounce the gateway for no reason.
                return None

            if plan is not None:
                if plan.dropped_workers:
                    logger.warning("Workers too small to hold layers: %s", list(plan.dropped_workers))
                logger.info(
                    "Replanning: %d active workers, head layers=%d, tensor_split=%s",
                    len(active), plan.layers_for(HEAD), plan.tensor_split,
                )
            else:
                logger.info("Starting head without a measured plan (%d configured workers).", len(active))
            startup_timeout = (
                max(30.0, self.cfg.rpc_startup_timeout_s) if active else 300.0
            )
            stall_timeout = max(5.0, self.cfg.rpc_stall_timeout_s)
            try:
                self.head.restart(
                    plan=plan,
                    active_workers=active,
                    startup_timeout=startup_timeout,
                    stall_timeout=stall_timeout,
                )
            except (RuntimeError, TimeoutError) as exc:
                if not active:
                    raise
                logger.error(
                    "Head failed to start with %d RPC worker(s): %s. Retrying head-only and "
                    "disabling those workers for this session.",
                    len(active), exc,
                )
                self._disabled_workers.update(w.name for w in active)
                self.head.restart(plan=None, active_workers=[], stall_timeout=stall_timeout)
                self._last_members = frozenset()
                return None
            if active and plan is not None:
                self._record_load(plan, active)
            self._last_members = frozenset(w.name for w in active)
            return plan

    def _record_load(self, plan: Plan, active: List[WorkerEndpoint]) -> None:
        """Log what the split actually cost and remember the link rate for future caps."""
        total_layers = plan.total_layers
        per_layer_mb = bytes_per_layer_mb(self.cfg.model_path, total_layers)
        worker_layers = sum(plan.layers_for(w.name) for w in active)
        load = dict(getattr(self.head, "last_load", {}) or {})
        transferred_mb = float(load.get("rpc_mb") or 0) or worker_layers * per_layer_mb
        seconds = float(load.get("seconds") or 0)
        if seconds <= 0:
            return
        rate = transferred_mb / seconds
        logger.info(
            "RPC split is live: %d layer(s) on %s, %.1f MiB transferred in %.1fs (~%.2f MiB/s, "
            "%s tensors).",
            worker_layers,
            ", ".join(w.address for w in active),
            transferred_mb,
            seconds,
            rate,
            load.get("tensors_transferred", "?"),
        )
        record_load([w.address for w in active], transferred_mb, seconds, path=self._rates_path)

    def status(self) -> Dict[str, object]:
        return {
            "workers": {
                name: {"reachable": s.reachable, "failures": s.consecutive_failures, "address": s.endpoint.address}
                for name, s in self._states.items()
            },
            "head": self.head.status(),
        }
