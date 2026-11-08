# RACoN Architecture

**Status:** reflects the code in this tree as of 2026-11-04.
This document supersedes `docs/ARCHITECTURE.md`, which describes the pre-cluster layout.

---

## 1. What RACoN is

A distributed AI inference network. Independent Windows and Linux computers (providers)
host models; consumers call an OpenAI-compatible API and are routed to a machine that can
serve the request. The consumer never addresses a worker directly.

Two execution stages exist, in this order of priority:

1. **Distributed hosting (default).** Each provider hosts a complete model. Routing picks a
   provider. This is the production path.
2. **Collective inference (optional, advanced).** `racon/cluster/**` splits layers across
   machines over llama.cpp RPC. It is empirically round-trip bound (~1.3 MiB/s effective
   link, see `docs/RPC_LINK_MEASUREMENTS.md`) and is never required for normal operation.

---

## 2. Component map

```
┌────────────────────────────── Desktop (Windows / Linux) ──────────────────────────────┐
│  React SPA (apps/desktop)                                                             │
│      ↕ HTTP + SSE (loopback, per-install token)                                       │
│  Local engine — racon/desktop.py hosts backend.racon.api in a thread, pywebview window │
│      ├── racon/services/engine_service.py   inference orchestration + metering         │
│      ├── racon/runtime/{llamacpp,ollama}    InferenceRuntime adapters                  │
│      ├── racon/models/{catalog,manager}     install / verify / estimate                │
│      └── racon/storage                      SQLite + versioned migrations              │
│  PySide6 shell — `--lab` window, `--autostart` tray, `--headless` agent                │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
                                        │ control channel (HTTPS, per-node token)
                                        ▼
┌──────────────────────────── Control plane — backend/racon ────────────────────────────┐
│  FastAPI, stateless, independently deployable (Docker / uvicorn)                      │
│  Agent routes · model registry · API-key gateway · routing scheduler · accounting     │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
                                        ▼
┌──────────────────────────── Supabase Postgres (shared) ───────────────────────────────┐
│  Schema owned by racon-console-web. RACoN never runs DDL.                             │
│  nodes · worker_* · models · model_versions · inference_* · scheduler_* · credit_*    │
└───────────────────────────────────────────────────────────────────────────────────────┘

Data plane (inference traffic) goes consumer → worker endpoint directly, not through the
control plane. Stage 2 adds llama.cpp RPC between workers on a private link.
```

---

## 3. Module ownership

| Concern | Canonical module | Notes |
|---|---|---|
| Settings | `racon/settings.py` | env → `.env` → defaults. No credential defaults in code. |
| Hardware | `racon/hardware.py` | Real probing; iGPU telemetry explicitly unavailable. |
| Runtime adapters | `racon/runtime/` | `base.py` contract; `llamacpp.py`, `ollama.py` implementations; `registry.py` factory. |
| Models | `racon/models/` | Catalog, HTTPS-only verified download, memory estimate from GGUF geometry. |
| Engine | `racon/services/engine_service.py` | The only place that binds runtime + storage; records successes *and* failures. |
| Local storage | `racon/storage/` | `database.py` + `migrations.py` (`PRAGMA user_version`). |
| Wire protocol | `protocols/` | Pydantic v2 with size bounds + Ed25519 canonical signing. |
| Provider agent | `racon/agent/worker.py` | Enroll, heartbeat, allow-listed task execution. |
| Coordination | `racon/coordinator/` | Worker API (`/enroll`, `/heartbeat`, `/tasks/claim`, `/tasks/report`) and admin API. |
| Stage-2 cluster | `racon/cluster/` | Layer planning, supervisor, llama.cpp RPC, gateway. Opt-in. |
| Control plane | `backend/racon/` | FastAPI server. May import `racon.*`; never the reverse. |
| Desktop UI | `apps/desktop/` | React 18 + Vite. |
| Desktop shell | `racon/desktop.py` | Single port, token bootstrap, pywebview with graceful fallbacks. |

**Rule:** a second implementation of an owned concern is a bug. `backend/racon/{hardware,
config,runtime,scheduler}` are legacy duplicates scheduled for collapse into shims.

---

## 4. Inference contract

```python
class InferenceRuntime(ABC):
    async def available(self) -> bool
    async def list_models(self) -> list[ModelInfo]
    async def load(self, model: str) -> None
    async def unload(self, model: str | None = None) -> None
    async def chat(self, messages, options) -> ChatResult
    def chat_stream(self, messages, options) -> AsyncIterator[ChatChunk]
    async def cancel(self) -> bool
    async def health(self) -> RuntimeHealth
```

