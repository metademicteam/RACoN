"""Synthesis agent.

Receives the original request and the outcomes of every planned task. It may only use the
supplied results. When a task failed, the gap is stated explicitly in the final answer. If
the synthesis runtime is unavailable, the caller receives the structured results and an
explicit degraded status instead of a fabricated answer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from backend.racon.orchestration.executor import TaskExecution
from backend.racon.orchestration.schemas import PlanRequest, PlanSpec
from racon.runtime.types import ChatMessage, RaconRuntimeError

logger = logging.getLogger(__name__)

MAX_TASK_SNIPPET_CHARS = 4000
MAX_SYNTHESIS_INPUT_CHARS = 28000

SYNTHESIS_SYSTEM_PROMPT = """You are the synthesis agent of RACoN, a distributed AI system.

You receive the user's original request and the outcomes of tasks executed by workers.
Produce one final answer for the user.

Rules:
1. Use ONLY the supplied task results. Never invent facts, figures, sources, or citations.
2. If a task failed or was not executed, state explicitly which part could not be completed.
   Do not pretend that missing work was done.
3. Remove duplication and resolve contradictions by pointing them out.
4. Answer the original request completely and directly.
5. Do not expose internal task ids unless the user needs them to understand a gap."""


@dataclass
class SynthesisResult:
    text: Optional[str]
    synthesis_available: bool
    complete: bool
    reason: Optional[str] = None
    missing: list[dict[str, str]] = field(default_factory=list)
    model: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "synthesis_available": self.synthesis_available,
            "complete": self.complete,
            "reason": self.reason,
            "missing": self.missing,
            "model": self.model,
        }


class Synthesizer:
    """LLM-backed merge step with an explicit degraded path."""

    supports_streaming = True

    def __init__(
        self,
        engine_factory=None,
        *,
        max_tokens: int = 1200,
        cap_tokens: Optional[int] = None,
    ) -> None:
        self._engine_factory = engine_factory or _default_engine_factory
        self._max_tokens = max_tokens
        #: Hard ceiling for the merge step. A merged answer longer than this is rarely
        #: useful, and every extra token is multiplied by the host's tokens/second cost.
        self._cap_tokens = int(cap_tokens) if cap_tokens else None

    async def synthesize(
        self,
        request: PlanRequest,
        plan: PlanSpec,
        executions: dict[str, TaskExecution],
        *,
        on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> SynthesisResult:
        completed = [ex for ex in executions.values() if ex.succeeded]
        missing = [
            {
                "task_id": ex.task_id,
                "error": ex.error or f"task ended in state '{ex.status}'",
            }
            for ex in executions.values()
            if not ex.succeeded
        ]

        if not completed:
            return SynthesisResult(
                text=None,
                synthesis_available=True,
                complete=False,
                reason="No task produced a usable result; nothing could be synthesized.",
                missing=missing,
            )

        try:
            engine = self._engine_factory()
        except Exception as exc:
            logger.warning("Synthesis runtime unavailable: %s", exc)
            return SynthesisResult(
                text=None,
                synthesis_available=False,
                complete=not missing,
                reason=f"Synthesis runtime unavailable: {exc}",
                missing=missing,
            )

        messages = [
            ChatMessage(role="system", content=SYNTHESIS_SYSTEM_PROMPT),
            ChatMessage(role="user", content=self._synthesis_prompt(request, plan, executions)),
        ]
        # The final answer is what the user reads. Give it the requested budget, never
        # less than the synthesizer's own floor, but keep the configured ceiling.
        max_tokens = max(1, min(4096, max(int(self._max_tokens), int(request.max_tokens))))
        if self._cap_tokens is not None:
            max_tokens = max(1, min(max_tokens, self._cap_tokens))
        synthesis_model = request.synthesis_model or request.model

        if on_delta is not None and hasattr(engine, "chat_stream"):
            text_parts: list[str] = []
            try:
                async for chunk in engine.chat_stream(
                    messages,
                    model=synthesis_model,
                    max_tokens=max_tokens,
                    temperature=0.3,
                ):
                    if chunk.delta:
                        text_parts.append(chunk.delta)
                        try:
                            await on_delta(chunk.delta)
                        except Exception:
                            logger.debug("Synthesis delta consumer failed", exc_info=True)
            except RaconRuntimeError as exc:
                return SynthesisResult(
                    text=None,
                    synthesis_available=False,
                    complete=not missing,
                    reason=f"Synthesis inference failed: {exc.code}: {exc.message}",
                    missing=missing,
                )
            except Exception as exc:
                logger.exception("Streaming synthesis raised unexpectedly")
                return SynthesisResult(
                    text=None,
                    synthesis_available=False,
                    complete=not missing,
                    reason=f"Synthesis inference failed: {type(exc).__name__}: {exc}",
                    missing=missing,
                )

            text = "".join(text_parts).strip()
            if not text:
                return SynthesisResult(
                    text=None,
                    synthesis_available=False,
                    complete=not missing,
                    reason="Synthesis runtime returned an empty answer.",
                    missing=missing,
                )
            return SynthesisResult(
                text=text,
                synthesis_available=True,
                complete=not missing,
                missing=missing,
                model=synthesis_model,
            )

        try:
            result = await engine.chat(
                messages,
                model=synthesis_model,
                max_tokens=max_tokens,
                temperature=0.3,
            )
        except RaconRuntimeError as exc:
            return SynthesisResult(
                text=None,
                synthesis_available=False,
                complete=not missing,
                reason=f"Synthesis inference failed: {exc.code}: {exc.message}",
                missing=missing,
            )
        except Exception as exc:
            logger.exception("Synthesis raised unexpectedly")
            return SynthesisResult(
                text=None,
                synthesis_available=False,
                complete=not missing,
                reason=f"Synthesis inference failed: {type(exc).__name__}: {exc}",
                missing=missing,
            )

        text = (result.text or "").strip()
        if not text:
            return SynthesisResult(
                text=None,
                synthesis_available=False,
                complete=not missing,
                reason="Synthesis runtime returned an empty answer.",
                missing=missing,
            )

        return SynthesisResult(
            text=text,
            synthesis_available=True,
            complete=not missing,
            missing=missing,
            model=getattr(result, "model", None),
        )

    def _synthesis_prompt(
        self,
        request: PlanRequest,
        plan: PlanSpec,
        executions: dict[str, TaskExecution],
    ) -> str:
        lines = ["Original user request:", request.prompt, "", "Task outcomes:"]
        budget = MAX_SYNTHESIS_INPUT_CHARS

        for task in plan.tasks:
            execution = executions.get(task.task_id)
            if execution is None:
                lines.append(f"[NOT EXECUTED] {task.title}: no result was produced.")
                continue
            if execution.succeeded:
                snippet = (execution.output or "")[:MAX_TASK_SNIPPET_CHARS]
                if len(snippet) > budget:
                    snippet = snippet[:budget]
                budget -= len(snippet)
                lines.append(f"[COMPLETED] {task.title}:")
                lines.append(snippet)
                lines.append("")
            else:
                lines.append(f"[FAILED] {task.title}: {execution.error or execution.status}")
                lines.append("")

        if plan.synthesis:
            lines.append("Planner synthesis instructions:")
            lines.append(plan.synthesis)

        lines.append("")
        lines.append(
            "Write the final answer now. Explicitly name anything that could not be completed."
        )
        return "\n".join(lines)


def _default_engine_factory():
    from racon.services.engine_service import get_engine

    return get_engine()
