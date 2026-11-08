"""Planning agent: decompose a user request into a validated task DAG.

The planner treats model output as untrusted. It extracts JSON, validates it against the
bounded ``PlanSpec`` schema, validates the graph, and performs at most one repair attempt.
If no model runtime is available — or the model cannot produce a valid plan — the request
is executed honestly as a single task instead of being silently "decomposed" into
fabricated work.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Optional

from pydantic import ValidationError

from backend.racon.orchestration.dag import DagValidationError, validate_plan
from backend.racon.orchestration.schemas import (
    MAX_INSTRUCTION_CHARS,
    MAX_TASK_ID_CHARS,
    MAX_TASKS,
    PlanRequest,
    PlanSpec,
    single_task_plan,
)
from racon.runtime.types import ChatMessage, RaconRuntimeError

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = f"""You are the planning component of RACoN, a distributed AI system.

Break the user's request into at most {MAX_TASKS} small, independently executable tasks.
Small local models execute them, so prefer 2-4 tasks and keep every field short.

Return ONE compact JSON object and nothing else. No prose, no markdown fences.

Schema (only these fields are needed):
{{
  "objective": "one sentence",
  "tasks": [
    {{
      "task_id": "lowercase-slug",
      "title": "short label",
      "instruction": "self-contained instruction, at most 40 words",
      "depends_on": ["another-task-id"]
    }}
  ],
  "synthesis": "how to combine the results, one sentence"
}}

Rules:
1. A task_id may contain only lowercase letters, digits, "-" and "_", must start with a
   letter, and must be one you defined in this plan (for example "research-models").
2. Use depends_on ONLY when a task genuinely needs another task's output.
3. Independent tasks may run in parallel; prefer parallel structure when possible.
4. Every instruction must be understandable on its own. Do not reference "the previous task"
   except through depends_on.
5. Do not plan tasks that require internet access, shell access, or files the workers do not have.
6. If the request is simple, return a single task.
"""

REPAIR_PROMPT = """The plan you returned was rejected with this error:

{error}

