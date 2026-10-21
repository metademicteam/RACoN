"""Core module for RACoN: configuration, settings, paths, and logging."""

from __future__ import annotations

from backend.racon.core.config import (
    APP_AUTHOR,
    APP_NAME,
    Settings,
    get_data_dir,
    get_db_path,
    get_log_dir,
    get_log_path,
    get_settings,
)
from backend.racon.core.logging import (
    JSONFormatter,
    TextRedactingFormatter,
    get_logger,
    setup_logging,
)

__all__ = [
    "APP_AUTHOR",
    "APP_NAME",
    "JSONFormatter",
    "Settings",
    "TextRedactingFormatter",
    "get_data_dir",
    "get_db_path",
    "get_log_dir",
    "get_log_path",
    "get_logger",
    "get_settings",
    "setup_logging",
]
