# RACoN Development Status

**Updated:** 2026-11-05
**Version:** 1.1.0 (working tree)
**Milestone:** O1 — agentic orchestration core complete; Phase 1 audit delivered
**Branch:** `main`

---

## 0. 2026-11-05 — O1 agentic orchestration

### 2026-11-05 — second pass: Friend AI, first-run setup, release builds

**What shipped**

- **Friend AI tab** replaces the generic Playground. It has two explicit modes:
  - *Friend AI · local* streams from the on-device runtime with a persona prompt that
    forbids claiming remote work.
  - *RACoN network* submits a real orchestration job, polls it, and renders the plan,
    per-task states, assigned worker, attempts, final synthesis, and an honest
    partial/failed explanation inside the conversation. The job can be cancelled there.
- **First-run setup card** on the Overview shows measured RAM/GPU, the recommended
  verified model from `/api/v1/models/recommendations`, live download progress, and a
  link to Friend AI. It appears only when no model is installed.
- **Windowed build fixed.** `sys.stdout`/`sys.stderr` are `None` in a PyInstaller
  `console=False` build; uvicorn's formatter called `sys.stdout.isatty()` during `Config`
  construction and aborted start-up. Logging now installs devnull streams before imports,
  and the local engine passes `log_config=None`.
- **Database isolation fixed.** `get_db_path()` ignored `RACON_DB_PATH`, so pytest opened
  the per-user database and its startup recovery marked a live job failed. The override is
  now honoured and covered by tests.
- **Planner robustness.** Model-written task ids are slugified, duplicates suffixed,
  dependency references remapped, missing optional fields repaired, and impossible
  dependency-cycle back-edges removed with the removal reported. Planning is bounded to
  120 s / 768 tokens and falls back to an honest single task on timeout. The prompt now asks
  for 2-4 compact tasks with only the required fields.
- **Linux build script** writes the inner bash script with LF endings; the previous
  multi-line argument through PowerShell truncated and failed.

**Executed evidence**

| Check | Result |
|---|---|
| Windowed simulation | `sys.stdout=None`, `sys.stderr=None` → engine healthy, stopped cleanly |
| Frozen app (PyInstaller onedir) | log: `Local engine ready on http://127.0.0.1:61350`, UI opened, no `AttributeError` |
| Browser verification | setup card showed *RAM 15.9 GB · GPU Intel UHD 630 (VRAM not measurable)* and recommended Qwen 2.5 0.5B (379 MB) |
| Friend AI network job | `completed`; planner `fallback_single`, execution `local`, 1/1 task, synthesis available, 283 s including cold model load |
| Test suite | 308 passed, 2 skipped, 3 deselected (7m 11s, with PyInstaller running concurrently) |
| Orchestration suite | 36 passed (normalisation, cycle repair, bounded timeout, retries, recovery, HTTP) |
| Windows build | `dist/RACoN/RACoN.exe` 18.8 MB in a 304.1 MB bundle |
| Windows installer | `dist/installer/RACoN_Setup_v1.1.0.exe` 75.8 MB |
| Linux build | `RACoN-linux-x86_64-v1.1.0.tar.gz` 97.7 MB; `racon_1.1.0_amd64.deb` 75.7 MB |

**Still not demonstrated**

1. Multi-machine execution through the coordinator from Friend AI. The dispatcher is
   implemented and unit-tested with doubles; it has not been exercised across two PCs here.
2. LLM-driven multi-task decomposition with the 1B model. Two observed runs after the
   prompt was compacted produced a reciprocal cycle and then unknown dependency ids; both
   were normalised or rejected, and the job fell back to a single task that completed with a
   real result. Decomposition with a larger planner model is unverified.
3. Linux runtime behaviour on real Ubuntu hardware (the artifacts are built in a container).
4. Durable coordinator node/task state (still in-memory).

### What shipped

- **Orchestration package** (`backend/racon/orchestration/`): `schemas.py` (bounded plan
  contract), `dag.py` (validation + topological order), `planner.py` (JSON planning with one
  repair attempt and an honest single-task fallback), `selector.py` (weighted worker scoring
  with unmeasured components reported), `executor.py` (real local engine and real coordinator
  dispatchers), `synthesizer.py` (gap-aware merge), `service.py` (durable lifecycle, retries,
  cancellation, recovery).
