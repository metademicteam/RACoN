"""Application configuration and file system paths for the RACoN control plane.

Path helpers delegate to :mod:`racon.config` so the desktop app and the control plane
share one data directory. The database file name stays backend-specific so the local
desktop database and the control-plane database never collide.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from platformdirs import user_data_dir, user_log_dir

from racon.version import APP_NAME, APP_AUTHOR, __version__

logger = logging.getLogger(__name__)

VERSION = __version__

__all__ = [
    "APP_NAME",
    "APP_AUTHOR",
    "VERSION",
    "BackendSettings",
    "settings",
    "get_data_dir",
    "get_log_dir",
    "get_db_path",
    "get_log_path",
    "get_token_path",
]


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


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:1420",
    "http://localhost:1420",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
]


class BackendSettings:
    """Backend service runtime settings."""

    def __init__(self) -> None:
        self.host: str = os.environ.get("RACON_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("RACON_PORT", "8765"))
        self.ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        self.dev_mode: bool = os.environ.get("RACON_DEV_MODE", "false").lower() in ("true", "1", "yes")
        cors_env = os.environ.get("RACON_CORS_ORIGINS")
        self.cors_origins: list[str] = _split_csv(cors_env) if cors_env else list(DEFAULT_CORS_ORIGINS)

    @property
    def ollama_host(self) -> str:
        """Alias kept for callers that used the pydantic Settings field name."""
        return self.ollama_url

    @property
    def OLLAMA_HOST(self) -> str:
        """Compatibility alias for legacy call sites."""
        return self.ollama_url


settings = BackendSettings()
