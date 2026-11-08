# Task Execution Protocol

**Status:** implemented in `backend/racon/orchestration/` (2026-11-05)
**Applies to:** orchestration jobs, task DAGs, dispatch, validation, synthesis

---

## 1. Job lifecycle

```
queued → planning → running → synthesizing → completed
                                      ├──→ partial
                                      └──→ failed
   any non-terminal state ──────────────→ cancelled
   process exit ────────────────────────→ failed ("interrupted by process restart")
```

Every transition is persisted in `orchestration_jobs.status` and appended to
`orchestration_events`.

| Status | Meaning |
|---|---|
| `queued` | Accepted; runner not started yet. |
| `planning` | Planner is decomposing the request. |
| `running` | Tasks are being dispatched. |
| `synthesizing` | All dispatch finished; the synthesizer is producing the answer. |
| `completed` | Every task completed **and** the synthesizer returned an answer. |
| `partial` | Some tasks failed, or execution succeeded but synthesis was unavailable. A result may exist; gaps are named. |
| `failed` | No usable output exists. |
| `cancelled` | Cancelled by the user; outstanding tasks are marked cancelled. |

## 2. Task DAG

A plan is `PlanSpec` (`schemas.py`) with 1–12 tasks. Each task declares:

- `task_id` — slug `^[a-z][a-z0-9_-]{0,31}$`, unique in the plan
- `title`, `instruction` (self-contained, ≤ 6000 chars)
- `depends_on` — ids of tasks that must complete first
- `requirements` — capabilities, optional model id, minimum available RAM
- `max_tokens`, `timeout_s` (≤ 900), `retries` (≤ 3)

Validation (`dag.py`) rejects before any dispatch:

- empty plans, duplicate ids, unknown dependencies, self-dependencies
- cycles (depth-first cycle detection, reported as a path)
- limit violations enforced by the Pydantic schema

A task is **ready** only when every dependency has state `completed`. A task whose
dependency fails is never dispatched; it is marked `failed` with
`dependency did not complete: <ids>`.

## 3. Plan fallback

If the planner runtime is unavailable, returns invalid JSON twice, or produces an
invalid/cyclic plan, the job continues with a **single-task plan** that executes the
original request. This is recorded as `planner_mode = "fallback_single"` with a
`planner_note` explaining why. RACoN never fabricates a decomposition.

## 4. Dispatch contract

`TaskDispatcher.dispatch(task, context, dependency_outputs, worker_id) -> TaskExecution`

`dependency_outputs` contains only completed dependency outputs, bounded to 12,000 chars.
`TaskExecution` fields: `task_id`, `status` (`completed|failed|cancelled`), `worker_id`,
`output`, `error`, `duration_ms`, `runtime`, `truncated`, `usage`.

Validation applied to every result:

1. status must be `completed`
2. output must be a non-empty string
3. output is capped at 20,000 chars (`truncated=true` when cut)
4. a URL/JSON/schema mismatch is not inferred — the dispatcher reports the raw failure

A failed task is retried up to `retries` times. The selector excludes the worker that just
failed; if no other worker is eligible, the same worker is retried. Attempts are persisted.

## 5. Worker selection

Score = `capability·w1 + model·w2 + availability·w3 + reliability·w4 + network·w5 − load·w6`

Defaults: `0.30, 0.20, 0.20, 0.15, 0.10, 0.15`. Hard constraints: missing capability,
offline status, fully busy status, insufficient measured RAM, model explicitly not installed.
Unmeasured components use neutral priors and are reported in `WorkerScore.unmeasured`; they
are never presented as measurements.

Tie-break is deterministic by worker id. `SelectionWeights` is configurable per service.

## 6. Synthesis

Input: the original request, every task outcome (completed / failed / not executed), and
the planner's synthesis instructions. The synthesizer:

- uses only supplied results
- must name failed/missing work
- returns `text=None` when no task produced output
- returns `synthesis_available=false` when the runtime is unavailable, preserving the
  structured task outputs for the caller

Job status mapping:

- all tasks completed + synthesis text → `completed`
- some failure or degraded synthesis + synthesis text or completed tasks → `partial`
- no completed tasks and no text → `failed`

## 7. Persistence

| Table | Purpose |
|---|---|
| `orchestration_jobs` | one row per request: status, modes, plan JSON, result, counters, timings |
| `orchestration_tasks` | one row per DAG node: dependency JSON, state, worker, attempts, output, error |
| `orchestration_events` | append-only transitions for UI/SSE; never overwritten |

On process start every non-terminal job is marked `failed` with an explicit reason and a
`job_interrupted` event. No task is assumed resumable.

## 8. Cancellation

`POST /api/v1/jobs/{id}/cancel` cancels the runner task. The runner records
`job_cancelled`, marks every non-terminal task `cancelled`, and stops dispatch. In-flight
HTTP calls to remote workers are abandoned; the coordinator-side task may still report a
late result, which is stored on the task record but cannot change the cancelled job.

## 9. HTTP surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/jobs` | Accept and start a job (202) |
| GET | `/api/v1/jobs` | List jobs (limit/offset/status) |
| GET | `/api/v1/jobs/{id}` | Full job incl. plan and tasks |
| GET | `/api/v1/jobs/{id}/events` | SSE lifecycle stream (`after_id`) |
| POST | `/api/v1/jobs/{id}/cancel` | Cancel |

Example request:

```json
{
  "model_name": "gemma-3-1b-racon",
  "prompt": "Research CPU vs GPU inference, compare, and recommend.",
  "decompose": true,
  "execution_mode": "auto",
  "max_parallel_tasks": 3
}
```
