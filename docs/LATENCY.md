# Latency: measurements and budgets

This document records what was measured on a real host, what was changed, and how to
reproduce the numbers. Values are wall-clock measurements from the server and from the
engine's `inference_history`; nothing here is an estimate.

## Test host and model

| Item | Value |
|---|---|
| CPU | Intel Core i3-10100 (4 cores / 8 threads) |
| RAM | 16 GB |
| GPU | none (CPU inference) |
| Runtime | llama.cpp (`llama-server` from WinGet), 7 threads, context 4096 |
| Model | Qwen2.5-0.5B-Instruct-Q4_K_M (~0.37 GB) |
| Request | `max_tokens=2048`, `decompose=true`, `execution_mode=local` |

Raw generation speed measured on this host: **18–33 tokens/second**, depending on prompt
and cache state. At that rate every 1,000 output tokens costs 30–55 seconds.

## Baseline (before)

A single agentic job took **187.1 seconds**:

| Phase | Duration | Generated |
|---|---|---|
| Planning (one LLM call) | 12.0 s | 144 tokens |
| Task execution (one task) | 69.3 s | 2,048 tokens |
| Synthesis (one LLM call) | **102.4 s** | **2,048 tokens** |
| Overhead / scheduling | 3.4 s | — |

The plan contained exactly **one task**. The synthesizer then re-generated the whole answer
from scratch at the full requested budget, doubling the work for no new information.

Additional per-request overhead: `ModelManager.find_installed()` parsed GGUF headers and
walked every configured model root on **every** inference call, synchronously on the event
loop. Measured cost: **180–220 ms per scan** for one model, repeated several times per
request, blocking all concurrent requests while it ran.

## Changes

1. **Single-task passthrough** (`backend/racon/orchestration/service.py`)
   When a validated plan contains one task and it succeeded, its output is the final answer.
   The synthesizer is not called. The answer is still published on the synthesis stream so
   SSE clients receive it. Enabled by the product factory through
   `OrchestrationConfig(reuse_single_task_output=True)`; direct constructions keep the old
   two-stage behavior.

2. **Synthesis ceiling** (`synthesizer.py`, `service.py`)
   The merge step is capped at `RACON_SYNTHESIS_MAX_TOKENS` (default **1024**) instead of
   always taking the request's full budget. The planner is capped at
   `RACON_PLANNER_MAX_TOKENS` (default **512**).

3. **Planner dependency repair** (`planner.py`)
   Small models often reference a task id they never defined (for example a single task
   depending on `research-trade-off-task-id`). The parser now drops that impossible edge and
   records it in `planner_note` instead of rejecting the plan and paying for a second
   repair generation. `validate_plan()` remains strict for programmatically built plans.

4. **Model inventory cache** (`racon/models/manager.py`)
   GGUF discovery is cached for 2 seconds and invalidated on install/import/delete.
   `/v1/models` went from **219 ms cold** to **3 ms** on repeated calls.

5. **Non-blocking model resolution** (`racon/services/engine_service.py`)
   Runtime selection and `find_installed()` run via `asyncio.to_thread` instead of blocking
   the event loop.

6. **Serialized model loading** (`racon/services/engine_service.py`)
   An `asyncio.Lock` prevents two concurrent first requests from starting two
   `llama-server` processes for the same model.

7. **Optional startup preload** (`backend/racon/api/app.py`)
   Set `RACON_PRELOAD_MODEL=<model id>` to load the model during application startup, so
   the first request does not pay the cold-start cost.

8. **Bounded distributed retry tail** (`backend/racon/network/gateway.py`)
   The gateway now has a total `RACON_TARGET_BUDGET` (default **240 s**) across all worker
   attempts. Previously three silent workers cost 3 × 180 s before an error was returned.

## After

Same job, same host, same model:

| Run | Wall clock | Planning | Task | Synthesis |
|---|---|---|---|---|
| Cold | **13.1 s** | 10.5 s | 2.6 s | skipped |
| Warm | **5.9 s** | 4.1 s | 1.8 s | skipped |

The model chose shorter answers in these runs (123 + 18 tokens and 126 + 59 tokens), so the
wall-clock improvement is larger than the structural change alone. The deterministic saving
is the removal of the 102.4 s synthesis generation and the second planner attempt.

A no-decompose job (`decompose=false`, `max_tokens=512`) took **20.3 s** with a single
309-token generation — that is the fastest agentic path: one task, no planner, no synthesis.

Cache effect on `/v1/models` (same process):

```
call 0: 219.1 ms   (cold scan)
call 1:   3.0 ms
call 2:  15.8 ms
call 3:   2.7 ms
```

## Reproducing

Start the API (any port), then point the benchmark at it:

```powershell
python scripts/bench_latency.py `
  --base-url http://127.0.0.1:8765 `
  --token $env:RACON_AUTH_TOKEN `
  --model Qwen2.5-0.5B-Instruct-Q4_K_M `
  --mode job --max-tokens 2048 --decompose --execution-mode local
```

`--mode chat` measures the OpenAI-compatible streaming path (TTFT and total). `--json`
prints machine-readable results. The script only reports values the server returned plus
client wall clock.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `RACON_PRELOAD_MODEL` | unset | Model id loaded at startup |
| `RACON_PLANNER_MAX_TOKENS` | 512 | Planner output ceiling |
| `RACON_SYNTHESIS_MAX_TOKENS` | 1024 | Final-answer ceiling for multi-task plans |
| `RACON_FIRST_TOKEN_TIMEOUT` | 180 | Per-worker first-token wait (seconds) |
| `RACON_TARGET_BUDGET` | 240 | Total wait across all worker attempts (seconds) |
| `RACON_CONTEXT_SIZE` | 4096 | llama.cpp context |
| `RACON_THREADS` | CPU count − 1 | llama.cpp thread count |

## Remaining limits

- Multi-task plans still generate every task plus a merge. Reducing `max_tokens`, using
  `decompose=false`, or using a smaller model are the remaining levers.
- CPU inference is linear in generated tokens. A 2,000-token answer on this host cannot be
  produced in under roughly 60–110 seconds regardless of scheduling.
- Streaming reduces perceived latency (TTFT), not total generation time. The desktop chat
  path already streams; the job path streams synthesis/task deltas.