- **Durable state**: `orchestration_jobs`, `orchestration_tasks`, `orchestration_events`.
  Non-terminal jobs are failed explicitly on process start; no silent resume.
- **Jobs API**: async submission (202), job detail with plan/DAG, SSE events, cancel.
- **Task Center UI**: submit form (model, execution mode, decomposition, parallelism), job
  list, live detail with per-task state, task outcomes, final answer, cancel.
- **Hardware recommendation**: `GET /api/v1/models/recommendations` uses measured RAM and real
  GGUF estimates; unmeasurable values return `fits: null` with a reason.
- **Hugging Face distribution**: verified LFS SHA-256 downloads for SmolLM2 360M, Qwen 2.5
  0.5B, and Llama 3.2 1B, plus dynamic repository/file discovery and install APIs. See
  `docs/HUGGINGFACE_MODELS.md`.
- **Optional Supabase registry**: read-only status and model metadata under `/api/v1/cloud/*`,
  surfaced on the dashboard. No writes, no DDL, no key exposure.
- **Local GGUF import**: real file import with GGUF validation; fake `alert()` removed.
- **Worker-plane hardening**: atomic claim (`assigned → running` once), expiry enforcement,
  result bound to the assigned node, coordinator signature verified by the worker, heartbeats
  on a dedicated thread, real `active_tasks`.
- **UI crash fixes**: Logs and Benchmarks tabs now render the actual payloads; invented
  hardware defaults and the false `0.0.0.0:8765` bind claim removed.
- **Windowed-build diagnostics**: `racon/logging_setup.py` always writes a UTF-8 log file, so a
  frozen `console=False` build can no longer fail silently.

### Executed evidence

| Check | Result |
|---|---|
| Full test suite | `286 passed, 3 deselected` (before the HF/cloud additions) |
| Orchestration tests | 32 tests, including DAG cycles, planner repair/fallback, retries, partial results, cancellation cleanup, restart recovery, HTTP end-to-end |
| Hugging Face tests | 10 tests (repo validation, LFS SHA extraction, gated errors, catalog integrity, download routing) |
| Cloud tests | 5 tests (status counts, key non-exposure, metadata mapping, API) |
| Recommendation tests | 3 tests (measured hardware, GGUF estimate, heartbeat persistence) |
| Frontend typecheck | `tsc --noEmit` clean |
| Frontend build | `vite build` → 222.86 kB (66.03 kB gzip) |
| Real end-to-end job | status `completed`, result `"RACON end-to-end test passed."`, 11,968 ms, 1 task, real engine usage |
| Real Hugging Face download | `SmolLM2-360M-Instruct-Q4_K_M`, 270,590,880 bytes, SHA-256 verified, GGUF header validated |
| Live Hub catalog check | all three built-in HF entries match the Hub SHA-256 and size |
| Live Supabase check | `models=4`, `model_versions=0`, `model_endpoints=4`, `nodes=1`; read-only |
| Windows build | `dist/RACoN/RACoN.exe` (18.8 MB) in a 304.1 MB bundle |
| Windows installer | `dist/installer/RACoN_Setup_v1.1.0.exe` (75.8 MB) |
| Process cleanup | no `llama-server` orphan after tests; shutdown closes the engine explicitly |

The end-to-end run used the repository GGUF with `llama-server` on CPU, `decompose=false`,
and the real synthesizer. It proves: HTTP submit → persisted job → dispatch → real model
generation → synthesis → persisted result.

### Not yet demonstrated

1. Multi-machine distributed execution through the coordinator from the orchestration layer.
   The dispatcher is implemented against the admin API but has only been exercised with
   doubles in tests.
2. Planner-driven multi-task decomposition with a real model. The 1B model used for the E2E
   run was tested with decomposition disabled; fallback behaviour is covered by tests.
3. Durable coordinator state (nodes/tasks are still in-memory and lost on restart).
4. Linux runtime verification on real Ubuntu hardware (the Linux release is built in a
   container; runtime behaviour is not re-verified here).
