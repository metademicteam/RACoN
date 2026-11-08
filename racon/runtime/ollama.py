"""Ollama runtime adapter.

Talks to a local or remote Ollama daemon over its HTTP API. Ollama keeps models
resident itself, so loading is implicit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from racon.models.catalog import ollama_tag_for
from racon.runtime.base import InferenceRuntime
from racon.runtime.tool_calls import normalise_tool_calls
from racon.runtime.types import (
    ChatChunk,
    ChatMessage,
    ChatResult,
    ChatUsage,
    GenerationOptions,
    InferenceCancelledError,
    InferenceExecutionError,
    ModelInfo,
    ModelNotFoundError,
    RuntimeCompatibilityError,
    RuntimeHealth,
    RuntimeUnavailableError,
)

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 120.0


class OllamaConnectionError(RuntimeUnavailableError):
    """Kept for callers that caught the original exception name."""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class OllamaRuntime(InferenceRuntime):
    """Client for the Ollama REST API."""

    name = "ollama"
    requires_explicit_load = False

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._external_client = client is not None
        self._client = client
        self._active_response: Optional[httpx.Response] = None
        self._cancelled = False

    # -- lifecycle ---------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
            )
        return self._client

    async def aclose(self) -> None:
        await self.cancel()
        if not self._external_client and self._client is not None:
            if not self._client.is_closed:
                await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OllamaRuntime":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    async def cancel(self) -> bool:
        self._cancelled = True
        response = self._active_response
        if response is None:
            return False
        try:
            await response.aclose()
        except Exception:  # pragma: no cover - best effort
            logger.debug("Closing Ollama stream failed", exc_info=True)
        return True

    # -- probes ------------------------------------------------------------

    async def available(self) -> bool:
        try:
            resp = await self._get_client().get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("Ollama unavailable at %s: %s", self.base_url, exc)
            return False

    async def is_available(self) -> bool:
        """Backwards-compatible alias used by older call sites."""
        return await self.available()

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._get_client().get("/api/tags")
        except Exception as exc:
            logger.debug("Ollama list_models failed: %s", exc)
            return []
        if resp.status_code != 200:
            logger.warning("Ollama /api/tags returned HTTP %d", resp.status_code)
            return []

        models: list[ModelInfo] = []
        for raw in resp.json().get("models", []) or []:
            details = raw.get("details") or {}
            models.append(
                ModelInfo(
                    id=raw.get("name") or raw.get("model") or "",
                    runtime=self.name,
                    display_name=raw.get("name") or raw.get("model") or "",
                    size_bytes=_int(raw.get("size")),
                    parameter_size=str(details.get("parameter_size", "")),
                    quantization=str(details.get("quantization_level", "")),
                    family=str(details.get("family", "")),
                    loaded=True,
                )
            )
        return models

    async def health(self) -> RuntimeHealth:
        ok = await self.available()
        models = [m.id for m in await self.list_models()] if ok else []
        return RuntimeHealth(
            runtime=self.name,
            available=ok,
            detail="" if ok else f"Ollama is unreachable at {self.base_url}",
            models=models,
            load_state="ready" if ok else "unavailable",
            extra={"base_url": self.base_url},
        )

    # -- generation --------------------------------------------------------

    @staticmethod
    def _ollama_messages(messages: list[ChatMessage], model: str) -> list[dict[str, Any]]:
        """Ollama expects string content. Convert OpenAI text parts; reject image parts."""
        converted: list[dict[str, Any]] = []
        for message in messages:
            payload = message.to_dict()
            content = payload.get("content")
            if isinstance(content, list):
                text_parts: list[str] = []
                has_media = False
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = str(part.get("type") or "")
                    if part_type in ("text", "input_text") and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
                    elif part_type in ("image_url", "input_image", "image", "audio_url", "input_audio"):
                        has_media = True
                if has_media:
                    raise RuntimeCompatibilityError(
                        "The Ollama runtime does not accept OpenAI media content parts",
                        {"model": model},
                    )
                payload["content"] = "\n".join(text_parts)
            converted.append(payload)
        return converted

    def _payload(
        self,
        messages: list[ChatMessage],
        options: GenerationOptions,
        *,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        opts = options.normalised()
        model_id = model or opts.model
        body: dict[str, Any] = {
            "model": model_id,
            "messages": self._ollama_messages(messages, model_id),
            "stream": False,
            "options": {
                "temperature": opts.temperature,
                "top_p": opts.top_p,
                "top_k": opts.top_k,
                "num_predict": opts.max_tokens,
            },
        }
        if opts.stop:
            body["options"]["stop"] = opts.stop
        if opts.seed is not None:
            body["options"]["seed"] = opts.seed
        if opts.context_size:
            body["options"]["num_ctx"] = opts.context_size
        if opts.tools:
            body["tools"] = opts.tools
        if opts.response_format:
            fmt = opts.response_format.get("type")
            if fmt == "json_object":
                body["format"] = "json"
            elif fmt == "json_schema":
                schema = (opts.response_format.get("json_schema") or {}).get("schema")
                if schema:
                    body["format"] = schema
        return body

    async def chat(self, messages: list[ChatMessage], options: GenerationOptions) -> ChatResult:
        self._cancelled = False
        started = time.monotonic()
        model_id = ollama_tag_for(options.model) or options.model
        body = self._payload(messages, options, model=model_id)
        try:
            resp = await self._get_client().post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise InferenceExecutionError(
                f"Ollama timed out after {self.timeout}s for model '{options.model}'",
                {"base_url": self.base_url},
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                f"Cannot reach Ollama at {self.base_url}. Start it with 'ollama serve'.",
                {"base_url": self.base_url, "error": str(exc)},
            ) from exc

        if resp.status_code == 404:
            raise ModelNotFoundError(
                f"Model '{options.model}' is not installed in Ollama",
                {"model": options.model, "ollama_model": model_id},
            )
        if resp.status_code != 200:
            raise InferenceExecutionError(
                f"Ollama returned HTTP {resp.status_code}",
                {"status_code": resp.status_code, "body": resp.text[:512]},
            )

        data = resp.json()
        message = data.get("message") or {}
        text = message.get("content") or ""
        usage = ChatUsage(
            prompt_tokens=_int(data.get("prompt_eval_count")),
            completion_tokens=_int(data.get("eval_count")),
        )
        tool_calls = normalise_tool_calls(message.get("tool_calls")) or None
        return ChatResult(
            text=text,
            model=data.get("model", options.model),
            runtime=self.name,
            usage=usage,
            duration_ms=(time.monotonic() - started) * 1000.0,
            finish_reason="tool_calls" if tool_calls else data.get("done_reason"),
            tool_calls=tool_calls,
        )

    async def chat_stream(
        self, messages: list[ChatMessage], options: GenerationOptions
    ) -> AsyncIterator[ChatChunk]:
        self._cancelled = False
        model_id = ollama_tag_for(options.model) or options.model
        body = self._payload(messages, options, model=model_id)
        body["stream"] = True
        started = time.monotonic()
        first_delta_at: Optional[float] = None
        prompt_tokens = 0
        completion_tokens = 0

        try:
            async with self._get_client().stream("POST", "/api/chat", json=body) as response:
                self._active_response = response
                if response.status_code == 404:
                    raise ModelNotFoundError(
                        f"Model '{options.model}' is not installed in Ollama",
                        {"model": options.model, "ollama_model": model_id},
                    )
                if response.status_code != 200:
                    text = (await response.aread()).decode("utf-8", "replace")[:512]
                    raise InferenceExecutionError(
                        f"Ollama returned HTTP {response.status_code}",
                        {"status_code": response.status_code, "body": text},
                    )

                async for line in response.aiter_lines():
                    if self._cancelled:
                        raise InferenceCancelledError("Generation cancelled by caller")
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    message = event.get("message") or {}
                    delta = message.get("content") or ""
                    tool_calls = normalise_tool_calls(message.get("tool_calls"))
                    if delta or tool_calls:
                        if delta and first_delta_at is None:
                            first_delta_at = time.monotonic()
                        yield ChatChunk(delta=delta, tool_calls=tool_calls or None)

                    if event.get("done"):
                        prompt_tokens = _int(event.get("prompt_eval_count"))
                        completion_tokens = _int(event.get("eval_count"))
                        yield ChatChunk(
                            done=True,
                            finish_reason=event.get("done_reason") or "stop",
                            usage=ChatUsage(prompt_tokens, completion_tokens),
                        )
                        return

        except (InferenceCancelledError, ModelNotFoundError, InferenceExecutionError):
            raise
        except httpx.TimeoutException as exc:
            raise InferenceExecutionError(
                f"Ollama stream timed out after {self.timeout}s", {"model": options.model}
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                f"Cannot reach Ollama at {self.base_url}",
                {"base_url": self.base_url, "error": str(exc)},
            ) from exc
        finally:
            self._active_response = None
            _ = started, first_delta_at

    # -- legacy single-prompt API -----------------------------------------

    async def execute(self, model_name: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Run a single prompt through /api/generate.

        Retained because the existing model routes and tests call it directly.
        """
        options = GenerationOptions(
            model=model_name,
            max_tokens=_int(kwargs.get("max_tokens") or kwargs.get("num_predict"), 512),
            temperature=float(kwargs.get("temperature", 0.7)),
            top_p=float(kwargs.get("top_p", 0.95)),
            top_k=_int(kwargs.get("top_k"), 40),
            stop=list(kwargs.get("stop") or []),
            seed=kwargs.get("seed"),
        ).normalised()

        ollama_model = ollama_tag_for(model_name) or model_name
        body: dict[str, Any] = {
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": options.temperature,
                "top_p": options.top_p,
                "top_k": options.top_k,
                "num_predict": options.max_tokens,
            },
        }
        if options.stop:
            body["options"]["stop"] = options.stop
        if kwargs.get("system"):
            body["system"] = kwargs["system"]

        try:
            resp = await self._get_client().post("/api/generate", json=body)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                f"Cannot reach Ollama at {self.base_url}",
                {"base_url": self.base_url, "error": str(exc)},
            ) from exc

        if resp.status_code == 404:
            raise ModelNotFoundError(
                f"Model '{model_name}' not found in Ollama",
                {"model": model_name, "ollama_model": ollama_model},
            )
        if resp.status_code != 200:
            raise InferenceExecutionError(
                f"Ollama returned HTTP {resp.status_code}",
                {"status_code": resp.status_code, "body": resp.text[:512]},
            )

        data = resp.json()
        total_duration = _int(data.get("total_duration"))
        eval_duration = _int(data.get("eval_duration"))
        return {
            "model": data.get("model", model_name),
            "response": data.get("response", ""),
            "total_duration": total_duration,
            "duration_sec": round(total_duration / 1e9, 4) if total_duration else 0.0,
            "eval_count": _int(data.get("eval_count")),
            "eval_duration": eval_duration,
            "eval_duration_sec": round(eval_duration / 1e9, 4) if eval_duration else 0.0,
            "prompt_eval_count": _int(data.get("prompt_eval_count")),
            "prompt_eval_duration": _int(data.get("prompt_eval_duration")),
            "done": data.get("done", True),
            "created_at": data.get("created_at"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def probe_ollama(base_url: str = DEFAULT_OLLAMA_URL, timeout: float = 2.0) -> bool:
    """Cheap availability probe used by health checks and tests."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            return resp.status_code == 200
    except (httpx.HTTPError, asyncio.TimeoutError):
        return False
