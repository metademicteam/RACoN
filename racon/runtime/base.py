"""The single inference interface every RACoN runtime implements.

Schedulers, the provider agent and the HTTP API depend on this contract only. Adding a
new backend means writing one adapter; no scheduler or transport code changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from racon.runtime.types import (
    ChatChunk,
    ChatMessage,
    ChatResult,
    GenerationOptions,
    ModelInfo,
    RuntimeHealth,
)


class InferenceRuntime(ABC):
    """Contract for a model runtime."""

    name: str = "base"
    #: Whether this runtime keeps models resident and requires an explicit load step.
    requires_explicit_load: bool = False

    @abstractmethod
    async def available(self) -> bool:
        """Return True when the runtime is usable on this machine right now."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return models this runtime can serve without further downloads."""

    async def load(self, model: str) -> None:
        """Make a model ready for generation. No-op for daemon-style runtimes."""

    async def unload(self, model: Optional[str] = None) -> None:
        """Release a model. No-op for daemon-style runtimes."""

    @abstractmethod
    async def chat(
        self, messages: list[ChatMessage], options: GenerationOptions
    ) -> ChatResult:
        """Run a full generation and return the aggregate result."""

    @abstractmethod
    def chat_stream(
        self, messages: list[ChatMessage], options: GenerationOptions
    ) -> AsyncIterator[ChatChunk]:
        """Stream deltas. Implementations must yield a final chunk with ``done=True``."""

    async def cancel(self) -> bool:
        """Abort the in-flight generation, if any. Returns True when something was aborted."""
        return False

    @abstractmethod
    async def health(self) -> RuntimeHealth:
        """Report availability and load state without raising."""

    async def aclose(self) -> None:
        """Release network and process resources."""
