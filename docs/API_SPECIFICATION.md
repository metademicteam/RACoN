# API Specification

**Base:** loopback HTTP served by `backend/racon/api/app.py`
**Auth:** every `/api/v1/*` and `/v1/*` request requires a credential in
`X-RACON-TOKEN`, `X-API-Token`, `Authorization: Bearer <token>`, `x-api-key`, or `?token=`.
`GET /health` and the static SPA are unauthenticated.

- The administrator token (`AUTH_TOKEN`) and limited client token (`RACON_CLIENT_TOKEN`) keep
  the existing per-install behavior.
- RACoN inference keys (`rk_…`) are accepted on `/v1/*` only. They are scoped to inference,
  rate-limited per key, and never accepted by `/api/v1/admin/*`.
- Errors under `/v1/*` use the OpenAI envelope:
  `{"error":{"message","type","param","code"}}`.

Error shape:

```json
{"error": {"code": "runtime_unavailable", "message": "...", "details": {}}}
```

---

## 1. System

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness, version (open) |
| GET | `/api/v1/status` | hardware, engine, Laya, Ollama, uptime |
| GET | `/api/v1/logs?limit=&level=` | in-memory logs + audit events (redacted) |
| GET | `/api/v1/settings` | persisted key/value settings |
| PUT | `/api/v1/settings` | `{key, value}` upsert |

## 2. Nodes / workers

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/nodes` | registered nodes with measured RAM/device |
| GET | `/api/v1/nodes/{id}` | one node |
| POST | `/api/v1/nodes/{id}/start` | mark available |
| POST | `/api/v1/nodes/{id}/stop` | mark unavailable |

## 3. Models

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/models` | runtime-reported models (Ollama) |
| GET | `/api/v1/models/catalog` | catalog + installed flags + pull status |
| POST | `/api/v1/models/refresh` | rescan |
| POST | `/api/v1/models/pull` | `{model_name}` — verified GGUF download (HF/operator) or Ollama pull |
| GET | `/api/v1/models/pull/status?model_name=` | pull/download progress |
| POST | `/api/v1/models/infer` | `{model_name, prompt}` via the engine |
| GET | `/api/v1/models/hf/files?repo_id=&revision=` | list GGUF artifacts in a Hub repository |
| POST | `/api/v1/models/hf/install` | `{repo_id, filename, revision?, model_id?}` verified install |
| GET | `/api/v1/models/hf/whoami` | token configured? (never returns the token) |
| GET | `/api/v1/models/recommendations` | measured hardware → ranked models, incl. HF entries |

## 4. Engine and network gateway

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/models` | OpenAI-compatible list: `racon` aliases, worker models, local models |
| GET | `/v1/models/{id}` | one model |
| POST | `/v1/chat/completions` | OpenAI-compatible; one worker or planner decomposition, SSE when `stream=true` |
| GET | `/api/v1/engine/status` | runtime health, loaded model, installed |
| POST | `/api/v1/engine/load` | `{model, context_size?}` |
| POST | `/api/v1/engine/unload` | unload |
| POST | `/api/v1/engine/cancel` | cancel active generation |
| GET | `/api/v1/engine/models` | installed + catalog + models dir + free disk |
| POST | `/api/v1/engine/models/import` | `{path, model_id?, copy?}` local GGUF import |
| GET | `/api/v1/engine/models/{id}/estimate` | real GGUF memory estimate |
| DELETE | `/api/v1/engine/models/{id}` | delete managed model |
| GET | `/api/v1/engine/history` | metered inference history |
| GET | `/api/v1/engine/events` | engine events |
| GET | `/api/v1/engine/usage` | token/request aggregates |

### Network gateway behavior

`model` controls routing:

| Model | Behavior |
|---|---|
| `racon` | Auto: quick query → one worker; complex task → planner decomposition across workers, then synthesis |
| `racon-single` | Force one worker |
| `racon-task` | Force decomposition |
| `racon-single:<model>` | Force one worker and pin the model |
| `racon-task:<model>` | Force decomposition and pin the worker model |
| any listed model | Route directly to a worker/local runtime that serves it |

Tool requests (`tools` or a tool-role message) always stay on one worker. The single path
streams worker deltas as `chat.completion.chunk`. The task path streams `task_delta` frames as
standard chunks with an empty `delta.content` plus a `racon` block (`event`, `task_id`, `title`,
`text`), then streams the synthesized answer as `delta.content`, and ends with `racon.status`
and `racon.missing`.

### Inference keys

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/admin/keys` | `{name, rate_limit_per_min?, scopes?}` → plaintext `key` once |
| GET | `/api/v1/admin/keys` | list prefixes, scopes, limits, last use, revocation |
| DELETE | `/api/v1/admin/keys/{id}` | revoke |

