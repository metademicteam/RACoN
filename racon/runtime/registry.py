"""Runtime adapter factory.

The scheduler and API resolve runtimes through here, so they never import a concrete
adapter and adding a backend is a one-line registration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from racon.runtime.base import InferenceRuntime
from racon.runtime.llamacpp import LlamaCppRuntime
from racon.runtime.ollama import OllamaRuntime

logger = logging.getLogger(__name__)

LLAMA_CPP = "llama_cpp"
OLLAMA = "ollama"

SUPPORTED_RUNTIMES = (LLAMA_CPP, OLLAMA)


def create_runtime(
    name: str,
    *,
    models_dir: Optional[Path] = None,
    ollama_url: str = "http://127.0.0.1:11434",
    context_size: int = 4096,
    n_gpu_layers: int = 0,
    threads: Optional[int] = None,
) -> InferenceRuntime:
    """Construct a runtime adapter by name."""
    key = (name or "").strip().lower()
    if key in (LLAMA_CPP, "llamacpp", "llama.cpp", "llama-server"):
        return LlamaCppRuntime(
            models_dir=models_dir,
            context_size=context_size,
            n_gpu_layers=n_gpu_layers,
            threads=threads,
        )
    if key == OLLAMA:
        return OllamaRuntime(base_url=ollama_url)
    raise ValueError(
        f"Unknown runtime '{name}'. Supported: {', '.join(SUPPORTED_RUNTIMES)}"
    )


async def available_runtimes(
    *,
    models_dir: Optional[Path] = None,
    ollama_url: str = "http://127.0.0.1:11434",
) -> list[str]:
    """Probe every supported runtime and return the ones usable right now."""
    found: list[str] = []
    llama = LlamaCppRuntime(models_dir=models_dir)
    if await llama.available():
        found.append(LLAMA_CPP)
    ollama = OllamaRuntime(base_url=ollama_url)
    try:
        if await ollama.available():
            found.append(OLLAMA)
    finally:
        await ollama.aclose()
    return found


def runtime_capabilities(name: str) -> dict[str, object]:
    """Static description used by the UI and the placement scheduler."""
    if name == LLAMA_CPP:
        return {
            "runtime": LLAMA_CPP,
            "display_name": "llama.cpp",
            "streaming": True,
            "requires_explicit_load": True,
            "supports_gguf": True,
            "supports_gpu_layers": True,
        }
    if name == OLLAMA:
        return {
            "runtime": OLLAMA,
            "display_name": "Ollama",
            "streaming": True,
            "requires_explicit_load": False,
            "supports_gguf": False,
            "supports_gpu_layers": False,
        }
    raise ValueError(f"Unknown runtime '{name}'")
