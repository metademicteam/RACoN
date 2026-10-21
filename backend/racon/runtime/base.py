"""Base interface and error definitions for RACoN inference runtimes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class RACoNRuntimeError(Exception):
    """Base exception for all RACoN runtime errors."""
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class RuntimeUnavailableError(RACoNRuntimeError):
    """Raised when an inference runtime daemon or service cannot be reached."""
    pass


class ModelNotFoundError(RACoNRuntimeError):
    """Raised when a specified model is not registered or loaded in the runtime."""
    pass


class InferenceExecutionError(RACoNRuntimeError):
    """Raised when inference generation fails during execution."""
    pass


class InferenceRuntime(ABC):
    """Abstract Base Class for inference runtimes in RACoN.

    All runtime backends (e.g. local Ollama, distributed execution,
    mock/simulation engines) must implement this contract.
    """

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the inference runtime service is reachable and responsive.

        Returns:
            bool: True if service is running and responsive, False otherwise.
        """
        pass

    @abstractmethod
    async def list_models(self) -> list[dict[str, Any]]:
        """List models available in this runtime.

        Returns:
            list[dict[str, Any]]: List of dictionary representations of
            available models, each containing at least 'name', 'size',
            'digest', and 'modified_at'.
        """
        pass

    @abstractmethod
    async def execute(
        self,
        model_name: str,
        prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute prompt inference against the specified model.

        Args:
            model_name: The name/tag of the target model (e.g. 'qwen2.5:1.5b').
            prompt: The input prompt string.
            **kwargs: Runtime-specific inference options (e.g. temperature, max_tokens).

        Returns:
            dict[str, Any]: Execution result containing response text, duration,
            eval_count, and timing metrics.
        """
        pass

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Return runtime health status and diagnostic metadata.

        Returns:
            dict[str, Any]: Diagnostic report containing status, base_url/target,
            availability, and any error messages.
        """
        pass
