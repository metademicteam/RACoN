"""Application configuration and file system paths for RACoN Backend."""
from __future__ import annotations

import os
import logging
from pathlib import Path
from platformdirs import user_data_dir, user_log_dir

logger = logging.getLogger(__name__)

APP_NAME = "RACoN Lab"
APP_AUTHOR = "RACoN"
VERSION = "0.1.0"


def get_data_dir() -> Path:
    """Return the per-user application data directory, creating it if needed."""
    p = Path(os.environ.get("RACON_DATA_DIR", user_data_dir(APP_NAME, APP_AUTHOR)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_log_dir() -> Path:
    """Return the per-user log directory, creating it if needed."""
    p = Path(os.environ.get("RACON_LOG_DIR", user_log_dir(APP_NAME, APP_AUTHOR)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_db_path() -> Path:
    """Return the SQLite database file path."""
    env_db = os.environ.get("RACON_DB_PATH")
    if env_db:
        return Path(env_db)
    return get_data_dir() / "racon_backend.db"


def get_log_path() -> Path:
    """Return the application log file path."""
    return get_log_dir() / "racon_backend.log"


def get_token_path() -> Path:
    """Return the ephemeral auth token secret file path."""
    return get_data_dir() / "racon_token.secret"


class BackendSettings:
    """Backend service runtime settings."""
    def __init__(self):
        self.host: str = os.environ.get("RACON_HOST", "0.0.0.0")
        self.port: int = int(os.environ.get("RACON_PORT", "8765"))
        self.ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        self.dev_mode: bool = os.environ.get("RACON_DEV_MODE", "false").lower() in ("true", "1", "yes")
        cors_env = os.environ.get("RACON_CORS_ORIGINS")
        if cors_env:
            self.cors_origins: list[str] = [o.strip() for o in cors_env.split(",") if o.strip()]
        else:
            self.cors_origins: list[str] = ["*"]


settings = BackendSettings()
