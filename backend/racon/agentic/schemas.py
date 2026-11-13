"""Typed values exchanged inside the agentic scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

AgentMode = Literal["chat", "task"]
TaskStatus = Literal["queued", "running", "completed", "failed"]


@dataclass(frozen=True)
class AgentDecision:
    """The routing decision produced by the small decision model."""

    mode: AgentMode
    confidence: float
    reason: str
    source: str = "together"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "confidence": round(float(self.confidence), 3),
            "reason": self.reason,
            "source": self.source,
            "model": self.model,
        }


@dataclass
class AgentTask:
    """One independently executable unit planned by the agent."""

    task_id: str
    title: str
    instruction: str
    depends_on: list[str] = field(default_factory=list)
    worker_id: Optional[str] = None
    model: Optional[str] = None
    max_tokens: int = 512
    timeout_s: float = 180.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "depends_on": list(self.depends_on),
            "worker_id": self.worker_id,
            "model": self.model,
            "max_tokens": self.max_tokens,
        }


@dataclass
class TaskOutcome:
    """The only thing a worker returns to the coordinator: its final output."""

    task_id: str
    title: str
    status: TaskStatus
    worker_id: Optional[str] = None
    model: Optional[str] = None
    output: str = ""
    error: Optional[str] = None
    duration_ms: float = 0.0
    ttft_ms: Optional[float] = None
    claim_ms: Optional[float] = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and bool(self.output.strip())

    def to_dict(self, *, include_output: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "worker_id": self.worker_id,
            "model": self.model,
            "duration_ms": round(self.duration_ms, 1),
            "ttft_ms": round(self.ttft_ms, 1) if self.ttft_ms is not None else None,
            "claim_ms": round(self.claim_ms, 1) if self.claim_ms is not None else None,
            "error": self.error,
        }
        if include_output:
            payload["output"] = self.output
        return payload


@dataclass
class MergeResult:
    text: Optional[str]
    model: Optional[str] = None
    degraded: bool = False
    reason: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)


def merge_usage(*values: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Sum OpenAI-style usage dictionaries without inventing missing counts."""
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    reported = False
    for value in values:
        if not isinstance(value, dict):
            continue
        for key in totals:
            try:
                totals[key] += int(value.get(key) or 0)
            except (TypeError, ValueError):
                pass
        if value.get("reported"):
            reported = True
    totals["reported"] = reported
    return totals
