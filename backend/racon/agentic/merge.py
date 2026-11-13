"""Low-token merge and review of worker outputs."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from .decision import conversation_text
from .errors import TogetherError
from .prompts import merge_system_prompt
from .schemas import MergeResult, TaskOutcome

logger = logging.getLogger(__name__)

MAX_TASK_OUTPUT_CHARS = 4000
MAX_TOTAL_OUTPUT_CHARS = 24000
MAX_REQUEST_CHARS = 8000

DeltaCallback = Callable[[str], Awaitable[None]]


def _merge_prompt(messages: list[dict[str, Any]], outcomes: list[TaskOutcome]) -> str:
    parts = [
        "Original user request:",
        conversation_text(messages, limit=MAX_REQUEST_CHARS),
        "",
        "Worker task outputs:",
    ]
    budget = MAX_TOTAL_OUTPUT_CHARS
    for outcome in outcomes:
        if outcome.succeeded:
            snippet = (outcome.output or "")[: min(MAX_TASK_OUTPUT_CHARS, max(0, budget))]
            budget -= len(snippet)
            parts.append(
                f"[COMPLETED] {outcome.title} (worker {outcome.worker_id or 'unknown'}, "
                f"model {outcome.model or 'unknown'}):"
            )
            parts.append(snippet)
        else:
            parts.append(
                f"[FAILED] {outcome.title}: {outcome.error or 'no output'}"
            )
        parts.append("")
    parts.append(
        "Write a concise final answer now (under 250 words). "
        "Name anything that could not be completed."
    )
    return "\n".join(parts)


def deterministic_merge(
    messages: list[dict[str, Any]],
    outcomes: list[TaskOutcome],
    *,
    reason: str,
) -> MergeResult:
    """Honest fallback when the review model is unreachable."""
    completed = [item for item in outcomes if item.succeeded]
    if not completed:
        return MergeResult(
            text=None,
            degraded=True,
            reason=reason or "No worker produced a usable output.",
        )
    lines = [
        "The review model was unavailable, so these are the worker outputs without a "
        f"merged narrative ({reason}).",
        "",
    ]
    for item in completed:
        lines.append(f"## {item.title}")
        lines.append(item.output.strip())
        lines.append("")
    failed = [item for item in outcomes if not item.succeeded]
    if failed:
        lines.append("## Not completed")
        for item in failed:
            lines.append(f"- {item.title}: {item.error or 'no output'}")
    return MergeResult(
        text="\n".join(lines).strip(),
        degraded=True,
        reason=reason,
        usage={},
    )


async def merge_outputs(
    client,
    *,
    model: str,
    messages: list[dict[str, Any]],
    outcomes: list[TaskOutcome],
    max_tokens: int,
    on_delta: Optional[DeltaCallback] = None,
    reasoning: Optional[dict[str, Any]] = None,
) -> MergeResult:
    """Run one bounded merge/review generation over the worker outputs."""
    if not any(item.succeeded for item in outcomes):
        return MergeResult(
            text=None,
            reason="No task produced a usable result; nothing could be merged.",
        )

    request = [
        {"role": "system", "content": merge_system_prompt()},
        {"role": "user", "content": _merge_prompt(messages, outcomes)},
    ]
    budget = max(64, min(int(max_tokens), 4096))
    parts: list[str] = []
    usage: dict[str, Any] = {}
    try:
        async for event in client.chat_stream(
            request,
            model=model,
            max_tokens=budget,
            temperature=0.2,
            reasoning=reasoning,
        ):
            content = event.get("content")
            if content:
                parts.append(str(content))
                if on_delta is not None:
                    try:
                        await on_delta(str(content))
                    except Exception:
                        logger.debug("Merge delta consumer failed", exc_info=True)
            if event.get("usage"):
                usage = event["usage"]
    except TogetherError as exc:
        logger.warning("Merge model failed (%s): %s", exc.code, exc)
        return deterministic_merge(
            messages, outcomes, reason=f"Together AI merge failed: {exc}"
        )
    except Exception as exc:
        logger.exception("Merge generation raised unexpectedly")
        return deterministic_merge(
            messages, outcomes, reason=f"{type(exc).__name__}: {exc}"
        )

    text = "".join(parts).strip()
    if not text:
        # The streaming endpoint sometimes returns a 200 with no content (rate limiting
        # or an upstream truncation). Retry once as a normal completion, which has its own
        # backoff, before falling back to a deterministic merge.
        try:
            result = await client.chat(
                request,
                model=model,
                max_tokens=budget,
                temperature=0.2,
                reasoning=reasoning,
            )
            text = str(result.get("text") or "").strip()
            if not text and result.get("finish_reason") == "length" and budget < 3072:
                # This model spends part of the budget on reasoning_content. One larger
                # retry keeps the review bounded while still producing a final answer.
                larger = min(3072, max(budget * 2, 1536))
                logger.info(
                    "Merge hit the %d-token ceiling with no content; retrying at %d",
                    budget,
                    larger,
                )
                result = await client.chat(
                    request,
                    model=model,
                    max_tokens=larger,
                    temperature=0.2,
                    reasoning=reasoning,
                )
                text = str(result.get("text") or "").strip()
            if text:
                usage = result.get("usage") or usage
                if on_delta is not None:
                    try:
                        await on_delta(text)
                    except Exception:
                        logger.debug("Merge delta consumer failed", exc_info=True)
        except TogetherError as exc:
            logger.warning("Merge retry failed (%s): %s", exc.code, exc)
            return deterministic_merge(
                messages, outcomes, reason=f"Together AI merge failed: {exc}"
            )

    if not text:
        logger.warning(
            "Merge returned no content (finish=%s, usage=%s)",
            (result or {}).get("finish_reason"),
            (result or {}).get("usage"),
        )
        return deterministic_merge(
            messages, outcomes, reason="Together AI merge returned an empty answer"
        )
    return MergeResult(text=text, model=model, usage=usage)