Adding a runtime means implementing this ABC and registering it in `registry.py`. Schedulers
and HTTP routes never import a concrete adapter.

**Honesty invariant:** `ChatUsage.reported` is `False` when the backend supplied no token
counts. Metering must treat such records as unmeasured; no component may estimate tokens,
latency, throughput, GPU load, or worker counts.

---

## 5. API surfaces

| Surface | Auth | Purpose |
|---|---|---|
| `POST /v1/chat/completions`, `GET /v1/models` | per-install token | Local engine, OpenAI-compatible, SSE streaming |
| `/api/v1/engine/*` | per-install token | Status, load/unload, models, estimate, history, usage |
| `/api/v1/status`, `/nodes`, `/models`, `/scheduler`, `/jobs`, `/benchmarks`, `/logs`, `/settings` | per-install token | Desktop management API |
| `/admin/*` (coordinator) | loopback only | Operator actions; must 404 on the public listener |
| `/enroll`, `/heartbeat`, `/tasks/*` (coordinator) | Ed25519 signed envelopes + session token | Provider agent |
| `/v1/*` (cluster gateway) | `rk_` API keys, SHA-256 at rest, rate limits | Public consumer gateway |

Error shape is uniform:

```json
{"error": {"code": "runtime_unavailable", "message": "...", "details": {}}}
```

---

## 6. Data ownership

| Store | Owns | Never holds |
|---|---|---|
| Local SQLite | settings, installed-model metadata, inference history, engine events, worker config | consumer credentials, billing state |
| Supabase | provider identity, node registration, model registry, API keys, usage, credits, scheduler decisions, audit log | model weights |
| Control-plane memory | live heartbeats, in-flight routing state (rebuildable) | anything authoritative |

Secrets: `SUPABASE_SECRET_KEY` and `SUPABASE_ACCESS_TOKEN` exist only in the control plane's
environment. Providers authenticate with a per-node token issued at enrollment. The desktop
binary contains no usable credential.

---

## 7. Failure behaviour

| Condition | Behaviour |
|---|---|
| No runtime installed | `503 runtime_unavailable` with an install hint; UI shows an actionable banner |
| Model missing | `404 model_not_found` |
| Model not loaded | `409 model_not_loaded` |
| Runtime crashes mid-generation | Exit code translated (`SIGILL`, access violation); no orphan process |
| Control plane unreachable | Local inference keeps working; provider features show a disconnected state |
| Cancelled request | `inference_cancelled`; SSE closes with an error frame and `[DONE]` |
| Worker disappears | Supervisor drops it after two consecutive failures; task re-queued (M2) |

No retry loop is unbounded. No request is silently dropped: every attempt is recorded in
`inference_history` with `status` and `error_code`.

---

## 8. Scaling notes

SQLite is correct for a single desktop and a single control-plane instance. Before running
more than one control-plane replica, move rate limiting and idempotency to a shared store —
the schema already has `api_idempotency_keys` and `rate_limit_policies` for this.
Collective inference remains opt-in and must never be described as pooled RAM/VRAM.

---

## 9. Agentic orchestration layer (target architecture)

This section defines the component that turns one user request into many worker tasks. It
is server-side: the desktop client never performs trusted global scheduling.

```
Friend AI ──► POST /api/v1/jobs
                    │
                    ▼
            ┌──────────────────────────────────────────────┐
            │ backend/racon/orchestration                  │
            │  1 Planner        request → PlanSpec         │
            │  2 DAG validator  cycle/dependency checks    │
            │  3 WorkerSelector live telemetry → placement │
            │  4 Dispatcher     coordinator | local engine │
            │  5 Validator      identity/schema/limits     │
            │  6 Synthesizer    merge + honest gaps        │
            └───────────────┬──────────────────────────────┘
                            │ durable state
                            ▼
             SQLite: orchestration_jobs · orchestration_tasks
                     orchestration_events
                            │
              ┌─────────────┴──────────────┐
              ▼                            ▼
   Coordinator task plane          Local engine (fallback)
   (remote workers, Ed25519)       (same machine, explicit mode)
```

### 9.1 Contract between stages