Return ONLY a corrected JSON object that satisfies the schema and the dependency rules.
"""


class PlanParseError(ValueError):
    """Raised when model output cannot be interpreted as a plan."""


def extract_json_object(text: str) -> dict:
    """Extract the first JSON object from model output, tolerating code fences."""
    if not text or not text.strip():
        raise PlanParseError("planner returned an empty response")

    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise PlanParseError("planner response contained no JSON object")

    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlanParseError(f"planner JSON is invalid: {exc.msg} at position {exc.pos}") from exc

    if not isinstance(parsed, dict):
        raise PlanParseError("planner JSON root must be an object")
    return parsed


def _slugify_task_id(value: str, index: int) -> str:
    """Turn a model-written identifier into the bounded slug alphabet.

    Small planners frequently emit ``Task 1``, ``1``, ``task.1`` or a UUID. The identifier is
    an internal handle, so normalising it is safe; rejecting the whole plan is not.
    """
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(value).strip().lower())
    slug = re.sub(r"[-_]{2,}", "-", slug).strip("-_")
    if not slug:
        return f"task-{index + 1}"
    if not slug[0].isalpha():
        slug = f"task-{slug}"
    slug = slug[:MAX_TASK_ID_CHARS].rstrip("-_")
    return slug or f"task-{index + 1}"


def _break_dependency_cycles(dependencies: dict[str, list[str]]) -> list[str]:
    """Drop back edges from a cyclic dependency graph and report them.

    A cycle cannot be satisfied by any execution order, so every plan containing one would
    be discarded. Removing the edge that closes the cycle keeps the rest of the model's
    decomposition executable. The removal is recorded in ``planner_note`` and shown in the
    UI; unknown or missing dependencies are still rejected by the DAG validator.
    """
    removed: list[str] = []
    white, gray, black = 0, 1, 2
    colour = {task_id: white for task_id in dependencies}

    def visit(task_id: str) -> None:
        colour[task_id] = gray
        for dependency in list(dependencies.get(task_id, [])):
            if colour.get(dependency) == gray:
                dependencies[task_id].remove(dependency)
                removed.append(f"{task_id} -> {dependency}")
                continue
            if colour.get(dependency, black) == white:
                visit(dependency)
        colour[task_id] = black

    for task_id in list(dependencies):
        if colour.get(task_id, black) == white:
            visit(task_id)
    return removed


def normalise_plan_data(data: dict) -> tuple[dict, list[str]]:
    """Repair identifier and optional-field defects before strict validation.

    Every other rule (lengths, dependency validity, acyclicity) is still enforced by the
    schema and the DAG validator. Only representational mistakes are repaired here.
    """
    if not isinstance(data, dict):
        return data, []

    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return data, []

    id_map: dict[str, str] = {}
    used: set[str] = set()

    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue

        raw_id = task.get("task_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raw_id = f"task-{index + 1}"

        candidate = _slugify_task_id(raw_id, index)
        unique = candidate
        suffix = 2
        while unique in used:
            unique = f"{candidate[: MAX_TASK_ID_CHARS - 2]}-{suffix}"[:MAX_TASK_ID_CHARS]
            suffix += 1
        used.add(unique)

        id_map[str(raw_id)] = unique
        id_map[str(raw_id).strip().lower()] = unique
        task["task_id"] = unique

        if not task.get("title"):
            instruction = str(task.get("instruction") or "")
            task["title"] = (instruction.strip().splitlines()[0][:120] if instruction.strip() else unique)

        dependencies = task.get("depends_on")
        if dependencies is None:
            task["depends_on"] = []
        elif isinstance(dependencies, str):
            task["depends_on"] = [dependencies]
        elif isinstance(dependencies, list):
            task["depends_on"] = [d for d in dependencies if isinstance(d, (str, int))]

        if "requirements" in task and task["requirements"] is None:
            task["requirements"] = {}

    unknown_dependencies: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        dependencies = task.get("depends_on")
        if not isinstance(dependencies, list):
            continue
        resolved: list[str] = []
        for dependency in dependencies:
            key = str(dependency).strip()
            target = id_map.get(key) or id_map.get(key.lower())
            if target is None:
                # A planner can reference an id it never defined (small models do this
                # often). That edge cannot be satisfied by any execution order, so keeping
                # it rejects the whole plan and pays for a second generation. Drop it and
                # record the repair; the DAG validator stays strict for direct callers.
                unknown_dependencies.append(f"{task.get('task_id', '?')} -> {key}")
                continue
            if target not in resolved:
                resolved.append(target)
        task["depends_on"] = resolved

    dependency_map = {
        task["task_id"]: list(task["depends_on"])
        for task in tasks
        if isinstance(task, dict) and isinstance(task.get("depends_on"), list) and task.get("task_id")
    }
    removed = _break_dependency_cycles(dependency_map)
    if removed:
        for task in tasks:
            if isinstance(task, dict) and task.get("task_id") in dependency_map:
                task["depends_on"] = dependency_map[task["task_id"]]

    repairs = [f"Dropped undefined dependency {edge}." for edge in unknown_dependencies]
    repairs.extend(f"Removed impossible dependency cycle edge {edge}." for edge in removed)
    return data, repairs


def parse_plan(text: str) -> PlanSpec:
    """Parse and validate a plan from raw model text."""
    data, repairs = normalise_plan_data(extract_json_object(text))
    try:
        plan = PlanSpec.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ())) or "plan"
        raise PlanParseError(f"invalid plan field '{location}': {first.get('msg', 'validation failed')}") from exc

    validate_plan(plan)
    plan.planner_mode = "llm"
    plan.planner_note = " ".join(repairs)[:1000]
    return plan


class Planner:
    """LLM-backed planner with a validated, honest single-task fallback."""

    def __init__(
        self,
        engine_factory: Optional[Callable[[], object]] = None,
        *,
        max_plan_tokens: int = 768,
        plan_timeout_s: float = 120.0,
    ) -> None:
        self._engine_factory = engine_factory or _default_engine_factory
        self._max_plan_tokens = max_plan_tokens
        self._plan_timeout_s = plan_timeout_s

    async def _chat(self, engine, messages, request: PlanRequest, temperature: float):
        """One bounded planning call.

        The local runtime's HTTP client has no read timeout because generation length is
        unknown. A plan that never returns would leave the job in ``planning`` forever, so
        planning is explicitly bounded and falls back honestly when the budget expires.
        """
        return await asyncio.wait_for(
            engine.chat(
                messages,
                model=request.planner_model or request.model,
                max_tokens=self._max_plan_tokens,
                temperature=temperature,
            ),
            timeout=self._plan_timeout_s,
        )

    async def plan(self, request: PlanRequest) -> PlanSpec:
        if not request.decompose:
            return single_task_plan(
                request.prompt,
                reason="Decomposition was disabled for this request.",
                max_tokens=request.max_tokens,
            )

        try:
            engine = self._engine_factory()
        except Exception as exc:  # engine construction failure is a runtime condition
            logger.warning("Planner could not construct a runtime: %s", exc)
            return single_task_plan(
                request.prompt,
                reason=f"Planner runtime unavailable: {exc}",
                max_tokens=request.max_tokens,
            )

        messages = [
            ChatMessage(role="system", content=PLANNER_SYSTEM_PROMPT),
            ChatMessage(role="user", content=_request_text(request)),
        ]

        try:
            result = await self._chat(engine, messages, request, 0.2)
        except asyncio.TimeoutError:
            logger.warning("Planner inference exceeded %.0fs; falling back to a single task", self._plan_timeout_s)
            return single_task_plan(
                request.prompt,
                reason=(
                    f"Planner inference exceeded its {self._plan_timeout_s:.0f}s budget; "
                    "one task executes the whole request."
                ),
                max_tokens=request.max_tokens,
            )
        except RaconRuntimeError as exc:
            logger.warning("Planner inference failed (%s); falling back to a single task", exc.code)
            return single_task_plan(
                request.prompt,
                reason=f"Planner inference unavailable: {exc.message}",
                max_tokens=request.max_tokens,
            )
        except Exception as exc:
            logger.warning("Planner inference raised unexpectedly: %s", exc)
            return single_task_plan(
                request.prompt,
                reason=f"Planner inference failed: {exc}",
                max_tokens=request.max_tokens,
            )

        try:
            return parse_plan(result.text)
        except (PlanParseError, DagValidationError) as first_error:
            first_error_text = str(first_error)
            logger.info("Planner output rejected (%s); requesting one repair", first_error_text)

        repair_messages = messages + [
            ChatMessage(role="assistant", content=result.text[:MAX_INSTRUCTION_CHARS]),
            ChatMessage(role="user", content=REPAIR_PROMPT.format(error=first_error_text)),
        ]
        try:
            repaired = await self._chat(engine, repair_messages, request, 0.0)
            return parse_plan(repaired.text)
        except asyncio.TimeoutError:
            logger.warning("Planner repair exceeded %.0fs; using a single-task plan", self._plan_timeout_s)
            return single_task_plan(
                request.prompt,
                reason=(
                    f"Planner repair exceeded its {self._plan_timeout_s:.0f}s budget; "
                    "one task executes the whole request."
                ),
                max_tokens=request.max_tokens,
            )
        except (PlanParseError, DagValidationError, RaconRuntimeError) as exc:
            logger.warning("Planner repair failed (%s); using a single-task plan", exc)
            return single_task_plan(
                request.prompt,
                reason=f"Planner output could not be validated; one task executes the whole request ({exc}).",
                max_tokens=request.max_tokens,
            )


def _request_text(request: PlanRequest) -> str:
    if not request.context:
        return request.prompt
    context_lines = []
    for item in request.context[-6:]:
        role = str(item.get("role", "user"))[:16]
        content = str(item.get("content", ""))[:1000]
        context_lines.append(f"{role}: {content}")
    return "Conversation context:\n" + "\n".join(context_lines) + "\n\nRequest:\n" + request.prompt


def _default_engine_factory():
    from racon.services.engine_service import get_engine

    return get_engine()
