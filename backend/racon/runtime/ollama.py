"""Ollama inference runtime implementation for RACoN.

Integrates with local Ollama daemon over REST API (default http://127.0.0.1:11434).
Provides health probing, model enumeration, and non-streaming prompt execution
with robust connection refusal and error handling.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from backend.racon.runtime.base import (
    InferenceExecutionError,
    InferenceRuntime,
    ModelNotFoundError,
    RuntimeUnavailableError,
)

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 30.0


class OllamaConnectionError(RuntimeUnavailableError):
    """Raised when connecting to Ollama fails (connection refused or timeout)."""
    pass


class OllamaRuntime(InferenceRuntime):
    """Client for local Ollama REST API.

    Connects to Ollama REST endpoints:
    - GET /api/tags - Enumerate installed models
    - POST /api/generate - Run prompt generation
    - GET / - Server status
    """

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ):
        """Initialize the Ollama runtime client.

        Args:
            base_url: Base URL of the Ollama service.
            timeout: Timeout in seconds for API requests (default 30.0s).
            client: Optional externally managed httpx.AsyncClient instance.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._external_client = client is not None
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def close(self) -> None:
        """Close the internal HTTP client session if owned."""
        if not self._external_client and self._client is not None:
            if not self._client.is_closed:
                await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OllamaRuntime":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def is_available(self) -> bool:
        """Check if Ollama service is reachable and responsive at base_url.

        Tests GET /api/tags or GET / with a short timeout.
        Never raises exceptions; returns False on failure.
        """
        try:
            client = self._get_client()
            resp = await client.get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            logger.debug("Ollama is_available probe failed at %s: %s", self.base_url, e)
            return False
        except Exception as e:
            logger.debug("Ollama unexpected probe error at %s: %s", self.base_url, e)
            return False

    async def list_models(self) -> list[dict[str, Any]]:
        """Fetch models installed in the local Ollama instance.

        Calls GET /api/tags and parses model descriptors.

        Returns:
            list[dict[str, Any]]: List of models with keys 'name', 'size',
            'digest', 'modified_at', and extracted metadata. Returns empty list
            if service is offline.
        """
        try:
            client = self._get_client()
            resp = await client.get("/api/tags")
            if resp.status_code != 200:
                logger.warning(
                    "Ollama GET /api/tags returned HTTP %d: %s",
                    resp.status_code,
                    resp.text,
                )
                return []

            data = resp.json()
            raw_models = data.get("models", [])
            parsed_models: list[dict[str, Any]] = []

            for m in raw_models:
                size_bytes = m.get("size", 0)
                details = m.get("details", {}) or {}
                parsed_models.append(
                    {
                        "name": m.get("name", ""),
                        "model": m.get("model", m.get("name", "")),
                        "size": size_bytes,
                        "size_mb": round(size_bytes / (1024 * 1024), 2),
                        "digest": m.get("digest", ""),
                        "modified_at": m.get("modified_at", ""),
                        "details": details,
                        "capabilities": m.get("capabilities", []),
                        "parameter_size": details.get("parameter_size", ""),
                        "family": details.get("family", ""),
                        "quantization_level": details.get("quantization_level", ""),
                    }
                )
            return parsed_models

        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            logger.warning(
                "Ollama connection refused at %s while listing models: %s",
                self.base_url,
                e,
            )
            return []
        except Exception as e:
            logger.error("Failed to list Ollama models: %s", e)
            return []

    async def execute(
        self,
        model_name: str,
        prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute a prompt completion against the specified Ollama model.

        Calls POST /api/generate with stream=False.

        Args:
            model_name: Name of the model (e.g. 'qwen2.5:1.5b').
            prompt: Text prompt string.
            **kwargs: Extra parameters (e.g. temperature, max_tokens, system).

        Returns:
            dict[str, Any]: Generation result including response text and timing.

        Raises:
            OllamaConnectionError: If Ollama service cannot be reached.
            ModelNotFoundError: If requested model is not found on Ollama.
            InferenceExecutionError: If generation fails on the server.
        """
        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
        }

        # Build options dictionary
        options: dict[str, Any] = kwargs.get("options", {}).copy()
        for opt_key in ("temperature", "top_p", "top_k", "num_predict", "seed", "stop"):
            if opt_key in kwargs and opt_key not in options:
                options[opt_key] = kwargs[opt_key]
        if options:
            payload["options"] = options

        if "system" in kwargs:
            payload["system"] = kwargs["system"]

        try:
            client = self._get_client()
            resp = await client.post("/api/generate", json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            msg = (
                f"Connection refused connecting to Ollama at {self.base_url}. "
                "Please verify that the Ollama service is running (`ollama serve`)."
            )
            logger.error("%s (details: %s)", msg, e)
            raise OllamaConnectionError(msg, details={"base_url": self.base_url, "error": str(e)}) from e
        except httpx.TimeoutException as e:
            msg = (
                f"Ollama inference request timed out after {self.timeout}s "
                f"for model '{model_name}' at {self.base_url}."
            )
            logger.error(msg)
            raise InferenceExecutionError(msg, details={"model": model_name, "timeout": self.timeout}) from e
        except Exception as e:
            msg = f"Unexpected communication error with Ollama at {self.base_url}: {e}"
            logger.error(msg)
            raise OllamaConnectionError(msg, details={"base_url": self.base_url, "error": str(e)}) from e

        if resp.status_code == 404:
            msg = f"Model '{model_name}' not found on Ollama at {self.base_url}."
            raise ModelNotFoundError(msg, details={"model": model_name, "status_code": 404})

        if resp.status_code != 200:
            msg = f"Ollama generation failed with HTTP {resp.status_code}: {resp.text}"
            raise InferenceExecutionError(
                msg,
                details={"model": model_name, "status_code": resp.status_code, "response": resp.text},
            )

        data = resp.json()
        total_duration = data.get("total_duration", 0) or 0
        eval_duration = data.get("eval_duration", 0) or 0

        return {
            "model": data.get("model", model_name),
            "response": data.get("response", ""),
            "total_duration": total_duration,
            "duration_sec": round(total_duration / 1e9, 4) if total_duration else 0.0,
            "eval_count": data.get("eval_count", 0) or 0,
            "eval_duration": eval_duration,
            "eval_duration_sec": round(eval_duration / 1e9, 4) if eval_duration else 0.0,
            "prompt_eval_count": data.get("prompt_eval_count", 0) or 0,
            "prompt_eval_duration": data.get("prompt_eval_duration", 0) or 0,
            "done": data.get("done", True),
            "created_at": data.get("created_at"),
            "context": data.get("context", []),
        }

    async def health(self) -> dict[str, Any]:
        """Check Ollama service health and return status summary.

        Returns:
            dict[str, Any]: Health summary report.
        """
        is_avail = await self.is_available()
        models: list[dict[str, Any]] = []
        error_msg: Optional[str] = None

        if is_avail:
            models = await self.list_models()
        else:
            error_msg = (
                f"Ollama is unreachable at {self.base_url}. "
                "Ensure Ollama is started ('ollama serve')."
            )

        return {
            "runtime": "ollama",
            "status": "online" if is_avail else "offline",
            "available": is_avail,
            "base_url": self.base_url,
            "model_count": len(models),
            "models": [m["name"] for m in models],
            "error": error_msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
