# RACoN Agentic Scheduler (Together AI)

The agentic scheduler is an optional layer in front of the OpenAI-compatible surface.
MiniMax is the router, chat responder, planner, and merger; the existing signed RACoN worker
transport handles execution.

```
client
  │  POST /v1/chat/completions   model=racon | racon-agent
  ▼
MiniMax router (one word, reasoning disabled)
  ├─ chat ──► MiniMax answer/tool calls ──► streamed answer
  └─ task ──► live worker snapshot
               └─ MiniMax plans with tool calls and streams a plan update
                    └─ tasks dispatched in parallel to the fastest suitable workers
                         └─ only final worker output returns to the coordinator
                              └─ MiniMax merges + reviews with a bounded token budget
                                   └─ streamed final answer
```

Task mode emits a visible status line and MiniMax's own plan update before workers run, then
`: keepalive` SSE comments while they execute. The `racon` sidecar carries structured
progress; the final answer streams as ordinary `delta.content`.

## Enable

The layer is disabled unless both `TOGETHER_ENABLED` and `TOGETHER_API_KEY` are set. Docker
Compose passes the values from the gitignored `.env` into `racn-coordinator`; the key is
never baked into the image or the web client.

```ini
TOGETHER_ENABLED=true
TOGETHER_API_KEY=tgp_...
TOGETHER_BASE_URL=https://api.together.xyz/v1
TOGETHER_AGENT_MODEL=MiniMaxAI/MiniMax-M3.1-Flash
# 'none' disables hidden reasoning; required for fast, reliable normal chat.
TOGETHER_REASONING_EFFORT=none
TOGETHER_TIMEOUT_S=90
# One-word router budget.
TOGETHER_DECISION_MAX_TOKENS=16
TOGETHER_AGENT_MAX_TOKENS=2048
RACON_AGENTIC_MAX_TASKS=6
RACON_AGENTIC_MAX_PARALLEL=4
RACON_AGENTIC_MERGE_MAX_TOKENS=2048
# Optional: pin a worker model. Empty chooses the fastest advertised model per node.
RACON_AGENTIC_WORKER_MODEL=
```

Then rebuild the coordinator:

```powershell
docker compose up -d --build racn-coordinator
docker compose logs -f racn-coordinator
```

## Which requests use it

| Request | Behaviour |
|---|---|
| `model: "racon"` | Uses the agentic layer when it is enabled. Falls back to the deterministic worker gateway if Together AI is disabled or unreachable. |
| `model: "racon-agent"` | Explicitly selects the layer. If the layer is disabled, it falls back to one worker. |
| `model: "racon-single"` / `racon-task` | Existing deterministic modes; unchanged. |
| Explicit worker model | Existing single-worker path; unchanged. |
| Request with `tools`, a `tool` role, or `tool_calls` | MiniMax continues the client's tool loop and streams tool calls back; it is not re-planned as a task. |

## Fast chat path

MiniMax is the router. It gets the user turns only and answers exactly one word, `chat` or
`task`; the gate then refuses a task unless the user text contains a build or long-horizon
signal. A client system prompt that mentions "codebase", "implement", or "build" cannot turn
a movie question into a worker plan.

`TOGETHER_REASONING_EFFORT=none` is important for latency: MiniMax otherwise spends the
entire output budget on hidden `reasoning_content` and returns no visible answer. With
reasoning disabled, the router call is cheap and normal chat starts streaming in about two
seconds.

Genuine build requests still enter task mode. Task mode emits a visible status line and
MiniMax's plan update immediately, then sends `: keepalive` SSE comments while workers run.

## Routing decision

MiniMax is asked for exactly one lowercase word, `chat` or `task`, with a 16-token budget.

- `chat` — greetings, suggestions, recommendations, explanations, definitions, single
  snippets, short advice, and client tool loops.
- `task` — only long-horizon build/implementation work, or a long research request with
  explicit deliverables.

If the router returns unusable output, the deterministic classifier in
`backend/racon/network/intent.py` decides, and the user-only gate forces chat when there is
no build signal. If Together AI is unreachable, the whole layer steps aside and the existing
gateway serves the request.

## Worker snapshot and selection

Only approved workers with a heartbeat inside 30 seconds, the `streaming_chat` feature, and
at least one advertised model are candidates. The agent sees this JSON:

```json
{
  "workers": [
    {
      "worker_id": "node-...",
      "models": ["Qwen2.5-0.5B-Instruct-Q4_K_M"],
      "active_tasks": 0,
      "cpu_pct": 4.0,
      "ram_available_mb": 6690,
      "gpu": "Intel UHD 630",
      "recent_latency_ms": 120.4,
      "tokens_per_second": 22.1,
      "score": 0.86,
      "unmeasured": []
    }
  ],
  "worker_count": 1,
  "preferred_model": null
}
```

The score is:

