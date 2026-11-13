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

Default to chat. Choose chat for greetings, suggestions, recommendations, movie/book lists,
explanations, definitions, single code snippets, short advice, and any request one model can
answer in one pass.

Choose task ONLY for a long-horizon build or implementation project that benefits from
multiple worker tasks: building an app/API/service, refactoring a codebase, multi-file
implementation, end-to-end deployment, or a long research report with several explicit
deliverables. When in doubt, answer chat.

Answer with exactly one lowercase word and nothing else: chat or task."""

#: Requests containing these are genuine build/implementation work.
_BUILD_PATTERNS = (
    r"\bbuild\b",
    r"\bimplement\b",
    r"\bdevelop\b",
    r"\brefactor\b",
    r"\bmigrate\b",
    r"\bdeploy\b",
    r"\bcodebase\b",
    r"\brepository\b",
    r"\brepo\b",
    r"\bmulti[- ]file\b",
    r"\bend[- ]to[- ]end\b",
    r"\bagentic\b",
    r"\blong[- ]horizon\b",
    r"\bbackend\b",
    r"\bfrontend\b",
    r"\btest suite\b",
    r"\bcreate (?:a|an) (?:app|application|project|service|api|tool|website)\b",
    r"\bfix (?:the )?bug\b",
    r"\bdebug\b",
    r"\bpipeline\b",
    r"\bworkflow\b",
    r"\bproduction[- ]ready\b",
    r"\bcomplete implementation\b",
)

#: Research/report work only qualifies as a task when it is also long.
_RESEARCH_PATTERNS = (
    r"\bresearch\b",
    r"\bcompare and (?:analyze|contrast)\b",
    r"\bcomprehensive report\b",
    r"\bmulti[- ]section\b",
    r"\bwhite ?paper\b",
    r"\bsurvey\b",
    r"\bdeep analysis\b",
    r"\bpros and cons\b",
    r"\btrade[- ]offs\b",
    r"\bstep[- ]by[- ]step plan\b",
    r"\bdeliverables\b",
    r"\bwrite a (?:detailed|full|comprehensive) report\b",
)


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
    """Flatten the last messages into a bounded transcript for the merge stage."""
    lines: list[str] = []
    for message in messages[-12:]:
        role = str(message.get("role") or "user").upper()
        text = _content_text(message.get("content")).strip()
        if text:
            lines.append(f"{role}: {text[:4000]}")
    joined = "\n\n".join(lines).strip()
    return joined[:limit]


def user_text(
    messages: list[dict[str, Any]],
    *,
    limit: int = 4000,
    max_messages: int = 3,
) -> str:
    """Return only the recent user turns.

    Client system prompts often describe a coding agent ("codebase", "implement",
    "build"). Routing on those makes every simple question look like a build task, so the
    gate and the router see only what the user actually asked.
    """
    parts: list[str] = []
    for message in reversed(messages):
        if str(message.get("role") or "") != "user":
            continue
        text = _content_text(message.get("content")).strip()
        if text:
            parts.append(text[:4000])
        if len(parts) >= max_messages:
            break
    return "\n\n".join(reversed(parts))[:limit]


def is_task_candidate(messages: list[dict[str, Any]]) -> tuple[bool, str]:
    """Return whether a request is a genuine long-horizon task candidate.

    This is a deliberate chat bias. The decision model occasionally labels a request like
    "suggest new 2026 thriller movies" as a task because it contains a plural deliverable;
    that turns a two-second answer into a minute of worker planning. Only explicit build
    work, or a long research request, may enter the worker path.
    """
    text = user_text(messages, limit=4000).lower()
    if not text.strip():
        return False, "empty request"
    for pattern in _BUILD_PATTERNS:
        if re.search(pattern, text):
            return True, f"build signal: {pattern}"
    for pattern in _RESEARCH_PATTERNS:
        if len(text) >= 400 and re.search(pattern, text):
            return True, f"long research signal: {pattern}"
    if len(text) >= 1500 and text.count("\n") >= 4:
        return True, "long multi-line request"
    return False, "simple chat"


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
    max_tokens: int = 16,
    timeout_s: float = 30.0,
    reasoning: Optional[dict[str, Any]] = None,
) -> AgentDecision:
    """Ask MiniMax for a one-word routing decision.

    Transport/authentication failures raise ``AgenticUnavailable`` so the gateway can use
    the deterministic path. A syntactically bad answer falls back to the local classifier.
    """
    prompt = user_text(messages, limit=4000, max_messages=3)
    candidate, candidate_reason = is_task_candidate(messages)

    request_messages = [
        {"role": "system", "content": DECISION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        # MiniMax is both the router and the responder now. A one-word answer with
        # reasoning disabled is cheap and settles chat vs task without a second model.
        result = await client.chat(
            request_messages,
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            reasoning=reasoning,
        )
    except TogetherError as exc:
        logger.warning("Routing model unavailable (%s): %s", exc.code, exc)
        if not candidate:
            return AgentDecision(
                mode="chat",
                confidence=0.7,
                reason=f"router unavailable; defaulting to chat ({exc.code})",
                source="heuristic",
            )
        raise AgenticUnavailable(f"Together AI routing failed: {exc}") from exc

    try:
        mode, confidence, reason = _parse_decision(result.get("text") or "")
    except (ValueError, json.JSONDecodeError) as exc:
        logger.info("Routing model returned an invalid decision (%s); using heuristics", exc)
        fallback = heuristic_decision(
            messages, tools, reason=f"Router output was invalid ({exc})"
        )
        if fallback.mode == "task" and not candidate:
            return AgentDecision(
                mode="chat",
                confidence=0.85,
                reason=f"forced chat: {candidate_reason}",
                source="gate",
            )
        return fallback

    if mode == "task" and not candidate:
        # The model saw plural nouns or "suggest" and over-called task. The gate keeps the
        # worker path for long-horizon build work only.
        decision = AgentDecision(
            mode="chat",
            confidence=0.9,
            reason=f"forced chat: {candidate_reason}",
            source="gate",
            model=model,
            usage=result.get("usage") or {},
        )
        logger.info("Routing decision: %s", decision.to_dict())
        return decision

    decision = AgentDecision(
        mode=mode,
        confidence=confidence,
        reason=(
            reason
            if reason and reason != "router label"
            else f"MiniMax router: {mode}"
        ),
        source="minimax-router",
        model=model,
        usage=result.get("usage") or {},
    )
    logger.info("Routing decision: %s", decision.to_dict())
    return decision
