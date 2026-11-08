"""Unified product settings for the desktop app, provider agent and control plane.

One object, one precedence order:

1. explicit process environment
2. ``.env`` next to the repository (or next to the executable in a frozen build)
3. built-in defaults

Secrets are only ever read from the environment or ``.env``. There are no credential
defaults in code, so a shipped binary contains no usable key.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from platformdirs import user_data_dir, user_log_dir

from racon.version import APP_AUTHOR, APP_NAME

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_CONTEXT_SIZE = 4096
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:1420,http://localhost:1420,"
    "http://127.0.0.1:8765,http://localhost:8765"
)

_TRUE = {"1", "true", "yes", "on"}


def _env_file_candidates() -> list[Path]:
    here = Path(__file__).resolve().parents[1]
    candidates = [Path.cwd() / ".env", here / ".env"]
    import sys

    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent / ".env")
    return candidates


def load_dotenv(path: Optional[Path] = None) -> dict[str, str]:
    """Minimal ``.env`` reader.

    Only ``KEY=VALUE`` lines are understood; comments and blank lines are ignored. Values
    already present in the process environment always win, so a real env var can override
    a checked-out ``.env`` without editing it.
    """
    values: dict[str, str] = {}
    candidates = [path] if path else _env_file_candidates()
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values.setdefault(key, value)
        break
    return values


def _setting(name: str, file_values: dict[str, str], default: str = "") -> str:
    if name in os.environ and os.environ[name] != "":
        return os.environ[name]
    if name in file_values and file_values[name] != "":
        return file_values[name]
    return default


def _int_setting(name: str, file_values: dict[str, str], default: int) -> int:
    raw = _setting(name, file_values, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_setting(name: str, file_values: dict[str, str], default: bool = False) -> bool:
    raw = _setting(name, file_values, "")
    if not raw:
        return default
    return raw.strip().lower() in _TRUE


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    """Resolved runtime configuration."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    runtime: str = "llama_cpp"
    ollama_url: str = DEFAULT_OLLAMA_URL
    context_size: int = DEFAULT_CONTEXT_SIZE
    n_gpu_layers: int = 0
    threads: Optional[int] = None
    data_dir: Path = field(default_factory=lambda: Path(user_data_dir(APP_NAME, APP_AUTHOR)))
    log_dir: Path = field(default_factory=lambda: Path(user_log_dir(APP_NAME, APP_AUTHOR)))
    models_dir: Optional[Path] = None
    control_plane_url: str = ""
    coordinator_url: str = ""
    gateway_url: str = ""
    auth_token: str = ""
    dev_mode: bool = False
    cors_origins: list[str] = field(default_factory=lambda: _csv(DEFAULT_CORS_ORIGINS))
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    supabase_access_token: str = ""
    hf_token: str = ""

    # -- derived paths -----------------------------------------------------

    @property
    def resolved_models_dir(self) -> Path:
        path = Path(self.models_dir) if self.models_dir else self.data_dir / "models"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "racon_lab.db"

    @property
    def is_server(self) -> bool:
        """True when this process is allowed to see server-side credentials."""
        return bool(self.supabase_secret_key)

    @property
    def platform_name(self) -> str:
        return platform.system()

    # -- safe serialisation ------------------------------------------------

    def public_dict(self) -> dict[str, Any]:
        """Configuration safe to send to the desktop UI. Never includes secrets."""
        return {
            "host": self.host,
            "port": self.port,
            "runtime": self.runtime,
            "ollama_url": self.ollama_url,
            "context_size": self.context_size,
            "n_gpu_layers": self.n_gpu_layers,
            "threads": self.threads,
            "data_dir": str(self.data_dir),
            "models_dir": str(self.resolved_models_dir),
            "control_plane_url": self.control_plane_url,
            "coordinator_url": self.coordinator_url,
            "gateway_url": self.gateway_url,
            "dev_mode": self.dev_mode,
            "platform": self.platform_name,
            "supabase_configured": bool(self.supabase_url),
            "has_secret_key": bool(self.supabase_secret_key),
        }


def build_settings(env_file: Optional[Path] = None) -> Settings:
    """Resolve settings from the environment, ``.env`` and defaults."""
    file_values = load_dotenv(env_file)

    data_dir_raw = _setting("RACON_DATA_DIR", file_values, "")
    data_dir = Path(data_dir_raw) if data_dir_raw else Path(user_data_dir(APP_NAME, APP_AUTHOR))

    log_dir_raw = _setting("RACON_LOG_DIR", file_values, "")
    log_dir = Path(log_dir_raw) if log_dir_raw else Path(user_log_dir(APP_NAME, APP_AUTHOR))

    models_dir_raw = _setting("RACON_MODEL_DIR", file_values, "")
    threads_raw = _setting("RACON_THREADS", file_values, "")

    cors_raw = _setting("RACON_CORS_ORIGINS", file_values, DEFAULT_CORS_ORIGINS)

    return Settings(
        host=_setting("RACON_HOST", file_values, DEFAULT_HOST),
        port=_int_setting("RACON_PORT", file_values, DEFAULT_PORT),
        runtime=_setting("RACON_RUNTIME", file_values, "llama_cpp"),
        ollama_url=_setting("RACON_OLLAMA_URL", file_values, _setting("OLLAMA_URL", file_values, DEFAULT_OLLAMA_URL)),
        context_size=_int_setting("RACON_CONTEXT_SIZE", file_values, DEFAULT_CONTEXT_SIZE),
        n_gpu_layers=_int_setting("RACON_N_GPU_LAYERS", file_values, 0),
        threads=int(threads_raw) if threads_raw.isdigit() else None,
        data_dir=data_dir,
        log_dir=log_dir,
        models_dir=Path(models_dir_raw) if models_dir_raw else None,
        control_plane_url=_setting("RACON_CONTROL_PLANE_URL", file_values, ""),
        coordinator_url=_setting("RACON_COORDINATOR_URL", file_values, ""),
        gateway_url=_setting("RACON_GATEWAY_URL", file_values, ""),
        auth_token=_setting("RACON_AUTH_TOKEN", file_values, _setting("AUTH_TOKEN", file_values, "")),
        dev_mode=_bool_setting("RACON_DEV_MODE", file_values, False),
        cors_origins=_csv(cors_raw),
        supabase_url=_setting("SUPABASE_URL", file_values, ""),
        supabase_publishable_key=_setting(
            "SUPABASE_PUBLISHABLE_KEY", file_values, _setting("SUPABASE_KEY", file_values, "")
        ),
        supabase_secret_key=_setting("SUPABASE_SECRET_KEY", file_values, ""),
        supabase_access_token=_setting("SUPABASE_ACCESS_TOKEN", file_values, ""),
        hf_token=_setting(
            "HF_TOKEN",
            file_values,
            _setting("HUGGINGFACE_TOKEN", file_values, _setting("HUGGING_FACE_HUB_TOKEN", file_values, "")),
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    settings = build_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings


def reset_settings() -> None:
    """Drop the cached settings (used by tests and by the settings UI)."""
    get_settings.cache_clear()
