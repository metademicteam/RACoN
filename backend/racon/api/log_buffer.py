"""In-memory ring buffer logging handler with token redaction for REST API logs."""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Any, Optional

from backend.racon.security import redact_sensitive_info


class RingBufferLogHandler(logging.Handler):
    """Logging handler that retains the latest N log records in memory."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self.records: deque[dict[str, Any]] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record) if self.formatter else record.getMessage()
            # Redact secrets and tokens
            safe_msg = redact_sensitive_info(msg)
            entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": safe_msg,
            }
            self.records.append(entry)
        except Exception:
            self.handleError(record)

    def get_logs(self, limit: int = 100, level: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve recent log records, optionally filtered by level."""
        items = list(self.records)
        items.reverse()
        if level:
            target_level = level.upper()
            items = [item for item in items if item["level"] == target_level]
        return items[:limit]

    def clear(self) -> None:
        """Clear memory buffer."""
        self.records.clear()


# Global log handler instance
_LOG_HANDLER: Optional[RingBufferLogHandler] = None


def get_log_buffer_handler() -> RingBufferLogHandler:
    """Retrieve or attach the global ring buffer log handler."""
    global _LOG_HANDLER
    if _LOG_HANDLER is None:
        _LOG_HANDLER = RingBufferLogHandler(capacity=1000)
        _LOG_HANDLER.setLevel(logging.INFO)
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s")
        _LOG_HANDLER.setFormatter(formatter)
        logging.getLogger().addHandler(_LOG_HANDLER)
    return _LOG_HANDLER