| Stage | Input | Output | Failure behaviour |
|---|---|---|---|
| Planner | prompt, context, limits | `PlanSpec` (validated tasks + synthesis instructions) | Runtime unavailable or invalid JSON → single-task plan, recorded as `planner_mode="fallback_single"` |
| DAG validator | `PlanSpec` | topological order | Cycle/unknown dependency/limit violation → job fails before any dispatch |
| Selector | task requirements + live nodes | worker id or `None` | No feasible worker → task retried, then failed; never silently executed elsewhere unless auto-local is enabled |
| Dispatcher | task, dependency outputs | `TaskExecution` | Transport/runtime error → retry with reassignment |
| Validator | raw result | accepted result or rejection reason | Oversized/empty/schema-invalid output → task failed |
| Synthesizer | original request + all task outcomes | final text + inventory | Runtime failure → structured results returned, final text `None`, job `partial` |

### 9.2 Durable state machine

Job: `queued → planning → running → synthesizing → completed | partial | failed | cancelled`.

Task: `pending → ready → assigned → running → completed | failed | cancelled`.

Every transition writes an `orchestration_events` row. On process start, any job left in a
non-terminal state is marked `failed` with `error="interrupted by process restart"`. RACoN
never claims to resume a task whose worker-side execution cannot be proven.

### 9.3 Execution-mode honesty

`auto` prefers the coordinator when at least one approved worker with a compatible runtime
is reachable, otherwise uses the local engine. The mode actually used is persisted as
`execution_mode` and shown in the UI. The two are never conflated.

### 9.4 Module ownership additions

| Concern | Canonical module |
|---|---|
| Plan schema + limits | `backend/racon/orchestration/schemas.py` |
| DAG validation | `backend/racon/orchestration/dag.py` |
| Planning agent | `backend/racon/orchestration/planner.py` |
| Worker scoring | `backend/racon/orchestration/selector.py` |
| Dispatch (distributed/local) | `backend/racon/orchestration/executor.py` |
| Synthesis | `backend/racon/orchestration/synthesizer.py` |
| Job lifecycle | `backend/racon/orchestration/service.py` |
| Durable records | `backend/racon/storage/models.py` (`Job`, `JobTask`, `JobEvent`) |

`racon/scheduler/**` remains the placement scorer used for cluster previews. The
orchestration selector is a distinct, job-aware scorer; it consumes live node telemetry
and task requirements, and it must not be replaced by a second copy of the cluster scorer.

---

## 10. Unified server, PostgreSQL and the operator console (2026-11-07)

The deployable unit is now **one FastAPI process** (the Docker container) that serves every
server-side surface:

| Surface | Module |
|---|---|
| Desktop management API (`/api/v1/*`) | `backend/racon/api/routes_*.py` |
| OpenAI-compatible API (`/v1/*`) | `backend/racon/api/routes_engine.py` |
| Worker ingress (`/discover`, `/enroll`, `/heartbeat`, `/tasks/*`) | `backend/racon/api/routes_worker.py` |
| Operator API (`/api/v1/admin/*`) | `backend/racon/api/routes_admin.py` |
| Operator dashboard (`/admin`) | `backend/racon/admin_ui/index.html` |

Shared coordinator identity, node registry, invite store and task manager live in
`backend/racon/api/coordinator_state.py`. The private key is stored under `RACON_DATA_DIR`
(`/data` in the container), so restarts keep the same coordinator identity. Previously those
routes existed only in the standalone `racon.coordinator.server` process, which is why a
worker could not enroll against the Docker container.

### 10.1 Storage

`backend/racon/storage/db.py` selects the database from `DATABASE_URL` (or
`RACON_DATABASE_URL`). PostgreSQL is the server default and SQLite remains the local
desktop/test store. Both paths share the same SQLAlchemy models. `routing_events` is an
append-only audit table recording each worker/coordinator interaction and each scheduler
assignment, so the operator console can answer "which node sent what to which node".

### 10.2 Placement without a second listener

`backend/racon/orchestration/executor.py::InProcessDispatcher` creates signed task envelopes
directly in the in-process coordinator and polls their state. The worker still claims and
reports over HTTP. This replaces the old assumption that an admin API listens on
`127.0.0.1:8766`, which is not true in the container.

### 10.3 Worker execution

`racon/agent/worker.py` tries a local Ollama daemon first and then falls back to this
worker's RACoN engine (llama.cpp or Ollama). A worker with only a GGUF installed can
therefore execute distributed completion tasks. Failures remain explicit; no placeholder
text is generated.

### 10.4 Smart model router

`backend/racon/orchestration/model_router.py` is a deterministic, auditable router inspired
by Mesh-LLM's classifier. It never calls a model to route:

