"""Local GGUF resolution helpers.

This module used to expose a second, simulated OpenAI-compatible router. That router has
been removed: ``/v1/chat/completions`` is now served by ``routes_engine`` which talks to a
real runtime and returns a structured error when none is available.

Only the model-path helper remains, because the desktop chat panel and the Linux
packaging tests depend on it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from racon.models.catalog import LOCAL_GEMMA_FILENAME

logger = logging.getLogger(__name__)

# Default model path relative to the repository root; RACON_GGUF_MODEL overrides it on any OS.
DEFAULT_GGUF_MODEL = str(Path(__file__).resolve().parents[2] / LOCAL_GEMMA_FILENAME)


def resolve_local_gguf() -> Optional[str]:
    """Find a local GGUF: explicit env var, the managed model store, then the repo root."""
    env_model = os.environ.get("RACON_GGUF_MODEL")
    if env_model and Path(env_model).is_file():
        return env_model

    candidates: List[Path] = []
    try:
        from racon.config import get_data_dir

        candidates.append(get_data_dir() / "models")
    except Exception:  # pragma: no cover - path lookup must never break import
        pass
    candidates.append(Path(DEFAULT_GGUF_MODEL).parent)

    for root in candidates:
        if not root.is_dir():
            continue
        matches = sorted(root.glob("*.gguf"))
        if matches:
            return str(matches[0])

    try:
        from racon.config import get_data_dir

        for path in sorted((get_data_dir() / "models").rglob("*.gguf")):
            return str(path)
    except Exception:  # pragma: no cover
        pass
    return None
