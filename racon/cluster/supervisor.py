"""Cluster supervisor: measures workers, plans layer placement, and replans when a worker drops.

Policy:
- A worker is live when its RPC port accepts a connection AND llama.cpp reports its memory.
- Membership changes (a worker joins or drops) trigger a replan. Layers from a dropped worker
  move to the head, and the head restarts with the new split. Restarts are the only way to change
  llama.cpp device membership, so a replan costs one restart.
- Plans are only recomputed on membership change, not on every probe, to avoid restart churn.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from racon.cluster.config import ClusterConfig, WorkerEndpoint
from racon.cluster.head import HeadServer
from racon.cluster.planner import HEAD, Plan, plan_layers
from racon.cluster.probe import ProbeResult, probe_devices
from racon.runtime.distributed_gguf import find_llama_tool

logger = logging.getLogger(__name__)

DEFAULT_PROBE_INTERVAL_S = 15.0
DEFAULT_FAILURES_BEFORE_DROP = 2


@dataclass
class WorkerState:
    endpoint: WorkerEndpoint
    consecutive_failures: int = 0
    reachable: bool = False


def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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
        failures_before_drop: int = DEFAULT_FAILURES_BEFORE_DROP,
    ) -> None:
        self.cfg = cfg
        self.head = head
        self._probe = probe or self._default_probe
        self._reachable = reachable
        self._failures_before_drop = failures_before_drop
        self._states: Dict[str, WorkerState] = {
            w.name: WorkerState(endpoint=w) for w in cfg.workers
        }
        self._lock = threading.Lock()
        self._last_members: Optional[frozenset] = None

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

    def build_plan(self, live: List[WorkerEndpoint], attempts: int = 3) -> Tuple[Plan, List[WorkerEndpoint]]:
        """Measure devices and assign layers. Returns the plan and the workers llama.cpp actually reached."""
        probe = None
        for attempt in range(attempts):
            probe = self._probe(self._cli_bin(), [w.address for w in live])
            if probe.local_free_mb is not None:
                break
            # The GPU driver can briefly report no devices right after another process exits.
            if attempt + 1 < attempts:
                logger.warning("Device probe returned no head memory; retrying in 2s.")
                time.sleep(2.0)
        if probe.local_free_mb is None:
            raise RuntimeError("Could not read head device memory from llama-cli --list-devices")

        measured = [w for w in live if w.address in probe.rpc_free_mb]
        worker_free = {w.name: probe.rpc_free_mb[w.address] for w in measured}
        total_layers = self._total_layers()
        plan = plan_layers(
            total_layers=total_layers,
            head_free_mb=probe.local_free_mb,
            worker_free_mb=worker_free,
            bytes_per_layer_mb=bytes_per_layer_mb(self.cfg.model_path, total_layers),
            worker_order=[w.name for w in measured],
        )
        active = [w for w in measured if w.name in plan.device_order]
        return plan, active

    def update_workers(self, endpoints: List[WorkerEndpoint]) -> bool:
        """Replace the worker set (e.g. from registry discovery). Returns True if membership changed."""
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
            members = frozenset(w.name for w in live)
            if members == self._last_members and self.head.is_running():
                return None

            try:
                plan, active = self.build_plan(live)
            except RuntimeError as exc:
                if "device memory" not in str(exc).lower():
                    raise
                # Never refuse to serve because a GPU probe failed: let llama.cpp pick its own split.
                logger.warning("Device probe failed (%s); starting the head without a measured split.", exc)
                plan, active = None, live

            if plan is not None:
                if plan.dropped_workers:
                    logger.warning("Workers too small to hold layers: %s", list(plan.dropped_workers))
                logger.info(
                    "Replanning: %d active workers, head layers=%d, tensor_split=%s",
                    len(active), plan.layers_for(HEAD), plan.tensor_split,
                )
            else:
                logger.info("Starting head without a measured plan (%d configured workers).", len(active))
            self.head.restart(plan=plan, active_workers=active)
            self._last_members = frozenset(w.name for w in active)
            return plan

    def status(self) -> Dict[str, object]:
        return {
            "workers": {
                name: {"reachable": s.reachable, "failures": s.consecutive_failures, "address": s.endpoint.address}
                for name, s in self._states.items()
            },
            "head": self.head.status(),
        }