1. classify the request from its text and structure (code, reasoning, chat, tool_call,
   creative, info, image; quick/moderate/deep; tools and media flags);
2. infer each model's capabilities from its name and catalog metadata;
3. apply hard filters (tool use, reasoning, code, vision) and a size tier;
4. score the remaining candidates (capability fit, already loaded, complexity fit, RAM fit)
   and return the best with a full candidate list.

`model="auto"` is accepted by `POST /v1/chat/completions` and `POST /api/v1/jobs`;
`POST /api/v1/scheduler/route` previews the same decision. An explicit model is always
honoured. The decision is returned as `racon.router` and stored in the job plan.

### 10.5 Model-aware workers and diagnostics

`EnrollmentRequest` carries optional `models` and `features` lists. They are included in the
signed canonical payload only when non-empty, preserving 1.1 agent compatibility. The
selector receives the inventory as `WorkerTelemetry.models`, and the in-process dispatcher
marks a worker `unavailable` when its heartbeat is older than 30 seconds. A background
maintenance task also transitions stale registry entries to `OFFLINE` and writes a
`node_offline` routing event.

`GET /api/v1/admin/diagnostics` is the RACoN `doctor`: database connectivity, coordinator
identity, active runtime, Ollama, llama.cpp, model inventory and free disk, worker liveness,
routing-log freshness, Tailscale and token strength. The admin console renders the checks
and a smart-router preview under **Diagnostics**.

## 11. RACoN Network Gateway (2026-11-08)

The public product surface is one OpenAI-compatible endpoint authenticated by a RACoN
inference key (`rk_…`). It is implemented in `backend/racon/network/` on top of the existing
coordinator, worker protocol and orchestrator.

```
Client (any OpenAI harness)
  │  Authorization: Bearer rk_…
  ▼
auth middleware ──► /v1/chat/completions
  │
  ├─ model: racon-single / explicit / tools
  │     └─ target selection → InProcessDispatcher.open_stream → worker /tasks/progress
  │           └─ OpenAI chat.completion.chunk relay
  │
  └─ model: racon-task / auto-classified task
        └─ Planner DAG → task streams on workers → Synthesizer merge
              └─ task_delta frames + synthesis content
```

### 11.1 Modules

| Concern | Module |
|---|---|
| Key storage, hashing, scopes, rate limits | `backend/racon/network/api_keys.py`, `ratelimit.py` |
| Query/task classifier and aliases | `backend/racon/network/intent.py` |
| Worker/local model catalog | `backend/racon/network/catalog.py` |
| Delta fan-out with replay | `backend/racon/network/broker.py` |
| Target selection, single/task streams | `backend/racon/network/gateway.py` |
| Key admin API / UI | `routes_keys.py`, `admin_ui/index.html` |

### 11.2 Worker streaming

Workers are behind NAT and only connect outbound, so the coordinator cannot open a stream to
them. A `chat_completion` task tells a worker to stream its local runtime and push deltas to
`POST /tasks/progress`. The coordinator authenticates the session, rejects duplicate or
out-of-order sequence numbers, stores a bounded ring buffer, and publishes to the in-process
broker. The terminal `/tasks/report` result remains Ed25519-signed and is the source of truth.
A worker advertises `streaming_chat`; workers without it are excluded from streamed routing
so an old binary cannot silently swallow a task.

### 11.3 Selection

- Approved workers with a heartbeat ≤ 30 s and `streaming_chat`.
- Explicit model must be served by a worker (or the local fallback); otherwise 404.
- `racon` uses the deterministic model router over worker models; quick prompts prefer small
  models with a quality floor, deep prompts prefer larger ones.
- Workers already running a task receive a score penalty so a plan spreads across nodes.
- Before the first token, the gateway retries another worker; after the first token it reports
  the failure and closes the stream (OpenAI streaming semantics).

### 11.4 Task mode

The planner runs on the coordinator's local runtime (which may be Ollama or llama.cpp); task
workers may serve a different model. `PlanRequest.planner_model` / `synthesis_model` keep those
roles separate. Each task streams deltas through the job broker; the synthesizer merges the
completed outputs and streams the final answer. Failed tasks are named in `racon.missing` and
the job is `partial`. A single-task plan skips the second model pass where possible.

Security boundaries: inference keys cannot reach `/api/v1/admin/*` or settings; the admin
token is never compiled into a client; the coordinator remains the only public entry point.