5. Supabase registry synchronization. The registry has metadata but no `model_versions`, so
   remote entries remain non-installable; RACoN performs no writes.

---

## 1. Current state in one paragraph

RACoN now performs real, local, streamed LLM inference through a runtime-agnostic adapter
layer, persists metered usage in a migrated SQLite database, and ships a product UI with a
working Playground. Every simulated inference path that previously existed in production
code has been deleted. The distributed/network layer (`racon/coordinator`, `racon/agent`,
`racon/cluster`) is unchanged and still functional, but it is not yet wired to the new
engine; that is milestone M2.

---

## 2. Verified evidence (not claims)

Measured on the development machine (Windows 11, CPU-only path):

| Check | Result |
|---|---|
| Test suite | `251 passed, 3 deselected` in 100.1 s (`pytest tests -q`) |
| Baseline before this work | `211 passed, 3 deselected` (214 collected) |
| Model discovery | `Gemma-3-1B-...-Q4_k_m.gguf`, 768.7 MB, `gemma3`, ctx 32768 |
| Model load (`llama-server`, CPU) | 74.5 s |
| Non-streaming completion | `"hello world"`, usage `17 prompt / 3 completion`, 3.37 tok/s |
| Streaming completion | tokens streamed, final usage `16 prompt / 18 completion` |
| Memory estimate (4k ctx, f16 KV) | weights 768.7 MB + KV 208.0 MB + overhead 61.5 MB = **1038.2 MB** |
| Desktop shell (single port) | `/health` 200, `/v1/models` 401 without token and 200 with it, SPA served, engine stopped cleanly |
| Fabricated-output grep | `RACoN Simulation`, `Processed by`, `Processed completion`, `simulated_reply` → **0 hits** |
| Frontend | `npm run typecheck` clean, `npm run build` → 208.69 kB bundle |

Reproduce the inference proof:

```powershell
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m racon.desktop        # opens the product UI
```

---

## 3. Completed

### M0 — Baseline lock

- `pyproject.toml` rewritten: real `setuptools.build_meta` backend, package discovery,
  `racon` / `racon-desktop` console scripts, `pywebview` dependency declared, and
  `-m "not live_laya"` in `addopts` so the default suite no longer hangs on model downloads.
- `requirements.txt` aligned with what the code actually imports.
- Version unified: `racon/version.py` is the single source; `racon`, `racon.config`,
  `backend.racon`, `backend.racon.config` all read it.
- `tests/conftest.py` exports a deterministic test token and DB path, so tests no longer
  depend on a developer's `.env`.

### M1 — Local inference end-to-end

**New runtime layer** (`racon/runtime/`)

| File | Purpose |
|---|---|
| `types.py` | `ChatMessage`, `ChatChunk`, `ChatResult`, `ChatUsage` (with `reported`), `ModelInfo`, `RuntimeHealth`, `GenerationOptions`, structured error hierarchy |
| `base.py` | `InferenceRuntime` ABC — `available`, `list_models`, `load`, `unload`, `chat`, `chat_stream`, `cancel`, `health` |
| `llamacpp.py` | `LlamaCppRuntime` — supervises `llama-server`, OpenAI-compatible HTTP, SSE streaming, real usage/timings, exit-code diagnosis, clean teardown |
| `ollama.py` | `OllamaRuntime` — `/api/chat` + NDJSON streaming, legacy `/api/generate` retained |
| `registry.py` | `create_runtime`, `available_runtimes`, `runtime_capabilities` |

**New model layer** (`racon/models/`)

| File | Purpose |
|---|---|
| `catalog.py` | Declarative catalog, Ollama tags, operator JSON overrides, no hardcoded filenames elsewhere |
| `estimate.py` | Memory estimate from real GGUF header geometry; returns `None` rather than guessing |
| `manager.py` | Discovery, HTTPS-only download, resumable `.part` files, SHA-256 verification, disk guard, safe delete |

**New engine + storage**

