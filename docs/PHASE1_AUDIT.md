# Phase 1 — Repository Audit

**Date:** 2026-11-05
**Scope:** entire working tree at commit `186ddc0` + uncommitted changes
**Method:** static inspection of every production module, plus an executed test run.

This audit is evidence-based. Every claim below names the file that supports it. Where a
capability does not exist, it says so rather than describing an intention.

---

## 1. Executed baseline

```
.venv\Scripts\python.exe -m pytest tests -q
250 passed, 1 failed, 3 deselected in 211.09s
```

The single failure is `tests/test_distributed_gguf.py::test_distributed_layer_inference_e2e`
— a real `llama-cli --rpc` run that timed out after 90 s on this machine. It is the
Stage-2 layer-split path, already documented as unreliable in
`docs/RPC_LINK_MEASUREMENTS.md`. It is not a regression from any change in this audit.

The 3 deselected tests are `live_laya` (network model downloads), excluded by
`pyproject.toml` `addopts`.

---

## 2. What is genuinely implemented

These are real, executed code paths — not simulations:

| Capability | Evidence |
|---|---|
| Local inference via supervised `llama-server` | `racon/runtime/llamacpp.py` (552 lines): binary discovery, ephemeral port, health wait, SSE streaming, usage from `timings`, exit-code diagnosis, process teardown. |
| Ollama adapter | `racon/runtime/ollama.py` (364 lines): `/api/chat`, NDJSON streaming, real token counts. |
| Runtime abstraction | `racon/runtime/base.py`, `types.py`, `registry.py`. No route imports a concrete adapter. |
| Durable local metering | `racon/services/engine_service.py` writes every request, including failures, to `inference_history`. Schema in `racon/storage/migrations.py` (version 2). |
| Model download + integrity | `racon/models/manager.py`: HTTPS-only, SHA-256 required, resumable `.part`, disk guard, GGUF header validation. |
| Memory estimate from real GGUF geometry | `racon/models/estimate.py`. Returns `None` when geometry is unavailable. |
| Hardware probing | `racon/hardware.py`: CPU name/cores/freq, RAM, NVIDIA VRAM via `nvidia-smi`, WMI/lspci fallback. Non-NVIDIA VRAM reported unavailable. |
| Worker enrollment + heartbeat | `racon/agent/worker.py`, `racon/coordinator/{server,registry,invites}.py`, Ed25519 canonical-JSON signatures in `protocols/crypto.py`. |
| Allow-listed remote task execution | `racon/agent/worker.py::_execute_task_workload`: `benchmark_probe`, `text_completion` (Ollama), `distributed_inference`, `shard_verification`. Unknown types fail explicitly. |
| Deterministic placement scorer | `racon/scheduler/deterministic.py`, `backend/racon/scheduler/engine.py`. Four objectives, hard constraints, deterministic tie-break. |
| Stage-2 layer split (opt-in) | `racon/cluster/{planner,head,supervisor,gateway}.py`. Works in principle; measured link bandwidth makes it slow. |
| Desktop shell | `racon/desktop.py`: single loopback port, per-install token, pywebview → Edge/Chromium app → browser fallback. |
| React product UI | `apps/desktop/src/App.tsx` (1,958 lines), 9 tabs, real SSE chat. |

**Fabrication check:** the previous milestone removed the canned-completion paths. The
desktop UI has zero `Math.random`/mock-data hits. What remains is hardcoded *copy* and
invented fallback display values (see §5.4), not fabricated execution.

---

## 3. The central architectural gap

The product vision requires: **one user request → plan → task DAG → many workers →
validation → synthesis → one answer.**

None of that exists. Precisely:

1. **No decomposition.** Repo-wide searches for `decompos*` and `synthes*` return zero
   matches. There is no planner agent.
2. **No DAG.** No graph type, no topological sort, no dependency state machine. Both
   `Placement` classes (`racon/scheduler/models.py:121`, `backend/racon/scheduler/models.py:140`)
   are declared and never constructed.
3. **No job concept in the worker plane.** `racon/` contains zero occurrences of `job`.
   A "job" exists only in `backend/racon/api/routes_jobs.py` and is one prompt → one
   completion executed locally.
4. **No synthesis.** Completed task results are never merged. `TaskResultEnvelope`
   (`protocols/models.py:164`) is stored and read only by admin `GET /admin/tasks`.
5. **No partial-result handling.** A task is `completed` or `failed`; there is no
   "some succeeded, report what is missing" path.
6. **Three disconnected execution planes:**
   - `/api/v1/jobs` → local engine only (`routes_jobs.py` docstring says so explicitly).
   - Coordinator `/admin/tasks` → real remote execution, but only operator-initiated,
     one task at a time, no job grouping.
   - `racon/cluster` → splits model *layers*, not work.

**Conclusion:** the orchestration layer is the missing core, not a refinement of an
existing one. This is Phase 4/5 work, and it is the next implementation milestone.

---

## 4. Reliability and durability defects