Workers report streamed deltas at `POST /tasks/progress` (session-token authenticated; the
terminal `/tasks/report` result remains Ed25519-signed).
| GET | `/api/v1/engine/status` | runtime health, loaded model, installed |
| POST | `/api/v1/engine/load` | `{model, context_size?}` |
| POST | `/api/v1/engine/unload` | unload |
| POST | `/api/v1/engine/cancel` | cancel active generation |
| GET | `/api/v1/engine/models` | installed + catalog + models dir + free disk |
| POST | `/api/v1/engine/models/import` | `{path, model_id?, copy?}` local GGUF import |
| GET | `/api/v1/engine/models/{id}/estimate` | real GGUF memory estimate |
| DELETE | `/api/v1/engine/models/{id}` | delete managed model |
| GET | `/api/v1/engine/history` | metered inference history |
| GET | `/api/v1/engine/events` | engine events |
| GET | `/api/v1/engine/usage` | token/request aggregates |

## 5. Orchestration jobs

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/jobs` | submit; returns **202** with the queued job |
| GET | `/api/v1/jobs?limit=&offset=&status=` | list jobs |
| GET | `/api/v1/jobs/{id}` | job + plan + per-task state |
| GET | `/api/v1/jobs/{id}/events?after_id=` | SSE lifecycle events |
| POST | `/api/v1/jobs/{id}/cancel` | cancel |

Request:

```json
{
  "name": "optional label",
  "model_name": "gemma-3-1b-racon",
  "prompt": "…",
  "max_tokens": 512,
  "decompose": true,
  "execution_mode": "auto",
  "max_parallel_tasks": 3
}
```

`execution_mode`: `auto` (prefer approved workers, otherwise local), `local`, `distributed`
(fails when no worker exists — never silently local). Legacy fields `objective`, `mode`,
`preferred_worker_id`, `model_partition_mb` are accepted and ignored.

Job response (abridged):

```json
{
  "id": "job-…",
  "status": "running",
  "requested_mode": "auto",
  "execution_mode": "hybrid",
  "planner_mode": "llm",
  "plan": {"objective": "…", "tasks": [...], "synthesis": "…"},
  "result": null,
  "error": null,
  "tasks_total": 3,
  "tasks_completed": 1,
  "tasks_failed": 0,
  "tasks": [
    {
      "task_id": "task-1",
      "state": "completed",
      "worker_id": "node-…",
      "depends_on": [],
      "attempts": 1,
      "output": "…",
      "error": null,
      "duration_ms": 1200.0
    }
  ]
}
```

SSE frames are `data: {event}` where `event` has `id`, `job_id`, `task_id`, `timestamp`,
`event_type`, `level`, `message`, `detail`. The stream ends with
`{"event_type":"stream_end"}` when the job reaches a terminal state.

## 6. Scheduler (cluster preview)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/scheduler/config` | default objective/mode |
| PUT | `/api/v1/scheduler/config` | update |
| POST | `/api/v1/scheduler/preview` | score scenario candidates |

## 7. Benchmarks

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/benchmarks` | persisted scheduler runs |
| POST | `/api/v1/benchmarks` | run a named scenario |

## 8. Cloud registry (optional, read-only)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/cloud/status` | configured/reachable, host, per-table counts; never returns keys |
| GET | `/api/v1/cloud/models` | metadata from the shared `models` registry + `model_endpoints` |

Supabase is optional. When `SUPABASE_URL` and a key are absent, both routes report
`configured: false` and the desktop remains fully local. Registry entries have
`installable: false` until a verified artifact exists in `model_versions`.

## 9. Provider/control-plane surfaces (separate processes)

| Surface | Port | Auth |
|---|---|---|
| Coordinator worker API `/enroll`, `/heartbeat`, `/tasks/claim`, `/tasks/report` | 8765 | invite code, Ed25519, session token |
| Coordinator admin `/admin/*` | 8766, loopback only | none — must never be published |
| Cluster gateway `/v1/*` | configured | `rk_` API keys, SHA-256 at rest, rate limits |

## 10. Limits

| Limit | Value |
|---|---|
| Job prompt | 6,000 chars |
| Tasks per plan | 12 |
| Task instruction | 6,000 chars |
| Task output | 20,000 chars (truncated) |
| Dependency context | 12,000 chars |
| Job execution budget | 1,800 s (service default) |
| Task timeout | ≤ 900 s |
| Parallel tasks per job | 1–12 |
| Model import path | 4,096 chars |
