"""Configuration and application paths for RACoN."""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from platformdirs import user_data_dir, user_log_dir
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "racon"
APP_AUTHOR = "RACoN"


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
    """Return the SQLite database file path (racon_v2.db)."""
    return get_data_dir() / "racon_v2.db"


def get_log_path() -> Path:
    """Return the default application log file path."""
    return get_log_dir() / "racon.log"


def _default_auth_token() -> str:
    """Return configured token or standard local development token."""
    env_token = os.environ.get("RACON_AUTH_TOKEN", os.environ.get("AUTH_TOKEN"))
    if env_token and env_token.strip():
        return env_token.strip()
    return "racon-local-dev-token"


def _default_laya_cache_dir() -> Path:
    """Return the default LAYA model cache directory."""
    path = get_data_dir() / "models" / "laya"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Settings(BaseSettings):
    """RACoN application settings with environment variable overrides."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    API_HOST: str = Field(default=os.environ.get("RACON_HOST", "0.0.0.0"), description="FastAPI server host")
    API_PORT: int = Field(default=int(os.environ.get("RACON_PORT", "8765")), description="FastAPI server port")
    AUTH_TOKEN: str = Field(
        default_factory=_default_auth_token,
        description="Shared secret authentication token",
    )
    OLLAMA_HOST: str = Field(
        default=os.environ.get("OLLAMA_HOST", os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")),
        description="Ollama server endpoint URL",
    )
    SUPABASE_URL: str = Field(
        default="https://aqqfhueypxkikgcbrfbn.supabase.co",
        description="Supabase project URL",
    )
    SUPABASE_KEY: str = Field(
        default="sb_publishable_DeyUioAnjw7JEub6D0ppSA_Veei_BQc",
        description="Supabase public/anon API key",
    )
    LAYA_MODEL_CACHE_DIR: Path = Field(
        default_factory=_default_laya_cache_dir,
        description="Local directory for cached LAYA model weights and checkpoints",
    )
    REDIS_URL: str = Field(
        default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        description="Redis connection URL for distributed caching and task coordination",
    )
    LLAMA_SERVER_URL: str = Field(
        default=os.environ.get("LLAMA_SERVER_URL", "http://racn-llama:8080"),
        description="llama.cpp inference server URL",
    )
    MODEL_REGISTRY_URL: str = Field(
        default=os.environ.get("MODEL_REGISTRY_URL", "http://racn-model-registry:8001"),
        description="Model registry service endpoint",
    )
    TAILSCALE_FUNNEL_ENABLED: bool = Field(
        default=True,
        description="Whether Tailscale Funnel global internet access is enabled",
    )

    @field_validator("AUTH_TOKEN", mode="before")
    @classmethod
    def _ensure_token(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return _default_auth_token()
        return str(v).strip()

    @field_validator("LAYA_MODEL_CACHE_DIR", mode="before")
    @classmethod
    def _ensure_cache_dir(cls, v: Any) -> Path:
        if v is None or (isinstance(v, str) and not v.strip()):
            return _default_laya_cache_dir()
        p = Path(v)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton instance of application settings."""
    return Settings()
