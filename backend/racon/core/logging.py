"""Structured JSON logging with sensitive token and credential redaction."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from backend.racon.core.config import get_log_path
from backend.racon.security.token import redact_sensitive


class JSONFormatter(logging.Formatter):
    """Structured JSON logging formatter with automatic secret redaction."""

    _RESERVED_ATTRS = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Format base message and apply redaction
        try:
            raw_message = record.getMessage()
        except Exception:
            raw_message = str(record.msg)

        redacted_message = redact_sensitive(raw_message)

        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redacted_message,
            "module": record.module,
            "func_name": record.funcName,
            "line_no": record.lineno,
        }

        # Format exception info if present
        if record.exc_info:
            log_entry["exception"] = redact_sensitive(self.formatException(record.exc_info))
        elif record.exc_text:
            log_entry["exception"] = redact_sensitive(record.exc_text)

        # Format stack info if present
        if record.stack_info:
            log_entry["stack_info"] = redact_sensitive(self.formatStack(record.stack_info))

        # Collect custom extra fields attached to the record
        extras: Dict[str, Any] = {}
        for key, val in record.__dict__.items():
            if key not in self._RESERVED_ATTRS and not key.startswith("_"):
                try:
                    if isinstance(val, (dict, list, int, float, bool)) or val is None:
                        extras[key] = val
                    else:
                        extras[key] = redact_sensitive(str(val))
                except Exception:
                    extras[key] = "<unserializable>"

        if extras:
            log_entry["extra"] = extras

        return json.dumps(log_entry, default=str)


class TextRedactingFormatter(logging.Formatter):
    """Human-readable plain text formatter with secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_sensitive(formatted)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    json_format: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure root and racon loggers with structured output and file rotation."""
    target_logger = logging.getLogger("racon")
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    target_logger.setLevel(numeric_level)

    # Remove pre-existing handlers to prevent duplicate output
    target_logger.handlers.clear()

    # Formatter selection
    formatter: logging.Formatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = TextRedactingFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s (%(module)s:%(lineno)d): %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console stdout handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    target_logger.addHandler(console_handler)

    # File handler with rotation
    target_file = log_file or get_log_path()
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(target_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        target_logger.addHandler(file_handler)
    except Exception as e:
        target_logger.warning("Failed to initialize file logger at %s: %s", target_file, e)

    target_logger.propagate = False
    return target_logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger childed under the racon namespace."""
    if not name or name == "racon":
        return logging.getLogger("racon")
    if name.startswith("racon."):
        return logging.getLogger(name)
    return logging.getLogger(f"racon.{name}")
