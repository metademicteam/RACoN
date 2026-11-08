"""Normalise tool calls from runtime-specific shapes to the OpenAI shape."""
from __future__ import annotations

import json
import secrets
from typing import Any, Iterable


def normalise_tool_calls(calls: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Return OpenAI-compatible tool call objects.

    Ollama returns ``{"function": {"name": ..., "arguments": {...}}}`` without an
    ``index``, ``type`` or string arguments. OpenAI clients (and agent harnesses) require
    ``{"index", "id", "type": "function", "function": {"name", "arguments": "<json>"}}``.
    The function is idempotent, so already-normalised calls pass through.
    """
    normalised: list[dict[str, Any]] = []
    for position, call in enumerate(calls or []):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(function.get("name") or call.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments", call.get("arguments", {}))
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments, ensure_ascii=False)
            except (TypeError, ValueError):
                arguments = "{}"
        index = call.get("index", position)
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = position
        normalised.append(
            {
                "index": index,
                "id": str(call.get("id") or f"call_{secrets.token_hex(6)}"),
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return normalised
