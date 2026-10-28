"""Application configuration and paths."""
import os
import logging
from pathlib import Path
from platformdirs import user_data_dir, user_log_dir

logger = logging.getLogger(__name__)

APP_NAME = "RACoN"
APP_AUTHOR = "RACoN"
VERSION = "1.0.1"

def get_data_dir() -> Path:
    """Return the per-user application data directory, creating it if needed."""
    p = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p

def get_log_dir() -> Path:
    """Return the per-user log directory, creating it if needed."""
    p = Path(user_log_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p

def get_db_path() -> Path:
    """Return the SQLite database file path."""
    return get_data_dir() / "racon_lab.db"

def get_log_path() -> Path:
    """Return the application log file path."""
    return get_log_dir() / "racon_lab.log"
