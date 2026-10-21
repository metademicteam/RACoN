"""Load and parse scheduling scenarios."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from ..scheduler.models import (
    WorkerCandidate,
    WorkerStatus,
    DeviceType,
)

logger = logging.getLogger(__name__)

_SCENARIOS_FILE = Path(__file__).parent / "sample_scenarios.json"

_STATUS_MAP = {
    "available": WorkerStatus.AVAILABLE,
    "busy": WorkerStatus.BUSY,
    "unavailable": WorkerStatus.UNAVAILABLE,
}

_DEVICE_MAP = {
    "cpu": DeviceType.CPU,
    "cuda": DeviceType.CUDA,
    "mps": DeviceType.MPS,
    "xpu": DeviceType.XPU,
    "onnx": DeviceType.ONNX,
}


def _parse_worker(data: dict, model_partition_mb: float) -> WorkerCandidate:
    """Parse a worker candidate from scenario JSON data."""
    status_raw = data.get("status", "available").lower()
    device_raw = data.get("device_type", "cpu").lower()

    return WorkerCandidate(
        worker_id=data["worker_id"],
        is_simulated=data.get("is_simulated", True),
        status=_STATUS_MAP.get(status_raw, WorkerStatus.AVAILABLE),
        device_type=_DEVICE_MAP.get(device_raw, DeviceType.CPU),
        ram_total_mb=float(data["ram_total_mb"]),
        ram_available_mb=float(data["ram_available_mb"]),
        accelerator_memory_mb=(
            float(data["accelerator_memory_mb"])
            if data.get("accelerator_memory_mb") is not None else None
        ),
        processing_speed_tps=(
            float(data["processing_speed_tps"])
            if data.get("processing_speed_tps") is not None else None
        ),
        network_latency_ms=(
            float(data["network_latency_ms"])
            if data.get("network_latency_ms") is not None else None
        ),
        current_workload_pct=float(data.get("current_workload_pct", 0)),
        model_partition_required_mb=model_partition_mb,
        compatible_runtimes=[
            _DEVICE_MAP[r.lower()] for r in data.get("compatible_runtimes", [])
            if r.lower() in _DEVICE_MAP
        ],
    )


def load_scenarios(filepath: Optional[Path] = None) -> list[dict]:
    """
    Load all predefined scenarios from the JSON file.

    Returns:
        list of dicts containing name, description, model_partition_required_mb, workers.
    """
    path = filepath or _SCENARIOS_FILE
    if not path.exists():
        # Fallback to root racon scenarios if needed
        fallback = Path(__file__).resolve().parents[3] / "racon" / "scenarios" / "sample_scenarios.json"
        if fallback.exists():
            path = fallback

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.error("Failed to load scenarios from %s: %s", path, e)
        return []

    scenarios = []
    for entry in raw:
        try:
            model_mb = float(entry.get("model_partition_required_mb", 2048))
            workers = [_parse_worker(w, model_mb) for w in entry.get("workers", [])]
            scenarios.append({
                "name": entry["name"],
                "description": entry.get("description", ""),
                "model_partition_required_mb": model_mb,
                "workers": workers,
            })
        except Exception as e:
            logger.error("Failed to parse scenario '%s': %s", entry.get("name", "?"), e)

    logger.info("Loaded %d scenarios from %s", len(scenarios), path)
    return scenarios


def get_scenario_names(filepath: Optional[Path] = None) -> list[str]:
    """Return list of scenario names."""
    return [s["name"] for s in load_scenarios(filepath)]


def get_scenario(name: str, filepath: Optional[Path] = None) -> Optional[dict]:
    """Return a specific scenario by name."""
    for s in load_scenarios(filepath):
        if s["name"] == name:
            return s
    return None
