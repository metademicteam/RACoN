"""Configuration and application paths for RACoN."""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from platformdirs import user_data_dir, user_log_dir
from pydantic import AliasChoices, Field, field_validator
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
    """Return the SQLite database file path (racon_v2.db).

    ``RACON_DB_PATH`` overrides the location. Tests set it so they never touch the
    per-user database: a test process that opened the real file would mark live jobs
    failed through the interrupted-job recovery that runs on application start.
    """
    override = os.environ.get("RACON_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return get_data_dir() / "racon_v2.db"


def get_log_path() -> Path:
    """Return the default application log file path."""
    return get_log_dir() / "racon.log"


def get_token_path() -> Path:
    """Return the persisted local API token path."""
    return get_data_dir() / "racon_token.secret"


def _default_auth_token() -> str:
    """Return the configured API token, generating a per-install secret when none is set.

    There is deliberately no shared literal default: a shipped build must not contain a
    token that every other install also accepts.
    """
    env_token = os.environ.get("RACON_AUTH_TOKEN", os.environ.get("AUTH_TOKEN"))
    if env_token and env_token.strip():
        return env_token.strip()

    if os.environ.get("RACON_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return "racon-local-dev-token"

    token_path = get_token_path()
    try:
        if token_path.is_file():
            existing = token_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token, encoding="utf-8")
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        logger.warning("Could not persist the generated API token; it is valid for this process only")
    return token


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
        validation_alias=AliasChoices("RACON_AUTH_TOKEN", "AUTH_TOKEN"),
        description="Administrator authentication token. Never shipped in client builds.",
    )
    CLIENT_TOKEN: str = Field(
        default="",
        validation_alias=AliasChoices("CLIENT_TOKEN", "RACON_CLIENT_TOKEN"),
        description=(
            "Limited-scope token for desktop and web clients. Grants API access except "
            "admin operations and settings changes. Empty disables client access."
        ),
    )
    OLLAMA_HOST: str = Field(
        default=os.environ.get("OLLAMA_HOST", os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")),
        description="Ollama server endpoint URL",
    )
    SUPABASE_URL: str = Field(
        default="",
        description="Supabase project URL (server-side configuration only)",
    )
    SUPABASE_KEY: str = Field(
        default="",
        description="Supabase publishable key (server-side configuration only)",
    )
    SUPABASE_SECRET_KEY: str = Field(
        default="",
        description="Supabase secret key. Server-side only; never shipped in a desktop build.",
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

    # --- Together AI agentic scheduler -------------------------------------
    # The layer is opt-in. Docker Compose enables it by default; host installs must set
    # TOGETHER_ENABLED=true (or RACON_AGENTIC_ENABLED=true) and provide a key.
    TOGETHER_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices("TOGETHER_ENABLED", "RACON_AGENTIC_ENABLED"),
        description="Enable the Together AI router/agent/merge layer for network requests",
    )
    TOGETHER_API_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("TOGETHER_API_KEY", "RACON_TOGETHER_API_KEY"),
        description="Together AI API key. Server-side only; never shipped to clients.",
    )
    TOGETHER_BASE_URL: str = Field(
        default="https://api.together.xyz/v1",
        description="OpenAI-compatible Together AI API base URL",
    )
    TOGETHER_DECISION_MODEL: str = Field(
        default="together/Tev1-4B-experimental",
        description="Low-cost model that classifies chat vs task",
    )
    TOGETHER_AGENT_MODEL: str = Field(
        default="MiniMaxAI/MiniMax-M3.1-Flash",
        description="Agent model used for chat, planning/tool calls, and merge review",
    )
    TOGETHER_TIMEOUT_S: float = Field(
        default=90.0,
        gt=0.0,
        le=600.0,
        description="Per-request Together AI timeout in seconds",
    )
    TOGETHER_DECISION_MAX_TOKENS: int = Field(
        default=96,
        ge=16,
        le=512,
        description="Output budget for the routing decision",
    )
    TOGETHER_AGENT_MAX_TOKENS: int = Field(
        default=2048,
        ge=128,
        le=8192,
        description="Output budget for the agent chat/planning model",
    )
    RACON_AGENTIC_MAX_TASKS: int = Field(
        default=6,
        ge=1,
        le=12,
        description="Maximum tasks the agent may dispatch in one request",
    )
    RACON_AGENTIC_MAX_PARALLEL: int = Field(
        default=4,
        ge=1,
        le=12,
        description="Maximum worker tasks executed concurrently",
    )
    RACON_AGENTIC_MERGE_MAX_TOKENS: int = Field(
        default=2048,
        ge=64,
        le=4096,
        description="Bounded ceiling for the merge/review pass (reasoning + answer)",
    )
    RACON_AGENTIC_WORKER_MODEL: str = Field(
        default="",
        description="Preferred worker model for agent tasks; empty picks the fastest advertised model",
    )

    @field_validator(
        "TOGETHER_API_KEY",
        "TOGETHER_BASE_URL",
        "TOGETHER_DECISION_MODEL",
        "TOGETHER_AGENT_MODEL",
        "RACON_AGENTIC_WORKER_MODEL",
        mode="before",
    )
    @classmethod
    def _strip_agentic_strings(cls, v: Any) -> str:
        return "" if v is None else str(v).strip()

    @field_validator("TOGETHER_BASE_URL")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("AUTH_TOKEN", mode="before")
    @classmethod
    def _ensure_token(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return _default_auth_token()
        return str(v).strip()

    @field_validator("CLIENT_TOKEN", mode="before")
    @classmethod
    def _strip_client_token(cls, v: Any) -> str:
        if v is None:
            return ""
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
