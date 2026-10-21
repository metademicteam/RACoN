"""Status and health endpoints for RACoN."""
from __future__ import annotations

import os
import platform
from datetime import datetime
from typing import Any

import httpx
import time
from fastapi import APIRouter

from ..config import VERSION, settings
from ..hardware import discover_hardware, get_lightweight_update
from ..scheduler.laya_adapter import get_laya_adapter
from ..storage.db import get_database

router = APIRouter(tags=["Status"])
_START_TIME = time.time()


async def check_ollama_status() -> dict[str, Any]:
    """Check availability of local Ollama runtime service."""
    ollama_url = settings.ollama_url.rstrip("/")
    result = {
        "installed": False,
        "running": False,
        "endpoint": ollama_url,
        "models_count": 0,
        "models": [],
        "version": None,
    }
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                result["installed"] = True
                result["running"] = True
                data = resp.json()
                models = [m.get("name") for m in data.get("models", [])]
                result["models_count"] = len(models)
                result["models"] = models
            v_resp = await client.get(f"{ollama_url}/api/version")
            if v_resp.status_code == 200:
                result["version"] = v_resp.json().get("version")
    except Exception:
        from shutil import which
        result["installed"] = which("ollama") is not None
        result["running"] = False

    return result


@router.get("/health")
def health_check():
    """Health check endpoint accessible without authentication."""
    return {
        "status": "ok",
        "service": "racon-backend",
        "version": VERSION,
        "timestamp": datetime.now().isoformat(),
        "platform": platform.platform(),
    }


@router.get("/api/v1/status")
async def get_system_status():
    """Detailed hardware telemetry, Laya advisor status, and Ollama runtime status."""
    snap = discover_hardware()
    light = get_lightweight_update()

    if light.get("cpu_utilization_pct") is not None:
        snap.cpu_utilization_pct = light["cpu_utilization_pct"]
    if light.get("ram_available_mb") is not None:
        snap.ram_available_mb = light["ram_available_mb"]
        snap.ram_used_mb = light["ram_used_mb"]
        snap.ram_utilization_pct = light["ram_utilization_pct"]
    if light.get("process_memory_mb") is not None:
        snap.process_memory_mb = light["process_memory_mb"]

    laya = get_laya_adapter()
    ollama = await check_ollama_status()
    db = get_database()
    local_node = db.get_node("node-local")

    return {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "version": VERSION,
        "laya_status": laya.status.value,
        "laya_version": laya.version,
        "ollama_status": "ready" if ollama.get("running") else "offline",
        "ollama_models_count": ollama.get("models_count", 0),
        "hardware": {
            "cpu_name": snap.cpu_name,
            "cpu_logical_count": snap.cpu_logical_count,
            "cpu_physical_count": snap.cpu_physical_count,
            "cpu_utilization_pct": round(snap.cpu_utilization_pct, 1),
            "cpu_freq_mhz": round(snap.cpu_freq_mhz, 1) if snap.cpu_freq_mhz else None,
            "ram_total_mb": round(snap.ram_total_mb, 1),
            "ram_available_mb": round(snap.ram_available_mb, 1),
            "ram_used_mb": round(snap.ram_used_mb, 1),
            "ram_utilization_pct": round(snap.ram_utilization_pct, 1),
            "process_memory_mb": round(snap.process_memory_mb, 1),
            "os_name": snap.os_name,
            "os_version": snap.os_version,
            "os_architecture": snap.os_architecture,
            "python_version": snap.python_version,
            "gpu": snap.gpu.to_dict(),
        },
        "cpu": {
            "name": snap.cpu_name,
            "logical_count": snap.cpu_logical_count,
            "physical_count": snap.cpu_physical_count,
            "utilization_pct": round(snap.cpu_utilization_pct, 1),
            "freq_mhz": round(snap.cpu_freq_mhz, 1) if snap.cpu_freq_mhz else None,
        },
        "ram": {
            "total_mb": round(snap.ram_total_mb, 1),
            "available_mb": round(snap.ram_available_mb, 1),
            "used_mb": round(snap.ram_used_mb, 1),
            "utilization_pct": round(snap.ram_utilization_pct, 1),
        },
        "gpu": snap.gpu.to_dict(),
        "process": {
            "pid": os.getpid(),
            "memory_mb": round(snap.process_memory_mb, 1),
        },
        "laya": {
            "status": laya.status.value,
            "version": laya.version,
            "is_ready": laya.is_ready,
            "error_message": laya.error_message,
        },
        "ollama": ollama,
        "local_node": {
            "id": "node-local",
            "status": local_node.get("status") if local_node else "available",
        },
    }
