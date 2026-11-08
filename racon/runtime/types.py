"""Normalised request/response types shared by every inference runtime.

The scheduler and the API layer speak only these types, so adding a runtime never
requires touching scheduling or transport code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ChatMessage:
    """A single chat turn.

    ``content`` may be a string, a list of OpenAI content parts, or ``None`` for an
    assistant turn that only carries tool calls.
    """

    role: Role
    content: Any = ""
    name: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            payload["content"] = self.content
        if self.name:
            payload["name"] = self.name
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass
class ChatUsage:
    """Token accounting reported by the runtime, never estimated by the client.

    ``reported`` is False when the backend gave no token counts at all. Metering code must
    treat those records as unmeasured instead of charging invented numbers.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reported: bool = True

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "reported": self.reported,
        }


@dataclass
class ChatChunk:
    """One streamed delta. `usage` is only populated on the final chunk."""

    delta: str = ""
    done: bool = False
    finish_reason: Optional[str] = None
    usage: Optional[ChatUsage] = None
    role: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None


@dataclass
class ChatResult:
    """Aggregate result of a completed generation."""

    text: str
    model: str
    runtime: str
    usage: ChatUsage = field(default_factory=ChatUsage)
    time_to_first_token_ms: Optional[float] = None
    duration_ms: float = 0.0
    finish_reason: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None

    @property
    def generation_tokens_per_second(self) -> Optional[float]:
        """Completion tokens divided by total duration, excluding time to first token."""
        generation_ms = self.duration_ms - (self.time_to_first_token_ms or 0.0)
        if generation_ms <= 0 or self.usage.completion_tokens <= 0:
            return None
        return round(self.usage.completion_tokens / (generation_ms / 1000.0), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "runtime": self.runtime,
            "usage": self.usage.to_dict(),
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "duration_ms": round(self.duration_ms, 2),
            "generation_tokens_per_second": self.generation_tokens_per_second,
            "finish_reason": self.finish_reason,
            "tool_calls": self.tool_calls,
        }


@dataclass
class ModelInfo:
    """A model known to a runtime."""

    id: str
    runtime: str
    display_name: str = ""
    size_bytes: int = 0
    context_length: Optional[int] = None
    parameter_size: str = ""
    quantization: str = ""
    family: str = ""
    loaded: bool = False
    path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "runtime": self.runtime,
            "display_name": self.display_name or self.id,
            "size_bytes": self.size_bytes,
            "context_length": self.context_length,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
            "family": self.family,
            "loaded": self.loaded,
            "path": self.path,
        }


@dataclass
class RuntimeHealth:
    """Health probe result for a runtime adapter."""

    runtime: str
    available: bool
    detail: str = ""
    models: list[str] = field(default_factory=list)
    load_state: str = "unloaded"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "available": self.available,
            "detail": self.detail,
            "models": self.models,
            "load_state": self.load_state,
            **self.extra,
        }


class RaconRuntimeError(Exception):
    """Base class for runtime failures surfaced to callers as structured errors."""

    code = "runtime_error"
    http_status = 500

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class RuntimeUnavailableError(RaconRuntimeError):
    """The runtime binary or daemon is not present or not answering."""

    code = "runtime_unavailable"
    http_status = 503


class ModelNotFoundError(RaconRuntimeError):
    """The requested model is not installed for this runtime."""

    code = "model_not_found"
    http_status = 404


class ModelNotLoadedError(RaconRuntimeError):
    """A generation was requested before the model was loaded."""

    code = "model_not_loaded"
    http_status = 409


class InferenceExecutionError(RaconRuntimeError):
    """Generation failed inside the runtime."""

    code = "inference_failed"
    http_status = 502


class InferenceCancelledError(RaconRuntimeError):
    """The caller cancelled the request."""

    code = "inference_cancelled"
    http_status = 499


class RuntimeCompatibilityError(RaconRuntimeError):
    """The model cannot run on this runtime or on this hardware."""

    code = "runtime_incompatible"
    http_status = 422


@dataclass
class GenerationOptions:
    """Runtime-agnostic generation parameters."""

    model: str
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 40
    stop: list[str] = field(default_factory=list)
    context_size: Optional[int] = None
    seed: Optional[int] = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: Any = None
    parallel_tool_calls: Optional[bool] = None
    response_format: Optional[dict[str, Any]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None

    def normalised(self) -> "GenerationOptions":
        """Clamp values into ranges every runtime accepts."""
        return GenerationOptions(
            model=self.model,
            max_tokens=max(1, min(int(self.max_tokens), 32768)),
            temperature=min(max(float(self.temperature), 0.0), 2.0),
            top_p=min(max(float(self.top_p), 0.0), 1.0),
            top_k=min(max(int(self.top_k), 0), 1000),
            stop=[s for s in self.stop if s][:8],
            context_size=self.context_size,
            seed=self.seed,
            tools=list(self.tools or []),
            tool_choice=self.tool_choice,
            parallel_tool_calls=self.parallel_tool_calls,
            response_format=self.response_format,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
        )
