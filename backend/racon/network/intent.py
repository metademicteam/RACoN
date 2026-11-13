"""Decide whether a request should run on one worker or be decomposed.

The classifier is deterministic and auditable. It reuses the existing model-router
classification signals and adds task-shape signals. Tool requests always stay on one model:
splitting an agent's tool loop across workers would corrupt the conversation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from backend.racon.orchestration.model_router import Classification, classify_messages

NETWORK_MODELS = frozenset(
    {"racon", "racon-single", "racon-task", "racon-agent", "mesh", "auto"}
)

_TASK_SIGNALS = (
    "compare and contrast",
    "compare ",
    "research",
    "survey",
    "analyze",
    "analysis",
    "report",
    "white paper",
    "step by step",
    "multi-step",
    "plan for",
    "design a",
    "architecture",
    "implement a",
    "build a",
    "create a",
    "write a detailed",
    "in depth",
    "comprehensive",
    "thorough",
    "multiple",
    "at least",
    "for each",
    "pros and cons",
    "trade-off",
    "tradeoff",
    "recommend",
    "evaluate",
    "break down",
    "decompose",
    "sections",
    "deliverables",
)

_TOOL_LOOP_ROLES = frozenset({"tool"})


@dataclass(frozen=True)
class IntentDecision:
    mode: str  # "single" | "task" | "agent"
    model: Optional[str]
    classification: Classification
    forced: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "model": self.model,
            "forced": self.forced,
            "reason": self.reason,
            "classification": self.classification.to_dict(),
        }


def _message_dicts(messages: Iterable[Any]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for message in messages or []:
        if isinstance(message, dict):
            normalised.append(message)
        else:
            normalised.append(
                {
                    "role": str(getattr(message, "role", "user")),
                    "content": getattr(message, "content", ""),
                }
            )
    return normalised


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _has_tool_loop(messages: list[dict[str, Any]], tools: Any) -> bool:
    if isinstance(tools, list) and tools:
        return True
    for message in messages:
        if str(message.get("role") or "") in _TOOL_LOOP_ROLES:
            return True
        if message.get("tool_calls"):
            return True
    return False


def _task_score(messages: list[dict[str, Any]], classification: Classification) -> tuple[int, list[str]]:
    text = "\n".join(_content_text(message.get("content")) for message in messages).lower()
    signals: list[str] = []
    score = 0
    if classification.complexity.value == "deep":
        score += 2
        signals.append("deep-complexity")
    for phrase in _TASK_SIGNALS:
        if phrase in text:
            score += 1
            signals.append(phrase.strip())
    if len(text) > 900:
        score += 1
        signals.append("long-prompt")
    if len(messages) > 6:
        score += 1
        signals.append("long-conversation")
    if re.search(r"(?m)^\s*(\d+\.|[-*])\s+", text) and len(text) > 400:
        score += 1
        signals.append("structured-list")
    return score, signals


def choose_intent(
    model: str,
    messages: Iterable[Any],
    tools: Any = None,
) -> IntentDecision:
    requested = (model or "racon").strip()
    lowered = requested.lower()
    message_dicts = _message_dicts(messages)
    classification = classify_messages(message_dicts, tools=tools)

    if lowered == "racon-single":
        return IntentDecision(
            mode="single",
            model=None,
            classification=classification,
            forced=True,
            reason="model racon-single forces a single worker",
        )
    if lowered.startswith("racon-single:"):
        explicit = requested.split(":", 1)[1].strip() or None
        return IntentDecision(
            mode="single",
            model=explicit,
            classification=classification,
            forced=True,
            reason="racon-single:<model> forces one worker with an explicit model",
        )
    if lowered.startswith("racon-task:"):
        explicit = requested.split(":", 1)[1].strip() or None
        if _has_tool_loop(message_dicts, tools):
            return IntentDecision(
                mode="single",
                model=explicit,
                classification=classification,
                forced=True,
                reason="tool calls cannot be decomposed safely; using one worker",
            )
        return IntentDecision(
            mode="task",
            model=explicit,
            classification=classification,
            forced=True,
            reason="racon-task:<model> forces decomposition with an explicit model",
        )
    if lowered == "racon-agent":
        if _has_tool_loop(message_dicts, tools):
            return IntentDecision(
                mode="single",
                model=None,
                classification=classification,
                forced=True,
                reason="tool calls cannot be replaced by the agentic task layer",
            )
        return IntentDecision(
            mode="agent",
            model=None,
            classification=classification,
            forced=True,
            reason="racon-agent selects the Together AI agentic layer",
        )
    if lowered == "racon-task":
        if _has_tool_loop(message_dicts, tools):
            return IntentDecision(
                mode="single",
                model=None,
                classification=classification,
                forced=True,
                reason="tool calls cannot be decomposed safely; using one worker",
            )
        return IntentDecision(
            mode="task",
            model=None,
            classification=classification,
            forced=True,
            reason="model racon-task forces planner decomposition",
        )

    if lowered not in NETWORK_MODELS:
        return IntentDecision(
            mode="single",
            model=requested,
            classification=classification,
            forced=True,
            reason="explicit model request",
        )

    if _has_tool_loop(message_dicts, tools):
        return IntentDecision(
            mode="single",
            model=None,
            classification=classification,
            forced=False,
            reason="tool/agent requests stay on one model",
        )

    score, signals = _task_score(message_dicts, classification)
    if score >= 2:
        return IntentDecision(
            mode="task",
            model=None,
            classification=classification,
            forced=False,
            reason="task signals: " + ", ".join(signals[:5]),
        )
    return IntentDecision(
        mode="single",
        model=None,
        classification=classification,
        forced=False,
        reason=(
            "single-worker request"
            + (f" (signals: {', '.join(signals[:3])})" if signals else "")
        ),
    )