| File | Purpose |
|---|---|
| `racon/settings.py` | One settings object with env → `.env` → defaults precedence; **no credential defaults in code** |
| `racon/services/engine_service.py` | Binds runtime + model store + history; records every request, including failures |
| `racon/storage/migrations.py` | Versioned, idempotent, embedded migrations (`PRAGMA user_version`) |
| `backend/racon/api/routes_engine.py` | `/v1/models`, `/v1/chat/completions` (+SSE), `/api/v1/engine/*` |
| `racon/desktop.py` | Single-port desktop shell: local engine + React UI in pywebview, token injected at launch |

**UI**

- New Playground tab: model selector, streaming chat, stop/cancel, and a metrics panel that
  reports only measured values (TTFT, duration, tokens/s, token counts) and explicitly says
  when the runtime did not report usage.
- `apps/desktop/src/api.ts`: token from the launch URL (no compiled-in default), same-origin
  API base, and a real SSE client.
- Stale settings claims (“0.0.0.0 binding”, “default development token”, “Supabase configured”)
  replaced with accurate statements.

### Fabrication removed

| Path | Before | Now |
|---|---|---|
| `racon/agent/worker.py` | Returned a canned sentence as a *completed* result | Returns `{}` + error; task is reported `FAILED` |
| `racon/agent/worker.py` probe | `sum(i*i)` labelled `OBSERVED` as memory throughput | Labelled `MEASURED` with `metric: integer_loop_throughput` |
| `backend/racon/api/routes_jobs.py` | Completed job with invented text and `duration + 80 ms` | Runs the real engine; failures return the runtime’s HTTP status |
| `backend/racon/api/routes_models.py` | Fake 15 % pull progress; `[RACoN Simulation]` reply at 28.5 tok/s | Pull fails loudly when Ollama is unreachable; `/infer` uses the engine |
| `docker/llama_server.py` | Canned completion for any prompt | Proxies a real `llama-server`; `503` when unconfigured |
| `backend/racon/api/app.py` | Local node advertised `processing_speed_tps: 30.0` | `null` (unmeasured) |

### Security fixes in this pass

- Removed the unconditional acceptance of `racon-local-dev-token`
  (`backend/racon/security/__init__.py`). A per-install token is now generated and persisted
  (`racon_token.secret`, mode 0600) when `RACON_DEV_MODE` is off.
- `/v1/*` (OpenAI surface) is authenticated, not just `/api/v1/*`.
- CORS is an explicit allow-list; `allow_origins=["*"]` removed.
- Default bind is `127.0.0.1`, not `0.0.0.0`.
- Removed the hardcoded Supabase project ref and publishable key from Python defaults.
- Local GGUF filename hardcoded in `ui/chat_panel.py` now comes from `racon.models.catalog`.

---

## 4. Test results by area

| Area | Tests | Status |
|---|---|---|
| Runtime adapters (both, mocked transports) | 15 | pass |
| Engine service (metering, failures, streaming) | 5 | pass |
| Model manager (verify, disk, resume, delete) | 12 | pass |
| Migrations (fresh, idempotent, legacy upgrade) | 5 | pass |
| API endpoints incl. SSE + auth | 17 | pass |
| Coordinator/agent protocol | 2 | pass |
| Cluster planner / RPC link | 36 | pass |
| Scheduler determinism | 13 | pass |
| Protocols / hardening | 8 | pass |
| Hardware / platform | 23 | pass |
| Storage / storage guard | 15 | pass |
| Existing UI, chat, automation, distributed suites | 100 | pass |
| **Total** | **251** | **pass** |

`3` tests are deselected by default (`live_laya`); they download Laya checkpoints and are run
explicitly with `-m live_laya`.

---

## 5. Platform compatibility

| Capability | Windows 10/11 | Linux |
|---|---|---|
| Hardware discovery | Real (registry, WMI, nvidia-smi) | Real (procfs, lspci, nvidia-smi) |
| GPU telemetry | NVIDIA real; Intel/AMD iGPU explicitly unavailable | NVIDIA via `nvidia-smi`; otherwise name-only |
| llama.cpp adapter | Verified on this machine | Same code path; binary discovery includes `/opt/racon/llama`, `~/.local/lib/racon/llama` |
| Ollama adapter | Verified structure (mock tests) | Same |
| Desktop shell | pywebview / Edge `--app` / browser fallback | pywebview / Chromium `--app` / browser fallback |
| Packaging | `RACoN.spec`, Inno Setup | `RACoN_Linux.spec`, tar.gz + `.deb` |

