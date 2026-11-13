"""Small-model routing decision: normal chat or a worker task."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from backend.racon.network.intent import choose_intent

from .errors import AgenticUnavailable, TogetherError
from .schemas import AgentDecision

logger = logging.getLogger(__name__)

DECISION_SYSTEM_PROMPT = """You are the routing model of RACoN, a distributed AI system.
Decide whether the user request should be answered directly or decomposed into work for
worker GPU/CPU nodes.

chat = greetings, explanations, definitions, single code snippets, short advice, and any
request one model can answer in one pass.
task = research/comparison, multi-section reports, planning with several deliverables, or a
request that explicitly asks for parallel work.

Answer with exactly one lowercase word and nothing else: chat or task."""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
        return "\n".join(parts)
    return ""


def conversation_text(messages: list[dict[str, Any]], *, limit: int = 12000) -> str:
    """Flatten the last messages into a bounded transcript for the router."""
    lines: list[str] = []
    for message in messages[-12:]:
        role = str(message.get("role") or "user").upper()
        text = _content_text(message.get("content")).strip()
        if text:
            lines.append(f"{role}: {text[:4000]}")
    joined = "\n\n".join(lines).strip()
    return joined[:limit]


def _parse_decision(text: str) -> tuple[str, float, str]:
    candidate = (text or "").strip()
    if not candidate:
        raise ValueError("decision response was empty")

    # Preferred contract: one lowercase word.
    words = re.findall(r"[a-zA-Z]+", candidate)
    if words and words[0].lower() in ("chat", "task"):
        return words[0].lower(), 0.9, "router label"

    # Some deployments return JSON (or a truncated JSON prefix) instead.
    if candidate.startswith("{"):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            match = re.search(r'"mode"\s*:\s*"(chat|task)"', candidate, re.IGNORECASE)
            if match:
                return match.group(1).lower(), 0.8, "router JSON prefix"
            raise ValueError("decision response contained an incomplete JSON object")
        if not isinstance(data, dict):
            raise ValueError("decision JSON root must be an object")
        mode = str(data.get("mode") or "").strip().lower()
        if mode not in ("chat", "task"):
            raise ValueError(f"unknown decision mode '{mode}'")
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        reason = str(data.get("reason") or "").strip()[:160]
        return mode, confidence, reason

    raise ValueError(f"decision response was not a chat/task label: {candidate[:60]!r}")


def heuristic_decision(
    messages: list[dict[str, Any]], tools: Any = None, *, reason: str = ""
) -> AgentDecision:
    """Deterministic fallback when the router model returns unusable output."""
    intent = choose_intent("racon", messages, tools=tools)
    mode = "task" if intent.mode == "task" else "chat"
    return AgentDecision(
        mode=mode,
        confidence=0.5,
        reason=(reason or f"Heuristic fallback: {intent.reason}")[:160],
        source="heuristic",
    )


async def decide(
    client,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: Any = None,
    max_tokens: int = 96,
    timeout_s: float = 30.0,
) -> AgentDecision:
    """Ask the small model for a routing decision.

    Transport/authentication failures raise ``AgenticUnavailable`` so the gateway can use
    the deterministic path. A syntactically bad answer falls back to the local classifier.
    """
    prompt = conversation_text(messages)
    request_messages = [
        {"role": "system", "content": DECISION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        # The experimental decision model has a very small output window, so ask for one
        # word instead of a JSON object. The parser also accepts JSON if a deployment
        # returns it.
        result = await client.chat(
            request_messages,
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
        )
    except TogetherError as exc:
        logger.warning("Routing model unavailable (%s): %s", exc.code, exc)
        raise AgenticUnavailable(f"Together AI routing failed: {exc}") from exc

    try:
        mode, confidence, reason = _parse_decision(result.get("text") or "")
    except (ValueError, json.JSONDecodeError) as exc:
        logger.info("Routing model returned an invalid decision (%s); using heuristics", exc)
        return heuristic_decision(
            messages, tools, reason=f"Router output was invalid ({exc})"
        )

    return AgentDecision(
        mode=mode,
        confidence=confidence,
        reason=reason,
        source="together",
        model=model,
        usage=result.get("usage") or {},
    )
