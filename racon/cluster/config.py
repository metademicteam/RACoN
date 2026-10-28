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
    total_layers: Optional[int] = None  # read from GGUF metadata when unset


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

    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not data.get("model_path"):
        raise ValueError("Cluster config requires 'model_path'")

    fields = {k: v for k, v in data.items() if k != "workers"}
    workers = [_parse_worker(w, i) for i, w in enumerate(data.get("workers", []))]
    return ClusterConfig(workers=workers, **fields)
