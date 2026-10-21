"""System settings management and persistence routes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from ..config import settings
from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/settings", tags=["Settings"])

DEFAULT_SETTINGS: dict[str, str] = {
    "host": "127.0.0.1",
    "port": str(settings.port),
    "ollama_url": settings.ollama_url,
    "telemetry_interval_sec": "3",
    "max_worker_ram_pct": "80",
    "laya_enabled": "true",
    "auto_fallback": "true",
    "log_level": "INFO",
}


@router.get("")
def get_system_settings():
    """Retrieve all active persistent configuration parameters."""
    db = get_database()
    stored = db.get_all_settings()
    combined = dict(DEFAULT_SETTINGS)
    combined.update(stored)
    return combined


@router.put("")
async def update_system_settings(request: Request):
    """
    Update and persist application configuration settings.
    Accepts {"key": "...", "value": "..."}, {"settings": {...}}, or arbitrary key-value mapping.
    """
    db = get_database()
    try:
        data = await request.json()
    except Exception:
        data = {}

    if isinstance(data, dict):
        if "key" in data and "value" in data:
            db.set_setting(data["key"], str(data["value"]))
            if data["key"] == "ollama_url":
                settings.ollama_url = str(data["value"])
        elif "settings" in data and isinstance(data["settings"], dict):
            for k, v in data["settings"].items():
                db.set_setting(k, str(v))
                if k == "ollama_url":
                    settings.ollama_url = str(v)
        else:
            for k, v in data.items():
                db.set_setting(k, str(v))
                if k == "ollama_url":
                    settings.ollama_url = str(v)

    db.log_audit("SETTINGS_UPDATE", f"Updated settings: {list(data.keys()) if isinstance(data, dict) else []}")
    stored = db.get_all_settings()
    combined = dict(DEFAULT_SETTINGS)
    combined.update(stored)
    return combined
