"""Agentic scheduler service: route, plan with tools, execute, merge.

The service exposes an async event stream so the gateway can surface progress to the client
without inventing content deltas. Only the merge output is sent as assistant content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, AsyncIterator, Optional

from backend.racon.core.config import get_settings

from .client import TogetherClient
from .decision import conversation_text, decide, heuristic_decision
from .errors import AgenticRunError, AgenticUnavailable, TogetherError
from .executor import AgentTaskRunner
from .merge import merge_outputs
from .prompts import PLANNING_TOOLS, planning_system_prompt
from .schemas import AgentDecision, AgentTask, TaskOutcome, merge_usage
from .selection import (
    build_environment,
    get_worker_telemetry,
    resolve_placement,
)

logger = logging.getLogger(__name__)

_MAX_PLAN_ROUNDS = 4
_TASK_ID_RE = re.compile(r"[^a-z0-9_-]+")
_VALID_ROLES = {"system", "user", "assistant", "tool"}
_TOOL_USE_GUIDANCE = """Tools are available, but use them only when the user explicitly
needs file access, code changes, or command execution. For general chat, suggestions,
recommendations, explanations, and knowledge questions, answer directly without calling a
tool. After a tool result, answer the user concisely; do not call another tool unless the
result shows that more work is required."""


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


def _agent_messages(messages: list[dict[str, Any]], *, limit: int = 24000) -> list[dict[str, Any]]:
    """Preserve roles and client tool-call state while bounding the prompt."""
    selected: list[dict[str, Any]] = []
    budget = limit
    for message in reversed(messages[-20:]):
        role = str(message.get("role") or "user")
        if role not in _VALID_ROLES:
            continue
        text = _content_text(message.get("content")).strip()
        item: dict[str, Any] = {"role": role, "content": text}
        if role == "assistant" and message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
        if role == "tool" and message.get("tool_call_id"):
            item["tool_call_id"] = message["tool_call_id"]
        cost = len(text)
        if item.get("tool_calls"):
            cost += len(json.dumps(item["tool_calls"], ensure_ascii=False))
        if not text and not item.get("tool_calls") and role != "tool":
            continue
        if cost > budget:
            text = text[: max(0, budget)]
            item["content"] = text
            cost = len(text)
        selected.append(item)
        budget -= cost
        if budget <= 0:
            break
    selected.reverse()
    return selected or [{"role": "user", "content": "Hello"}]


def _merge_tool_call_deltas(
    accumulator: dict[int, dict[str, Any]], deltas: list[dict[str, Any]]
) -> None:
    """Assemble OpenAI streaming tool-call fragments for non-stream responses."""
    for delta in deltas or []:
        if not isinstance(delta, dict):
            continue
        index = int(delta.get("index", 0) or 0)
        entry = accumulator.setdefault(
            index,
            {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        if delta.get("id"):
            entry["id"] = str(delta["id"])
        if delta.get("type"):
            entry["type"] = str(delta["type"])
        function = delta.get("function") or {}
        if isinstance(function, dict):
            if function.get("name"):
                entry["function"]["name"] += str(function["name"])
            if function.get("arguments"):
                entry["function"]["arguments"] += str(function["arguments"])


def _slug(value: str, index: int) -> str:
    slug = _TASK_ID_RE.sub("-", str(value or "").strip().lower()).strip("-_")
    if not slug:
        slug = f"task-{index + 1}"
    if not slug[0].isalpha():
        slug = f"task-{slug}"
    slug = slug[:32].rstrip("-_")
    return slug or f"task-{index + 1}"


def _normalise_dependencies(tasks: list[AgentTask]) -> list[str]:
    """Drop unknown, self, and cyclic dependencies so execution cannot deadlock."""
    known = {task.task_id for task in tasks}
    notes: list[str] = []
    for task in tasks:
        cleaned: list[str] = []
        for dep in task.depends_on:
            dep = str(dep).strip().lower()
            if dep == task.task_id or dep not in known:
                notes.append(f"dropped dependency {task.task_id} -> {dep or '?'}")
                continue
            if dep not in cleaned:
                cleaned.append(dep)
        task.depends_on = cleaned

    # Kahn's algorithm: anything left after sorting is in a cycle. Break its incoming
    # edges so the plan remains executable and say so.
    indegree = {task.task_id: len(task.depends_on) for task in tasks}
    children: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    for task in tasks:
        for dep in task.depends_on:
            children[dep].append(task.task_id)
    queue = [task_id for task_id, count in indegree.items() if count == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for child in children.get(current, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(tasks):
        for task in tasks:
            if indegree[task.task_id] > 0:
                notes.append(f"removed cycle edge(s) from {task.task_id}")
                task.depends_on = []
    return notes


class AgenticService:
    def __init__(
        self,
        *,
        settings=None,
        client: Optional[TogetherClient] = None,
        dispatcher=None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._dispatcher = dispatcher

    @property
    def settings(self):
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    @property
    def enabled(self) -> bool:
        try:
            return bool(self.settings.TOGETHER_ENABLED and self.settings.TOGETHER_API_KEY)
        except Exception:
            return False

    def _client_or_raise(self) -> TogetherClient:
        if self._client is None:
            self._client = TogetherClient(
                self.settings.TOGETHER_API_KEY,
                self.settings.TOGETHER_BASE_URL,
                timeout_s=self.settings.TOGETHER_TIMEOUT_S,
            )
        return self._client

    def _dispatcher_or_default(self):
        if self._dispatcher is None:
            from backend.racon.orchestration.executor import InProcessDispatcher

            self._dispatcher = InProcessDispatcher()
        return self._dispatcher

    def _reasoning(self) -> Optional[dict[str, str]]:
        effort = str(
            getattr(self.settings, "TOGETHER_REASONING_EFFORT", "none") or "none"
        ).strip().lower()
        if not effort or effort == "default":
            return None
        return {"effort": effort}

    # ------------------------------------------------------------------ routing

    async def prepare(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Any = None,
    ) -> AgentDecision:
        if not self.enabled:
            raise AgenticUnavailable("Together AI agentic layer is disabled")

        # A request carrying tool results is already inside a client-side tool loop.
        # MiniMax must continue that conversation; it must not be re-planned as a task.
        for message in messages:
            if str(message.get("role") or "") == "tool" or message.get("tool_calls"):
                return AgentDecision(
                    mode="chat",
                    confidence=0.9,
                    reason="continuing a client tool loop",
                    source="tool-loop",
                )

        try:
            return await asyncio.wait_for(
                decide(
                    self._client_or_raise(),
                    model=self.settings.TOGETHER_AGENT_MODEL,
                    messages=messages,
                    tools=None,
                    max_tokens=self.settings.TOGETHER_DECISION_MAX_TOKENS,
                    reasoning=self._reasoning(),
                ),
                timeout=min(30.0, self.settings.TOGETHER_TIMEOUT_S),
            )
        except asyncio.TimeoutError:
            logger.warning("Routing model timed out; using the deterministic classifier")
            return heuristic_decision(messages, reason="Routing model timed out")
        except AgenticUnavailable:
            raise
        except Exception as exc:
            logger.warning("Routing decision failed (%s); using the deterministic classifier", exc)
            return heuristic_decision(messages, reason=f"Routing failed: {exc}")

    # ------------------------------------------------------------------ public API

    async def stream(
        self,
        decision: AgentDecision,
        request: Any,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"event": "decision", "decision": decision.to_dict()}
        if decision.mode == "chat":
            async for event in self._chat_stream(request, messages):
                yield event
            return
        async for event in self._task_stream(decision, request, messages):
            yield event

    async def complete(
        self,
        decision: AgentDecision,
        request: Any,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        text_parts: list[str] = []
        final: Optional[dict[str, Any]] = None
        error: Optional[str] = None
        error_code = "agent_failed"
        retryable = False
        tool_call_parts: dict[int, dict[str, Any]] = {}
        async for event in self.stream(decision, request, messages):
            event_type = str(event.get("event") or "")
            if event_type in ("delta", "merge_delta"):
                text_parts.append(str(event.get("delta") or ""))
            elif event_type == "tool_calls":
                _merge_tool_call_deltas(tool_call_parts, event.get("tool_calls") or [])
            elif event_type == "done":
                final = event
            elif event_type == "error":
                error = str(event.get("message") or event.get("error") or "agent failed")
                error_code = str(event.get("code") or error_code)
                retryable = bool(event.get("retryable"))

        text = str((final or {}).get("text") or "".join(text_parts)).strip()
        tool_calls = [tool_call_parts[index] for index in sorted(tool_call_parts)]
        if not text and not tool_calls:
            raise AgenticRunError(
                error or "the agent produced no answer",
                code=error_code,
                retryable=retryable,
            )
        return {
            "text": text,
            "tool_calls": tool_calls,
            "finish_reason": (
                "tool_calls"
                if tool_calls
                else str((final or {}).get("finish_reason") or "stop")
            ),
            "usage": (final or {}).get("usage") or {},
            "decision": decision.to_dict(),
            "effective_mode": (final or {}).get("effective_mode", decision.mode),
            "tasks": (final or {}).get("tasks") or [],
            "merge_model": (final or {}).get("merge_model"),
            "degraded": bool((final or {}).get("degraded")),
        }

    # ------------------------------------------------------------------ chat

    async def _chat_stream(
        self,
        request: Any,
        messages: list[dict[str, Any]],
        *,
        note: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        if note:
            yield {"event": "progress", "stage": "chat", "message": note}
        else:
            yield {
                "event": "progress",
                "stage": "chat",
                "message": "The router selected a direct answer; the agent model is replying.",
            }

        usage: dict[str, Any] = {}
        parts: list[str] = []
        finish: Optional[str] = None
        stream_error: Optional[TogetherError] = None
        tool_calls_seen = False
        tools = getattr(request, "tools", None)
        tool_choice = getattr(request, "tool_choice", None)
        parallel_tool_calls = getattr(request, "parallel_tool_calls", None)
        agent_messages = _agent_messages(messages)
        if tools:
            # Append the guidance to the client's system prompt (or add one) so it wins
            # over a coding-agent preamble that tells the model to reach for tools.
            system_index = next(
                (
                    index
                    for index in range(len(agent_messages) - 1, -1, -1)
                    if agent_messages[index].get("role") == "system"
                ),
                None,
            )
            if system_index is None:
                agent_messages.insert(0, {"role": "system", "content": _TOOL_USE_GUIDANCE})
            else:
                existing = str(agent_messages[system_index].get("content") or "")
                agent_messages[system_index] = {
                    "role": "system",
                    "content": f"{existing}\n\n{_TOOL_USE_GUIDANCE}".strip(),
                }
        try:
            async for event in self._client_or_raise().chat_stream(
                agent_messages,
                model=self.settings.TOGETHER_AGENT_MODEL,
                max_tokens=min(
                    int(request.max_tokens), self.settings.TOGETHER_AGENT_MAX_TOKENS
                ),
                temperature=float(request.temperature),
                top_p=float(request.top_p),
                stop=request.stop,
                reasoning=self._reasoning(),
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
            ):
                if event.get("content"):
                    parts.append(str(event["content"]))
                    yield {"event": "delta", "delta": str(event["content"])}
                if event.get("tool_calls"):
                    tool_calls_seen = True
                    yield {"event": "tool_calls", "tool_calls": event["tool_calls"]}
                if event.get("usage"):
                    usage = event["usage"]
                if event.get("finish_reason"):
                    finish = str(event["finish_reason"])
        except TogetherError as exc:
            logger.warning("Agent chat stream failed (%s): %s", exc.code, exc)
            stream_error = exc

        text = "".join(parts).strip()
        if not text and not tool_calls_seen and (
            stream_error is None or stream_error.retryable
        ):
            # A stream that ends empty or is rate-limited gets one non-stream retry with
            # the client's own backoff. The retry is skipped if content or tool calls
            # already reached the caller, so a partial answer is never duplicated.
            try:
                result = await self._client_or_raise().chat(
                    agent_messages,
                    model=self.settings.TOGETHER_AGENT_MODEL,
                    max_tokens=min(
                        int(request.max_tokens),
                        self.settings.TOGETHER_AGENT_MAX_TOKENS,
                    ),
                    temperature=float(request.temperature),
                    top_p=float(request.top_p),
                    stop=request.stop,
                    reasoning=self._reasoning(),
                    tools=tools,
                    tool_choice=tool_choice,
                    parallel_tool_calls=parallel_tool_calls,
                )
                text = str(result.get("text") or "").strip()
                if text:
                    usage = result.get("usage") or usage
                    yield {"event": "delta", "delta": text}
                calls = result.get("tool_calls") or []
                if calls:
                    tool_calls_seen = True
                    prepared = []
                    for index, call in enumerate(calls):
                        if isinstance(call, dict):
                            item = dict(call)
                            item.setdefault("index", index)
                            prepared.append(item)
                    if prepared:
                        yield {"event": "tool_calls", "tool_calls": prepared}
            except TogetherError as exc:
                logger.warning("Agent chat retry failed (%s): %s", exc.code, exc)
                stream_error = stream_error or exc

        if not text and not tool_calls_seen:
            yield {
                "event": "error",
                "message": (
                    str(stream_error)
                    if stream_error
                    else "Together AI returned an empty chat answer"
                ),
                "code": stream_error.code if stream_error else "empty_answer",
                "retryable": bool(stream_error.retryable) if stream_error else True,
                "stage": "chat",
            }
            return
        yield {
            "event": "done",
            "text": text,
            "usage": usage,
            "effective_mode": "chat",
            "finish_reason": "tool_calls" if tool_calls_seen else (finish or "stop"),
            "tool_calls": tool_calls_seen,
            "tasks": [],
            "degraded": False,
        }

    # ------------------------------------------------------------------ task

    async def _task_stream(
        self,
        decision: AgentDecision,
        request: Any,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        environment = build_environment(self.settings.RACON_AGENTIC_WORKER_MODEL)
        if not environment.get("worker_count"):
            yield {
                "event": "visible_status",
                "message": (
                    "No worker nodes are online, so the agent model is answering directly."
                ),
            }
            yield {
                "event": "progress",
                "stage": "no_workers",
                "message": (
                    "The router found a task, but no worker nodes are online. "
                    "Answering directly with the agent model."
                ),
            }
            async for event in self._chat_stream(
                request,
                messages,
                note="No worker nodes are online; the agent model is answering directly.",
            ):
                yield event
            return

        yield {
            "event": "visible_status",
            "message": (
                f"I'm planning this with {environment['worker_count']} worker node(s). "
                "The merged answer will stream here when they finish."
            ),
        }
        yield {
            "event": "progress",
            "stage": "planning",
            "message": (
                f"Planning across {environment['worker_count']} live worker node(s). "
                "The agent is choosing tasks and the fastest suitable nodes."
            ),
        }

        tasks: list[AgentTask] = []
        plan_usage: dict[str, Any] = {}
        async for event in self._plan_events(messages, environment):
            if event.get("event") == "plan_ready":
                tasks = event.get("tasks") or []
                plan_usage = event.get("usage") or {}
            else:
                yield event

        notes = _normalise_dependencies(tasks)
        if notes:
            yield {
                "event": "progress",
                "stage": "plan_review",
                "message": "Adjusted the plan: " + "; ".join(notes[:4]),
            }

        executable: list[AgentTask] = []
        unplaced: dict[str, TaskOutcome] = {}
        used_workers: set[str] = set()
        for index, task in enumerate(tasks):
            candidate, model, placement_error = resolve_placement(
                requested_worker=task.worker_id,
                requested_model=task.model,
                preferred_model=self.settings.RACON_AGENTIC_WORKER_MODEL,
                exclude=used_workers,
            )
            if placement_error or candidate is None or not model:
                unplaced[task.task_id] = TaskOutcome(
                    task_id=task.task_id,
                    title=task.title,
                    status="failed",
                    worker_id=task.worker_id,
                    model=task.model,
                    error=placement_error or "no worker could be selected",
                )
                yield {
                    "event": "progress",
                    "stage": "task_unplaced",
                    "task_id": task.task_id,
                    "message": (
                        f"Task '{task.title}' has no suitable worker: "
                        f"{placement_error or 'no worker selected'}"
                    ),
                }
                continue
            task.worker_id = candidate.node_id
            task.model = model
            used_workers.add(candidate.node_id)
            executable.append(task)
            yield {
                "event": "task_queued",
                "task": task.to_dict(),
                "score": round(candidate.score, 3),
                "latency_ms": (
                    round(candidate.latency_ms, 1)
                    if candidate.latency_ms is not None
                    else None
                ),
            }
            if index == 0 or candidate.active_tasks == 0:
                yield {
                    "event": "progress",
                    "stage": "dispatch",
                    "task_id": task.task_id,
                    "message": (
                        f"Dispatching '{task.title}' to {candidate.node_id} "
                        f"with model {model}."
                    ),
                }

        outcomes: dict[str, TaskOutcome] = dict(unplaced)
        if executable:
            runner = AgentTaskRunner(
                self._dispatcher_or_default(),
                telemetry=get_worker_telemetry(),
                job_id=f"agent-{uuid.uuid4().hex[:12]}",
            )
            queue: asyncio.Queue = asyncio.Queue()

            async def on_event(item: dict[str, Any]) -> None:
                await queue.put(item)

            holder: dict[str, Any] = {}

            async def run_tasks() -> None:
                try:
                    holder["outcomes"] = await runner.execute(
                        executable,
                        max_parallel=self.settings.RACON_AGENTIC_MAX_PARALLEL,
                        on_event=on_event,
                    )
                except Exception as exc:  # never leave the consumer waiting
                    holder["error"] = exc
                finally:
                    await queue.put(None)

            task_runner = asyncio.create_task(run_tasks())
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    # Keep the HTTP stream alive while workers are executing.
                    yield {"event": "heartbeat"}
                    continue
                if item is None:
                    break
                yield item
            await task_runner
            if "error" in holder:
                raise holder["error"]
            outcomes.update(holder.get("outcomes") or {})

        ordered = [outcomes[task.task_id] for task in tasks if task.task_id in outcomes]
        completed = [item for item in ordered if item.succeeded]
        if not completed:
            yield {
                "event": "error",
                "message": (
                    "No worker task produced an output. "
                    + "; ".join(
                        f"{item.task_id}: {item.error}" for item in ordered[:3]
                    )
                ).strip(),
                "code": "no_task_output",
                "retryable": True,
                "stage": "execute",
            }
            return

        yield {
            "event": "progress",
            "stage": "merging",
            "message": (
                f"Collected {len(completed)}/{len(ordered)} worker output(s). "
                "The merge agent is reviewing and combining them with a low token budget."
            ),
        }

        merge_queue: asyncio.Queue = asyncio.Queue()
        merge_holder: dict[str, Any] = {}

        async def on_merge_delta(delta: str) -> None:
            await merge_queue.put(delta)

        async def run_merge() -> None:
            try:
                merge_holder["result"] = await merge_outputs(
                    self._client_or_raise(),
                    model=self.settings.TOGETHER_AGENT_MODEL,
                    messages=messages,
                    outcomes=ordered,
                    max_tokens=self.settings.RACON_AGENTIC_MERGE_MAX_TOKENS,
                    on_delta=on_merge_delta,
                    reasoning=self._reasoning(),
                )
            except Exception as exc:
                merge_holder["error"] = exc
            finally:
                await merge_queue.put(None)

        merge_task = asyncio.create_task(run_merge())
        while True:
            try:
                item = await asyncio.wait_for(merge_queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                yield {"event": "heartbeat"}
                continue
            if item is None:
                break
            yield {"event": "merge_delta", "delta": item}
        await merge_task

        merge = merge_holder.get("result")
        if merge is None or not merge.text:
            yield {
                "event": "error",
                "message": (
                    str(merge_holder.get("error"))
                    if merge_holder.get("error")
                    else "The merge agent produced no answer."
                ),
                "code": "merge_failed",
                "retryable": True,
                "stage": "merge",
            }
            return

        usage = merge_usage(
            decision.usage,
            plan_usage,
            merge.usage,
            *[outcome.usage for outcome in ordered],
        )
        yield {
            "event": "done",
            "text": merge.text,
            "usage": usage,
            "effective_mode": "task",
            "merge_model": merge.model or self.settings.TOGETHER_AGENT_MODEL,
            "degraded": merge.degraded,
            "merge_reason": merge.reason,
            "tasks": [item.to_dict(include_output=False) for item in ordered],
            "degraded_tasks": [item.task_id for item in ordered if not item.succeeded],
        }

    async def _plan_events(
        self,
        messages: list[dict[str, Any]],
        environment: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        conversation: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": planning_system_prompt(
                    environment, max_tasks=self.settings.RACON_AGENTIC_MAX_TASKS
                ),
            }
        ]
        conversation.extend(_agent_messages(messages))

        tasks: list[AgentTask] = []
        usage: dict[str, Any] = {}
        for round_index in range(_MAX_PLAN_ROUNDS):
            yield {
                "event": "progress",
                "stage": "planning",
                "message": f"Agent planning round {round_index + 1}.",
            }
            try:
                result = await self._client_or_raise().chat(
                    conversation,
                    model=self.settings.TOGETHER_AGENT_MODEL,
                    max_tokens=min(self.settings.TOGETHER_AGENT_MAX_TOKENS, 2048),
                    temperature=0.2,
                    tools=PLANNING_TOOLS,
                    tool_choice="auto",
                    reasoning=self._reasoning(),
                )
            except TogetherError as exc:
                logger.warning("Agent planning failed (%s): %s", exc.code, exc)
                yield {
                    "event": "progress",
                    "stage": "planning_failed",
                    "message": f"Agent planning failed ({exc.code}); using a single task.",
                }
                break

            usage = merge_usage(usage, result.get("usage"))
            content = str(result.get("text") or "").strip()
            if content:
                # Stream MiniMax's own plan/update to the user before workers run.
                yield {"event": "visible_status", "message": content[:800]}
                yield {
                    "event": "progress",
                    "stage": "agent",
                    "message": content[:800],
                }
            calls = result.get("tool_calls") or []
            if not calls:
                break

            conversation.append(
                {
                    "role": "assistant",
                    "content": result.get("text") or None,
                    "tool_calls": calls,
                }
            )
            queued_this_round = 0
            dispatch_error = False
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else {}
                name = str((function or {}).get("name") or "")
                raw_args = (function or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (TypeError, ValueError, json.JSONDecodeError):
                    args = {}
                if name == "list_workers":
                    tool_result: Any = environment
                elif name == "dispatch_task":
                    tool_result = self._queue_task(tasks, args, environment)
                    if tool_result.get("status") == "queued":
                        queued_this_round += 1
                        yield {
                            "event": "progress",
                            "stage": "task_planned",
                            "task_id": tool_result.get("task_id"),
                            "message": (
                                f"Queued task '{tool_result.get('title')}' "
                                f"({tool_result.get('task_id')})."
                            ),
                        }
                    elif tool_result.get("error"):
                        dispatch_error = True
                else:
                    tool_result = {"error": f"unknown tool '{name}'"}
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(
                            (call or {}).get("id") or f"call-{round_index}-{len(tasks)}"
                        ),
                        "content": json.dumps(tool_result, ensure_ascii=False)[:12000],
                    }
                )
            # The first successful dispatch round is the plan. A second model turn only
            # repeats the same summaries and adds latency plus rate-limit pressure; it is
            # still allowed when a dispatch failed or nothing was queued yet.
            if queued_this_round and not dispatch_error:
                break
            if len(tasks) >= self.settings.RACON_AGENTIC_MAX_TASKS:
                break

        if not tasks:
            prompt = conversation_text(messages, limit=6000)
            tasks.append(
                AgentTask(
                    task_id="task-1",
                    title="Answer the request",
                    instruction=prompt or "Answer the user request.",
                    max_tokens=min(self.settings.RACON_AGENTIC_MERGE_MAX_TOKENS * 2, 2048),
                )
            )
            yield {
                "event": "progress",
                "stage": "plan_fallback",
                "message": "The agent did not dispatch a plan; executing the request as one task.",
            }

        yield {"event": "plan_ready", "tasks": tasks, "usage": usage}

    def _queue_task(
        self,
        tasks: list[AgentTask],
        args: dict[str, Any],
        environment: dict[str, Any],
    ) -> dict[str, Any]:
        title = str(args.get("title") or "").strip()
        instruction = str(args.get("instruction") or "").strip()
        if not title or not instruction:
            return {"error": "dispatch_task requires a non-empty title and instruction"}
        if len(tasks) >= self.settings.RACON_AGENTIC_MAX_TASKS:
            return {
                "error": (
                    f"task limit reached ({self.settings.RACON_AGENTIC_MAX_TASKS}); "
                    "do not dispatch more tasks"
                )
            }

        worker_id = str(args.get("worker_id") or "").strip() or None
        model = str(args.get("model") or "").strip() or None
        known_workers = {
            str(item.get("worker_id")): set(item.get("models") or [])
            for item in environment.get("workers", [])
        }
        if worker_id and worker_id not in known_workers:
            return {"error": f"unknown or offline worker '{worker_id}'"}
        if model:
            advertised = (
                known_workers.get(worker_id, set()) if worker_id else set().union(*known_workers.values())
            )
            if model not in advertised:
                return {
                    "error": (
                        f"model '{model}' is not advertised by "
                        + (f"worker '{worker_id}'" if worker_id else "an online worker")
                    )
                }

        task_id = _slug(str(args.get("task_id") or title), len(tasks))
        if any(item.task_id == task_id for item in tasks):
            task_id = f"{task_id[:30]}-{len(tasks) + 1}"
        depends_on = [
            str(dep).strip().lower()
            for dep in (args.get("depends_on") or [])
            if str(dep).strip()
        ]
        try:
            max_tokens = int(args.get("max_tokens") or 512)
        except (TypeError, ValueError):
            max_tokens = 512
        task = AgentTask(
            task_id=task_id,
            title=title[:120],
            instruction=instruction[:6000],
            depends_on=depends_on,
            worker_id=worker_id,
            model=model,
            max_tokens=max(64, min(2048, max_tokens)),
        )
        tasks.append(task)
        return {
            "status": "queued",
            "task_id": task.task_id,
            "title": task.title,
            "worker_id": task.worker_id,
            "model": task.model,
            "depends_on": task.depends_on,
        }


_GLOBAL_SERVICE: Optional[AgenticService] = None


def get_agentic_service() -> AgenticService:
    global _GLOBAL_SERVICE
    if _GLOBAL_SERVICE is None:
        _GLOBAL_SERVICE = AgenticService()
    return _GLOBAL_SERVICE


def reset_agentic_service() -> None:
    global _GLOBAL_SERVICE
    _GLOBAL_SERVICE = None


async def close_agentic_service() -> None:
    """Compatibility shutdown hook; the client opens one connection per request."""
    reset_agentic_service()
