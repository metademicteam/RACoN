"""Application configuration and paths."""
import logging
from pathlib import Path

from platformdirs import user_data_dir, user_log_dir

from racon.version import APP_NAME, APP_AUTHOR, __version__

logger = logging.getLogger(__name__)

__all__ = [
    "APP_NAME",
    "APP_AUTHOR",
    "VERSION",
    "get_data_dir",
    "get_log_dir",
    "get_db_path",
    "get_log_path",
]

VERSION = __version__


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
