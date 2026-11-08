# RACoN Development Roadmap

**Updated:** 2026-11-05
**Baseline:** 250 passed / 1 known environmental failure (`test_distributed_layer_inference_e2e`)
**Current milestone:** O1 — agentic orchestration core

Status legend: **DONE** evidence exists and is executed · **PARTIAL** some real code, gaps
listed · **MISSING** no implementation.

---

## Phase 1 — Repository audit and architecture (DONE)

Deliverables:

- `docs/PHASE1_AUDIT.md` — evidence-based inventory, defects, duplication, missing capability.
- `ARCHITECTURE.md` §9 — target orchestration architecture and ownership.
- This roadmap.

Acceptance: every claim in the audit names a file; baseline test run recorded.

---

## Phase 2 — Desktop foundation (PARTIAL)

| Item | Status | Notes |
|---|---|---|
| Cross-platform shell | DONE | `racon/desktop.py`; pywebview → Chromium app → browser. |
| Per-install token | DONE | `backend/racon/core/config.py`. |
| Local engine in-process | DONE | uvicorn thread, single port. |
| Model download/verify | DONE | `racon/models/manager.py`. |
| Hardware detection | DONE | `racon/hardware.py`. |
| Model recommendation | DONE | `GET /api/v1/models/recommendations`; measured RAM, real GGUF estimates, `fits: null` when unmeasurable. |
| Friend AI persona | DONE | Playground is now Friend AI: local persona plus a network mode that submits and renders real jobs. |
| UI crash fixes | DONE | Logs and Benchmarks tabs render the real payloads. |
| Onboarding / first-run | DONE | Overview setup card recommends a verified model from measured hardware and shows download progress. |

Next actions: Phase 2 gaps are closed. Remaining work is in Phase 3 (worker progress and
durable coordinator state), Phase 6 (reconnect reconciliation) and Phase 8 (multi-machine
demonstration).

---

## Phase 3 — Worker runtime (PARTIAL)

| Item | Status | Notes |
|---|---|---|
| Enrollment + Ed25519 identity | DONE | `racon/agent/worker.py`. |
| Heartbeat | PARTIAL | Blocking loop stalls during generation; `active_tasks` always empty. |
| Task claim | PARTIAL | No lease, no state transition, duplicate delivery possible. |
| Task execution | DONE | Four allow-listed handlers; unknown types fail. |
| Progress reporting | MISSING | Protocol has no progress field. |
| Result signing | DONE | Worker signs; coordinator verifies. |
| Coordinator→worker signature verification | MISSING | Imports present, never called. |
| Durable worker/task state | MISSING | In-memory dicts. |

Acceptance for completion: one task is delivered exactly once, survives a coordinator
restart, reports progress, and is cancellable.

---

## Phase 4 — Agentic scheduler (IN PROGRESS — O1)

| Item | Status |
|---|---|
| Plan schema (strict bounds) | implementing |
| DAG validation + topological order | implementing |
| LLM planner with validated JSON + single-task fallback | implementing |
| Weighted worker selection (capability, model, availability, reliability, network, load) | implementing |
| Durable job/task/event persistence | implementing |
| Parallel + dependency-ordered execution | implementing |
| Retries, reassignment, cancellation | implementing |
| Distributed dispatcher (coordinator task plane) | implementing |
| Local fallback dispatcher | implementing |
| Stale-job recovery on restart | implementing |

Acceptance:

1. A submitted job produces a persisted DAG of tasks.
2. Independent tasks run concurrently; dependent tasks wait.
3. A failed task is retried and reassigned; the job reports partial success honestly.
4. Cancelling a job stops dispatch and marks tasks cancelled.
5. Restarting the process marks interrupted jobs failed with an explicit reason; no job is
   silently resumed.
6. Orchestration unit tests pass without a real model runtime (fake dispatcher).

---

## Phase 5 — Synthesis agent (IN PROGRESS — O1)

| Item | Status |
|---|---|
| Result schema validation | implementing |
| Dependency-context assembly | implementing |
| Synthesis prompt with explicit completed/failed inventory | implementing |
| Degraded mode when the synthesis runtime is unavailable | implementing |
| Streaming final response | planned (O2) |

Acceptance: final response names missing tasks; no synthesis call may invent results when
tasks failed; a runtime failure returns structured task outputs instead of a fabricated
answer.

---

## Phase 6 — Fallback and resilience (PARTIAL)

| Item | Status |
|---|---|
| Local inference when server/coordinator unreachable | DONE (engine is local-first) |
| Local job execution when no workers | implementing (auto dispatcher) |
| Degraded-capacity reporting | planned |
| Reconnect + state reconciliation | planned |
| Interrupted-job handling | implementing (fail explicitly, no silent resume) |

---

## Phase 7 — Production desktop experience (PARTIAL)

| Screen | Status | Next |
|---|---|---|
| Dashboard | DONE | Real status, measured hardware, first-run setup, cloud registry state, job counts. |
| Friend AI | DONE | Local persona and network mode (job submission, live task graph, cancel, honest partial/failed). |
| Worker Center | PARTIAL | Wire start/stop, add enrollment + limits. |
| Model Manager | DONE | Catalog, verified HF downloads, recommendations, import, delete, estimate. |
| Task Center | DONE | DAG + task states + results from the orchestration API. |
| Settings | PARTIAL | Persist via `PUT /api/v1/settings`; real network config. |
| Packaging | DONE | Windows installer and Linux tarball/.deb rebuilt for 1.1.0; frozen launch verified. |

---

## Phase 8 — End-to-end verification (PLANNED)

Required demonstration, on two machines:

1. Install and launch on Windows and Linux.
2. Download a hardware-compatible model.
3. Register both as workers.
4. Submit a decomposable request.
5. Observe a real DAG, parallel execution, and synthesis.
6. Kill a worker mid-job; observe retry/reassignment.
7. Stop the coordinator; observe local fallback with an explicit notice.
8. Restart; observe no duplicate execution and explicit reconciliation.

This cannot be claimed until it is executed on real hardware. Until then the honest status
is "single-machine orchestration verified by automated tests; multi-machine not yet
demonstrated".

---

## Engineering rules carried forward

1. No simulated execution in production paths.
2. Unmeasured values stay `None`, never defaults.
3. Plan and worker output are untrusted input: validate before execution.
4. Every state transition is persisted and has a timestamp.
5. A failing dependency never silently produces a "successful" final answer.
6. One canonical module per concern; duplicates are defects.
