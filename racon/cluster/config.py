"""Cluster topology and gateway configuration loaded from JSON."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from racon.config import get_data_dir

DEFAULT_RPC_PORT = 50052
_UNROUTABLE_HOSTS = {"0.0.0.0", "::", ""}


@dataclass(frozen=True)
class WorkerEndpoint:
    name: str
    host: str
    port: int

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class ClusterConfig:
    model_path: str
    model_alias: str = "gemma-3-1b"
    workers: List[WorkerEndpoint] = field(default_factory=list)
    head_host: str = "127.0.0.1"
    head_port: int = 8080
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8000
    n_gpu_layers: int = 26
    context_size: int = 8192
    threads: int = 4
    max_tokens_limit: int = 2048
    kv_cache_type: str = "q8_0"  # q8_0, q4_0, or f16 for reduced attention cache memory
    flash_attn: bool = False  # Enable flash attention when supported by backend
    total_layers: Optional[int] = None  # read from GGUF metadata when unset
    max_worker_rtt_ms: float = 50.0  # exclude workers slower than this from layer splitting
    allow_slow_workers: bool = False  # force layer splitting over high-latency links
    rpc_startup_timeout_s: float = 300.0  # hard cap for an RPC head load before head-only fallback
    rpc_stall_timeout_s: float = 45.0  # abort an RPC load when the head log stops making progress
    max_worker_layers: int = 0  # cap layers per worker so the first load fits the timeout (0 = no cap)
    worker_layer_policy: str = "auto"  # auto: split only if the head cannot hold the model; always; never


def default_cluster_dir() -> Path:
    path = get_data_dir() / "cluster"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config_path() -> Path:
    return default_cluster_dir() / "cluster.json"


def default_keys_path() -> Path:
    return default_cluster_dir() / "api_keys.json"


def _parse_worker(entry: Dict[str, Any], index: int) -> WorkerEndpoint:
    name = str(entry.get("name") or f"worker-{index + 1}")
    host = str(entry.get("host", "")).strip()
    port = int(entry.get("port", DEFAULT_RPC_PORT))
    if host in _UNROUTABLE_HOSTS:
        raise ValueError(f"Worker '{name}' must have a concrete Tailscale or LAN host, not '{host}'")
    if not 1 <= port <= 65535:
        raise ValueError(f"Worker '{name}' has invalid port {port}")
    return WorkerEndpoint(name=name, host=host, port=port)


def load_config(path: Optional[str | Path] = None) -> ClusterConfig:
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Cluster config not found: {config_path}")

    # utf-8-sig tolerates the BOM that Notepad and PowerShell add to hand-edited configs.
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not data.get("model_path"):
        raise ValueError("Cluster config requires 'model_path'")

    fields = {k: v for k, v in data.items() if k != "workers"}
    workers = [_parse_worker(w, i) for i, w in enumerate(data.get("workers", []))]

    policy = str(fields.get("worker_layer_policy", "auto")).lower()
    if policy not in ("auto", "always", "never"):
        raise ValueError(
            "worker_layer_policy must be 'auto', 'always' or 'never' "
            f"(got '{fields.get('worker_layer_policy')}')"
        )
    fields["worker_layer_policy"] = policy
    if int(fields.get("max_worker_layers", 0)) < 0:
        raise ValueError("max_worker_layers must be 0 (no cap) or a positive layer count")
    return ClusterConfig(workers=workers, **fields)
