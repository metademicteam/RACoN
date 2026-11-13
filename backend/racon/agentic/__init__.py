"""Together AI agentic scheduler layer for the RACoN network gateway.

The layer sits in front of the OpenAI-compatible surface:

* a small decision model (Tev) classifies the request as ``chat`` or ``task``;
* normal chat is answered by the agent model (MiniMax);
* task requests are planned with tool calls against the live worker environment, the
  resulting tasks run in parallel on the fastest suitable workers, and only their final
  outputs return to the coordinator for a low-token merge/review pass.

Every stage is optional and degrades to the deterministic gateway path when Together AI is
disabled or unreachable.
"""

from __future__ import annotations

from .errors import AgenticRunError, AgenticUnavailable, TogetherError

__all__ = [
    "AgenticRunError",
    "AgenticService",
    "AgenticUnavailable",
    "TogetherError",
    "close_agentic_service",
    "get_agentic_service",
    "reset_agentic_service",
]


def get_agentic_service():
    from .service import get_agentic_service as _get

    return _get()


def reset_agentic_service() -> None:
    from .service import reset_agentic_service as _reset

    _reset()


async def close_agentic_service() -> None:
    from .service import close_agentic_service as _close

    await _close()


def __getattr__(name: str):
    if name == "AgenticService":
        from .service import AgenticService

        return AgenticService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