Linux runtime behaviour has **not** been re-verified in this pass; only the Windows path was
executed against real hardware. That is the top item for the next validation round.

---

## 6. Known issues

1. **Pre-existing CPU/RPC crash.** `llama-cli` with `--rpc` dies with SIGILL on this CPU
   (`0xC000001D`) with the installed WinGet build. The test now skips with an explicit
   reason. `llama-server` (the path the product uses) works. Tracked for the Stage-2 work.
2. **~75 s cold model load** on CPU for the 806 MB GGUF. Acceptable but not great; a warm
   keep-alive policy in the agent is required before consumer traffic.
3. **Backend duplication not yet collapsed.** `backend/racon/{hardware,config,runtime,scheduler}`
   still contain older parallel implementations. `racon.*` is canonical; the shims and test
   migration land in M2.
4. **`racon/agent/ipc.py` is still a stub** (ADR-010). The desktop shell no longer depends on it.
5. **`scenarios/*.json` workers are still `is_simulated: true`.** Fine for scheduler
   benchmarks, but the placement scheduler must be fed live node telemetry in M4.
6. **Model catalog has no hosted index.** Users add GGUFs by file or operator JSON; a signed
   remote catalog is pending.
7. **Ollama pull path** still tracked in memory (`_PULL_STATUS`) and resets on restart.

---

## 7. Supabase schema — important discovery

Read-only introspection (`scripts/introspect_supabase.py`) found **59 tables** in the shared
project, far richer than assumed. Full column-level dump: `docs/SUPABASE_SCHEMA.md`
(machine-readable: `docs/supabase_schema.json`).

Tables that now define the control-plane contract:

- **Provider identity/registration** — `nodes` (`worker_token_hash`, `public_key`,
  `enrollment_state`, `availability`, `max_cpu_pct`/`max_memory_pct`/`max_vram_pct`,
  `supported_models uuid[]`, `supported_transports text[]`), `worker_enrollment_tokens`.
- **Telemetry** — `node_hardware`, `node_health`, `node_network`, `worker_heartbeats`.
- **Model registry** — `models`, `model_versions` (`manifest jsonb`, `manifest_checksum`,
  attention geometry), `model_shards`, `model_endpoints`, `partitions`.
- **Inference** — `inference_requests` (idempotency, scheduler linkage), `inference_jobs`
  (`ttft_ms`, `tokens_generated`, `kv_hit`, `was_recovered`), `inference_usage`
  (`verified`, `verification_method`, `worker_count`), `worker_jobs`.
- **Scheduling** — `scheduler_decisions` (`engine`, `proposed_plan`, `final_plan`,
  `validated`, `violations` — exactly the “agent proposes, deterministic validates” model),
  `scheduler_jobs`.
- **Billing** — `credit_accounts`, `credit_ledger`, `credit_reservations`,
  `credit_adjustments`, `pricing_policies`, `reward_policies`, `worker_rewards`.
- **API surface** — `api_keys` (hash-only, scopes, `rate_limit_tier`), `api_key_scopes`,
  `api_idempotency_keys`, `rate_limit_policies`.

**Rule honoured:** zero DDL was issued. The script performs GET requests only.

---

## 8. Next milestone — M2 (provider networking)

1. Bind the control plane to the tables above (no schema changes). Start with `nodes`,
   `worker_enrollment_tokens`, `node_hardware`, `node_health`.
2. Implement `racon/control/` client + `backend/racon/api/routes_agent.py`
   (`/agent/enroll`, `/agent/heartbeat`, `/agent/poll`, `/agent/report`) using
   `nodes.worker_token_hash` for authentication.
3. Collapse `backend/racon/{hardware,config,runtime,scheduler}` into re-export shims.
4. Verify two physical machines registering and heartbeating; re-measure on Linux.

Acceptance: two independent PCs (Windows + Linux) appear as `nodes` rows with live
`node_health`, and a provider can be paused/stopped from the UI with the change reflected
in the database.