| # | Defect | Location | Impact |
|---|---|---|---|
| 1 | Coordinator node registry is an in-memory dict | `racon/coordinator/registry.py:49` | Every coordinator restart forgets all workers. Agents recover only via `re_enroll`. |
| 2 | Task state is an in-memory dict | `racon/coordinator/tasks.py:48` | All task results are lost on restart. |
| 3 | `claim_task` does not transition state or lease the task | `racon/coordinator/server.py:126-140` | The same task is handed to every claim until a result arrives. Duplicate execution is possible. |
| 4 | Nothing ever sets `TaskState.RUNNING` | `racon/coordinator/tasks.py` | Lifecycle is `ASSIGNED → COMPLETED`; no progress visibility. |
| 5 | Task expiry is set but never checked | `tasks.py:70` | Expired tasks remain claimable forever. |
| 6 | `cancel_task` has no delivery route | `tasks.py:139`, `server.py` | Cancellation cannot reach a worker. |
| 7 | Worker never verifies the coordinator signature on a task | `racon/agent/worker.py:40-41, 160` | Imports exist; verification is never called. |
| 8 | Agent heartbeat loop blocks during generation | `worker.py:493-510` | A long task stalls heartbeats, causing false offline detection. |
| 9 | `active_tasks` always `[]` | `worker.py:282,292` | Worker load is invisible to the server. |
| 10 | `db.update_node_heartbeat` does not exist; exception swallowed | `backend/racon/api/app.py:98-104` | Local node telemetry is never updated. |
| 11 | Admin API has no authentication | `racon/coordinator/server.py:158-265` | Only the `127.0.0.1` bind protects it. Acceptable today, fragile. |
| 12 | `verify_token_header` dependency defined but never used | `backend/racon/security/token.py:81` | Auth exists solely as path-prefix middleware. |

---

## 5. Duplication and dead code

### 5.1 Three config modules

- `racon/settings.py` — canonical for engine/desktop.
- `backend/racon/core/config.py` — pydantic-settings control plane; also imported by
  `racon/desktop.py:68`.
- `backend/racon/config.py` — path helpers + `BackendSettings`.

They disagree on database filenames: `racon_lab.db`, `racon_v2.db`, `racon_backend.db`.

### 5.2 Two schedulers

`racon/scheduler/` (dataclasses, used by the UI) and `backend/racon/scheduler/`
(pydantic, used by the API). Same names, different implementations, no shared code.

### 5.3 Three hardware modules

`racon/hardware.py` (used by agent/coordinator), `backend/racon/hardware/` (used by API),
`backend/racon/hardware.py` (**shadowed by the package, unreachable**).

### 5.4 UI defects

| Defect | Location | Impact |
|---|---|---|
| Logs tab renders objects as React children | `App.tsx:1404` vs `routes_logs.py` | TypeError → ErrorBoundary replaces the app. |
| Benchmarks tab calls `b.score.toFixed(4)` on a missing field | `App.tsx:1367` vs `db.py:388` | TypeError → app crashes. |
| `JobItem`/`BenchmarkRecord` types do not match payloads | `types.ts` vs `backend/.../db.py` | Silent data loss; job assignment and response never displayed. |
| Invented hardware defaults (`Intel UHD 630`, `16.0 GB`, `i3`) | `App.tsx:465-535` | Violates the project's non-fabrication rule. |
| Claims `0.0.0.0:8765` bind | `App.tsx:542` | Factually wrong; the shell binds `127.0.0.1` on an ephemeral port. |
| "Manage" and "Import Model" are `alert()` no-ops | `App.tsx:599, 799` | Fake functionality. |

### 5.5 Dead modules

`backend/racon/runtime/`, `backend/racon/workers/`, `backend/racon/api/routes_openai.py`
(only `resolve_local_gguf()` survives), `backend/racon/hardware.py`. Only
`tests/test_backend_modules.py` imports the first two.

---

## 6. Missing product capabilities

| Requirement | Status |
|---|---|
| Task decomposition / planner | **Missing** |
| DAG execution with dependencies | **Missing** |
| Multi-worker job dispatch | **Partial** — coordinator exists, no job grouping |
| Result validation (schema/identity/integrity) | **Partial** — signature only; no schema validation |
| Synthesis agent | **Missing** |
| Partial/degraded results | **Missing** |
| Durable job/task state | **Missing** (coordinator is in-memory) |
| Worker capability model registry | **Partial** — enrollment sends runtimes; no per-model capability list |
| Hardware-based model recommendation | **Missing** — `estimate_fits()` has zero call sites |
| Remote model catalog | **Missing** — one built-in local GGUF + 4 Ollama tags |
| Friend AI persona / local fallback UX | **Partial** — generic Playground; no fallback narration |
| Worker Center controls | **Partial** — read-only table; start/stop API exists, unused |
| Task Center | **Missing** — jobs list only |
| Graceful job recovery after restart | **Missing** |
| Desktop packaging | **Present** — PyInstaller specs + Inno Setup; Linux tar/deb script |

---

## 7. Security posture

Implemented and verifiable:

- Per-install token, generated and persisted with mode 0600
  (`backend/racon/core/config.py:48-80`); no compiled-in default unless `RACON_DEV_MODE`.
- `/api/v1/*` and `/v1/*` both authenticated by middleware (`app.py:158`).
- CORS is an explicit allow-list (`app.py:140-146`).
- Default bind is loopback.
- Ed25519 signatures over canonical JSON (`protocols/crypto.py`).
- Allow-listed task types; no shell/FS execution.
- Model downloads require HTTPS + SHA-256 (`racon/models/manager.py`).

Open issues:

1. Coordinator admin API unauthenticated (§4.11).
2. Worker does not verify coordinator→worker task signatures (§4.7).
3. Plan/task payloads have byte caps declared in `protocols/models.py` but never enforced
   at the boundary.
4. No rate limiting on the coordinator worker routes.

---

## 8. What this means for the roadmap

The repository is a **working local inference product with a working but isolated
distributed task plane**. The shortest credible path to the stated vision is:

1. Build the orchestration layer (planner → validated DAG → selector → dispatcher →
   validator → synthesizer) as a server-side module with durable state.
2. Bind the existing coordinator task plane to it as the distributed dispatcher, and the
   local engine as the fallback dispatcher.
3. Make the desktop UI a real client of that layer (Task Center, Worker Center, Friend AI).
4. Fix the durability and signature defects that multi-worker execution depends on.

The next document, `docs/DEVELOPMENT_ROADMAP.md`, sequences this work with acceptance
criteria.
