"""System logs and audit event inspection routes."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from ..security import redact_sensitive_info
from ..storage.db import get_database
from .log_buffer import get_log_buffer_handler

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/logs", tags=["Logs"])


@router.get("")
def get_system_logs(
    limit: int = Query(100, ge=1, le=1000),
    level: Optional[str] = Query(None, description="Filter by level: DEBUG, INFO, WARN, WARNING, ERROR"),
):
    """
    Retrieve recent system logs and database audit records with automatic token redaction.
    """
    handler = get_log_buffer_handler()
    in_memory_logs = handler.get_logs(limit=limit, level=level)

    db = get_database()
    audit_logs = db.get_audit_logs(limit=limit, level=level)

    combined = []
    for entry in in_memory_logs:
        combined.append(entry)

    for entry in audit_logs:
        combined.append({
            "id": entry["id"],
            "timestamp": entry["timestamp"],
            "event_type": entry["event_type"],
            "level": entry["level"],
            "logger": "racon.audit",
            "message": redact_sensitive_info(entry["message"]),
            "details": redact_sensitive_info(entry.get("details", "") or ""),
        })

    # Ensure at least one entry exists if system was just booted
    if not combined:
        combined.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "logger": "racon.system",
            "message": "RACoN system service online and ready",
        })

    return combined[:limit]