```
0.50 * speed + 0.25 * (1 - load) + 0.15 * ram_fit + 0.10 * model_fit
```

`recent_latency_ms` is the measured time from creating a signed task envelope to the worker
claiming it (EMA); if no claim has been measured yet it falls back to time-to-first-token.
Until a signal exists the component uses a neutral prior and is listed in `unmeasured`.
Workers that already received a task in the plan are avoided when another candidate exists,
so independent work spreads across nodes.

## Planning with tool calls

The agent model receives the live environment and two tools:

- `list_workers` — returns the environment snapshot.
- `dispatch_task` — queues `{task_id, title, instruction, worker_id?, model?, depends_on?,
  max_tokens?}`.

`dispatch_task` validates worker ids and model names against the live snapshot. Unknown,
self, and cyclic dependencies are removed so execution cannot deadlock. The number of tasks
is capped by `RACON_AGENTIC_MAX_TASKS`. If the model cannot call tools, the request runs as
one task on the fastest suitable worker.

## Execution

`AgentTaskRunner` executes the DAG in dependency order:

- Independent tasks in a wave run concurrently, capped by `RACON_AGENTIC_MAX_PARALLEL`.
- Each task uses the existing `InProcessDispatcher.open_stream` transport, so the worker
  still receives a signed `chat_completion` envelope and reports through `/tasks/progress`
  and `/tasks/report`.
- Token deltas are consumed by the coordinator and are **not** sent to the API client. Only
  the final output of each task enters the merge stage.
- A failed dependency fails its dependants with the dependency named; it is never invented
  or silently skipped.

## Merge and review

The merge agent receives the original request plus every completed output (truncated and
bounded). It is instructed to use only those outputs, remove duplication, resolve or report
contradictions, and state anything that could not be completed. The merge pass is capped by
`RACON_AGENTIC_MERGE_MAX_TOKENS` (default 512), so review does not spend a second full
generation budget.

If the merge model is unreachable, the coordinator returns a deterministic concatenation of
the worker outputs and marks `racon.degraded = true`; it does not fabricate an answer. If no
worker produced output, the request fails honestly with `no_task_output`.

## SSE events

Every frame is a normal `chat.completion.chunk`. Progress frames have an empty
`delta.content` and a `racon` sidecar:

```json
{"racon":{"mode":"agent","event":"decision","decision":{"mode":"task","confidence":0.9,"source":"together"}}}
{"racon":{"mode":"agent","event":"agent_progress","stage":"planning","message":"Planning across 2 live worker node(s)."}}
{"racon":{"mode":"agent","event":"task_queued","task":{"task_id":"analysis","title":"Analyze A","worker_id":"node-a","model":"..."},"score":0.91}}
{"racon":{"mode":"agent","event":"task_started","task_id":"analysis","worker_id":"node-a","model":"...","title":"Analyze A"}}
{"racon":{"mode":"agent","event":"task_done","task_id":"analysis","status":"completed","duration_ms":1840.2,"ttft_ms":410.5,"claim_ms":120.4,"output_chars":812}}
```

The merged answer streams as ordinary `delta.content`. The final chunk carries usage and:

```json
{"racon":{"mode":"agent","event":"done","effective_mode":"task","decision":{"mode":"task"},
 "tasks":[{"task_id":"analysis","status":"completed","worker_id":"node-a"}],
 "merge_model":"MiniMaxAI/MiniMax-M3.1-Flash","degraded":false,"degraded_tasks":[]}}
```

A non-streaming request returns the same metadata under `racon` on the normal
`chat.completion` object.

## Fallbacks

| Condition | Result |
|---|---|
| Layer disabled or no key | Deterministic worker gateway. |
| Decision model transport/auth failure | Deterministic worker gateway. |
| Decision output unparseable | Local deterministic classifier. |
| No online worker with a model | Direct chat answer from the agent model; `racon.effective_mode = "chat"`. |
| Agent planning fails | One task executes the whole request on the fastest worker. |
| Merge model fails | Deterministic merge, `racon.degraded = true`. |
| All worker tasks fail | Error `no_task_output`; no invented answer. |
| Client disconnects | The request task is cancelled with the HTTP request. |

## Limits and caveats

- At most `RACON_AGENTIC_MAX_TASKS` (default 6) tasks and `RACON_AGENTIC_MAX_PARALLEL`
  (default 4) concurrent worker streams.
- The measured "fast node" signal includes worker polling and claim overhead, not a raw
  bandwidth test. It is a placement heuristic, not a network benchmark.
- Together AI sees the routing prompt, the planning conversation, and the merge inputs.
  Do not send credentials, personal data, or confidential material through a public
  deployment.
- The layer is in-process with the coordinator because worker state and SSE fan-out are
  in-memory. It is not a separate container.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agentic.py -q
```

The suite injects a fake Together client and dispatcher; it never makes a live provider call.
