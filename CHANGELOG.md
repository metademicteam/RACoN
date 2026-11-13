# Changelog - RACoN Desktop

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added - Together AI agentic scheduler

- `racon` and `racon-agent` can now run through a Together AI layer: the
  `together/Tev1-4B-experimental` decision model classifies chat vs task,
  `MiniMaxAI/MiniMax-M3.1-Flash` answers normal chat, plans task requests with tool calls,
  and merges/reviews worker outputs with a low token budget.
- Task plans are dispatched in parallel to online workers with a measured fastest-node
  score (claim latency/TTFT, load, RAM, model fit). Only final worker outputs return to the
  coordinator; progress is streamed in the `racon` SSE sidecar.
- Together settings are passed into the coordinator container from the gitignored `.env`.
  The layer degrades to the deterministic gateway when disabled or unreachable, answers
  directly when no worker is online, and marks deterministic merges as degraded.

### Changed - agentic latency and task selection

- Normal chat now uses a user-only task gate and a short fast path. Obvious chat requests
  skip the router and stream the agent model immediately instead of being decomposed into
  worker tasks.
- `TOGETHER_REASONING_EFFORT=none` disables hidden reasoning for the agent model. Without
  it, a simple movie recommendation could spend the entire output budget on
  `reasoning_content` and return no visible answer.
- Task mode emits a visible status line immediately and sends SSE keepalive comments while
  workers execute, so clients no longer see an empty stream during long worker waits.

### Fixed - Cloudflare tunnel and admin routing

- `racon.metademic.org` now has a per-user sign-in scheduled task for the Cloudflare
  tunnel, restoring the public site after the tunnel had no active connection.
- `/admin` and `/api/*` are routed to the coordinator through Cloudflare, so the admin
  dashboard works on the public hostname instead of the static website container.

## [1.4.0] - 2026-11-09

### Fixed - OpenAI-compatible clients and tokens

- `POST /v1/chat/completions`, `POST /chat/completions`, and trailing-slash variants now
  reach the same handler. A request can no longer fall through to the SPA catch-all and
  return a misleading `405 Method Not Allowed`.
- Unknown `/v1` and `/api/v1` paths return a JSON 404 that names the endpoint.
- The cluster gateway applies the same alias and trailing-slash normalization before its
  allow-list, key check, and rate limits.
- `RACON_CLIENT_TOKEN` from `.env` now resolves as the server's limited-scope
  `CLIENT_TOKEN` through `AliasChoices`.

### Added - local fallback runtime

- New `racon.runtime.mistralrs` adapter runs a GGUF model in-process when llama.cpp is
  missing or the Ollama daemon is not running. Streaming, cancellation, health, and
  measured usage are supported.
- `RACON_FALLBACK_RUNTIME=mistralrs` selects the fallback, and
  `RACON_MISTRALRS_MODEL` / `RACON_MISTRALRS_QUANT` control the text-model default.
- Windows and Linux specs collect the mistralrs wheel when it is present in the build
  environment. A build without the wheel still succeeds and reports the runtime as
  unavailable.

### Changed - install-time connection

- `scripts/build_desktop.ps1` generates `RACON_CLIENT_TOKEN` when `.env` has none and
  writes `installer/client.json` with the public server URL and token.
- The Windows and Linux installers copy `client.json` into per-user data. The desktop
  shell reads it before opening the window and passes it to the SPA as launch settings.
- The Settings panel keeps the token hidden behind Advanced connection settings and
  never displays it by default.

### Changed - interface and website

- New tokenized desktop stylesheet with visible focus states, hover states, responsive
  navigation, tabular numbers, and reduced-motion support.
- Website rebuilt with a hero network visual, structured feature and download sections,
  accessible mobile navigation, and copy controls.
- Removed em dash characters from the website and application UI.
- Version bumped to `1.4.0` for the Windows installer and Linux tarball/.deb.

## [1.3.0] - 2026-11-08

### Added - network model tiers, vision projectors and hardware matching

- **Nine network-tier GGUF entries** with verified Hugging Face LFS SHA-256 values and exact
  byte sizes: Qwen3.5 9B (medium GPU), Qwen3.8 27B and Ornith-1.5 35B-A3B (high GPU),
  LFM3-VL 2B and OrcaCyber Nano (low GPU), and Qwen3.5 2B / Spark 1.7B / Ternary Bonsai 4B /
  Qwen3.5 0.8B (CPU). Every public link is HEAD-checked by
  `scripts/verify_model_links.py`.
- **Hardware tiers** (`cpu`, `low_gpu`, `medium_gpu`, `high_gpu`) on catalog entries, with
  `min_ram_mb` / `min_vram_mb`. `racon/models/matching.py` ranks models for a machine and
  the scheduler can report per-worker suggestions.
- **Per-worker model suggestions**: `GET /api/v1/admin/model-suggestions` returns each
  node's measured tier, `recommended_model` and a ranked list. `GET /api/v1/models/suggestions`
  does the same for the local machine.
- **Multi-file model installs.** A model and its vision projector are downloaded, verified
  and installed together; a `<model>.gguf.racon.json` sidecar records the projector.
- **llama.cpp vision + GPU offload.** The adapter skips `mmproj-*` files as chat models,
  auto-detects the sidecar, passes `--mmproj`, and enables full GPU offload only when the
  GGUF actually fits measured VRAM (`RACON_AUTO_GPU_LAYERS=0` disables it).
- **Gated model support.** OrcaCyber requires an accepted Hugging Face license and
  `HF_TOKEN`; the downloader resolves the LFS digest with the token before writing bytes.
- **Live node inventory** (from the previous session): model lists are refreshed on every
  signed heartbeat, so `model="racon"` routes against what each worker currently has.

### Fixed - context window and silent truncation

- **Default context raised from 4096 to 8192** tokens (`RACON_CONTEXT_SIZE`). The runtime
  caps it at the model's trained GGUF context rather than extending past training.
- **One llama.cpp slot** (`--parallel 1`) so the whole context budget belongs to the
  request; newer llama-server defaults could split it across slots.
- **`POST /api/v1/engine/load` honours `context_size`** instead of ignoring it.
- **No silent truncation:** llama.cpp and Ollama streams that end without a terminal
  event now raise an error; the desktop SSE client reports `stream_incomplete` instead of
  calling `onDone`, and the API SSE route has a catch-all error frame.
- **Context overflow is typed:** `context_length_exceeded` (HTTP 400) with a message to
  raise `RACON_CONTEXT_SIZE` or shorten the conversation.
- **Chat output budget raised** from 512 to 2048 tokens in the desktop UI, API schema,
  engine defaults, and the PySide6 chat worker. Workers send `num_ctx` to Ollama.
- **Thinking-model fix:** Qwen3.5 defaults to thinking and could spend the entire output
  budget in `reasoning_content`, returning empty `content`. llama.cpp now starts with
  `--reasoning off` and Ollama requests send `think: false` (`RACON_REASONING=on/auto`
  opts back in). A reasoning-only response is reported as an explicit error instead of an
  empty completion.
- **Character-budget preflight:** prompts far above `RACON_CONTEXT_SIZE * 4` characters
  are rejected with `context_length_exceeded` before dispatch, so Ollama can no longer
  silently truncate them.

### Changed

- **Preconfigured desktop token.** `scripts/build_desktop.ps1` reads the limited-scope
  `RACON_CLIENT_TOKEN` from `.env` and compiles it as `VITE_DEFAULT_AUTH_TOKEN`; the
  Windows and Linux build scripts run it automatically. The administrator token is never
  embedded. A new `scripts/build_windows.ps1` wraps the frontend, PyInstaller and Inno
  Setup steps.
- Version bumped to `1.3.0` for the Windows installer and Linux tarball/.deb.
- `ModelManager.installed()` no longer reports `mmproj` projectors as servable models.

## [1.2.0] - 2026-11-07

### Added - server mode, PostgreSQL and the operator console

- **Unified server.** The Docker container now exposes the worker ingress (`/discover`,
  `/enroll`, `/heartbeat`, `/tasks/claim`, `/tasks/report`) from the same FastAPI app as the
  desktop API. Previously only the standalone `racon.coordinator.server` had those routes,
  so a worker could never join the container.
- **Shared coordinator state** (`backend/racon/api/coordinator_state.py`): persistent
  Ed25519 identity under `RACON_DATA_DIR`, node registry, invite store, task manager and
  auto-approve/auto-join policy.
- **PostgreSQL support.** `DATABASE_URL` selects PostgreSQL (`psycopg2`), SQLite remains the
  desktop/test store. Docker Compose starts `racn-postgres` with a health check and a
  persistent volume; `routing_events` is created automatically.
- **Admin dashboard** at `/admin` (no build step): live worker inventory with CPU, cores,
  RAM, GPU/VRAM, OS, runtimes, RPC endpoint, heartbeat age and active tasks; the
  node-to-node routing log; agentic scheduler jobs/tasks; model download progress; invite
  management; approve/revoke and test-task dispatch.
- **Admin API** under `/api/v1/admin/*` (token protected) with overview, nodes, events,
  scheduler, jobs, downloads, invites and dispatch.
- **Durable routing audit** (`routing_events`): enrollment, heartbeat, scheduler assignment,
  worker claim, worker report, approval and revocation.
- **In-process dispatcher** (`InProcessDispatcher`) lets the scheduler place tasks without a
  second admin listener on port 8766.
- **Smart model router** (`backend/racon/orchestration/model_router.py`): deterministic
  request classification (code, reasoning, chat, tool_call, creative, info, image; quick /
  moderate / deep), hard capability filters, size-tier selection and a scored candidate
  list. `model="auto"` is available on `/v1/chat/completions` and `/api/v1/jobs`; every
  response carries `racon.router`. `POST /api/v1/scheduler/route` previews the decision.
- **Model-aware workers**: enrollment advertises the models a worker can serve. The new
  fields are added to the signed payload only when present, so 1.1 agents keep verifying.
  The in-process and coordinator dispatchers pass the inventory to the selector, and a
  worker that misses the 30-second heartbeat window is excluded from placement.
- **Operator diagnostics**: `GET /api/v1/admin/diagnostics` checks PostgreSQL, the active
  inference runtime, Ollama, llama.cpp, installed models and disk, worker liveness, the
  routing log, Tailscale and token strength. The admin console has a Diagnostics tab and a
  smart-router preview.
- **llama.cpp in the server image**: a pinned CPU build (`b12110`) is installed at
  `/opt/racon/llama`, so verified GGUF downloads are servable inside the container without
  a host runtime. `RACON_RUNTIME=ollama` remains available.

### Security - scoped client token (2026-11-08)

- **Administrator and client tokens are separate.** `AUTH_TOKEN` is the operator secret;
  `RACON_CLIENT_TOKEN` is the limited-scope token compiled into desktop and web builds.
  Clients can run jobs, chat and inspect the API, but `/api/v1/admin/*` and settings updates
  require the administrator token and return `403` for a client token.
- **The app no longer ships the administrator token.** Release builds compile only the client
  token (`VITE_DEFAULT_AUTH_TOKEN`); upgrading installs discard the old shared development
  token from local storage.
- **Fail closed.** Docker Compose refuses to start without `AUTH_TOKEN`; the hardcoded
  `racon-local-dev-token` default was removed from the standalone Dockerfile and helper
  scripts now read tokens from `.env`.
- Added `tests/test_token_scopes.py` for scope enforcement (client allowed, client forbidden
  from admin/settings writes, admin allowed, unknown token rejected).

### Added - RACoN Network Gateway (2026-11-08)

- **RACoN inference keys.** DB-backed `rk_` keys with SHA-256-at-rest storage, per-key rate
  limits, revocation, last-used audit, admin CRUD (`/api/v1/admin/keys`) and an API-keys tab in
  the admin console. Keys are scoped to the public `/v1` surface and cannot reach admin routes.
- **Unified OpenAI gateway.** `model: "racon"` classifies each request: a query streams from one
  worker; a complex task is planner-decomposed across workers and merged by the synthesizer.
  `racon-single`, `racon-task`, and `racon-single:<model>` / `racon-task:<model>` override the
  mode and pin a worker model.
- **Streaming worker protocol.** New `chat_completion` task type and
  `POST /tasks/progress`; workers push deltas from their outbound-only control channel. The
  coordinator keeps a bounded replay buffer, so late subscribers receive the prefix.
- **Task stream.** `POST /v1/chat/completions` emits `task_delta` progress frames and streams the
  synthesized answer; the final chunk carries `racon.status` and missing task names. The existing
  job SSE now includes task deltas too.
- **OpenAI compatibility.** Tool/function calling (including normalized tool-call deltas),
  content-part messages, `tool_choice`, `parallel_tool_calls`, `response_format`, `seed`,
  penalties, and OpenAI-shaped errors for `/v1/*`.
- **Network model catalog.** `/v1/models` lists `racon`, the mode aliases, online worker models,
  and coordinator-local fallback models. Workers advertise `streaming_chat`; older workers are
  excluded from streamed routing.
- **Routing quality.** Quick prompts prefer small models with a quality floor; the job selector
  penalizes workers already running a task; multi-task token budgets are distributed across the
  plan; planner and synthesis models can differ from the worker task model.
- Added `docs/RACON_NETWORK.md`, `tests/test_api_keys.py`, `tests/test_network_gateway.py`, and
  `tests/test_openai_compat.py`.

### Fixed - worker inventory and enrollment (2026-11-08)

- **Real hardware in the admin console.** `node_capabilities` is now refreshed on every
  enrollment (CPU, cores, GPU/VRAM, runtimes, OS, RPC endpoint, models, features), and
  `get_nodes()` returns the capability dictionary. Previously the admin merge read a field
  the DB layer never returned, so every remembered node showed `Unknown` / no OS / no
  runtimes even though the data was stored.
- **Schema upgrade for existing deployments.** `node_capabilities` gained `os_name`,
  `rpc_endpoint`, `models_json` and `features_json`; `init_db` adds the columns in place
  (`create_all` never alters an existing table).
- **Friendly OS labels.** Linux workers report `/etc/os-release`'s `PRETTY_NAME` (for
  example `Ubuntu 24.04.4 LTS`) instead of the raw kernel build string.
- **The desktop app is a network participant.** On launch it enrolls and heartbeats as a
  provider agent in the background, so a running app appears in the admin inventory.
  `RACON_AUTO_CONNECT=0` disables it.
- **Local hardware in the desktop UI.** The Overview hardware card and the header CPU/RAM
  now read this machine's local engine. Previously they showed the remote coordinator's
  container (Debian, 7.7 GB) because only the remote `/api/v1/status` was consulted.
- **Launch token pinned to the local engine.** The engine receives both `RACON_AUTH_TOKEN`
  and `AUTH_TOKEN`, so a `.env` in the working directory cannot override the per-install
  token the desktop window was launched with.
- **Coordinator URL resolution.** Installed clients default to the public coordinator and
  automatically use `127.0.0.1:8765` when this machine runs the coordinator itself.
  `RACON_COORDINATOR_URL` still overrides both.
- Added `tests/test_node_capabilities.py` (persist, refresh and return hardware).

### Fixed - networking and model delivery

- **Docker image now copies `protocols/`**; without it every enrollment import failed.
- **`RACON_MODEL_DIR=/models`** aligns the coordinator's downloads with the shared
  `racon-models` volume used by the registry and llama services.
- **Downloads resume and retry.** Transient connection drops are retried three times using
  Range requests, and the final size is checked against the catalog before SHA-256
  verification.
- **Default coordinator URL** no longer points at a stale tailnet host; it defaults to
  `http://127.0.0.1:8765` and is overridden with `RACON_COORDINATOR_URL` on remote workers.
- **Worker engine fallback.** `text_completion` tries Ollama first, then the worker's local
  RACoN engine, so a llama.cpp-only worker can execute distributed tasks.

### Added - deployment assets

- `scripts/start_server.ps1` (build stack, wait for health, print tailnet URLs) and
  `scripts/start_worker.ps1` (join a server headless or with the desktop UI).
- `docs/SERVER_DEPLOYMENT.md` - Windows host + Tailscale + PostgreSQL + Docker runbook.
- `racon_original/` - a clean snapshot of the documentation and source tree.

## [1.1.0] - 2026-11-05

### Added - Friend AI and first-run setup (2026-11-05)

- **Friend AI tab** (formerly Playground): a local persona that streams from the on-device
  runtime, plus a **RACoN network** mode that submits a real orchestration job and renders
  the live task graph in the conversation - per-task state, assigned worker, attempt count,
  final synthesis, and an explicit partial/failed explanation. Jobs can be cancelled from
  the chat.
- **First-run setup card** on the Overview: when no model is installed it shows the measured
  RAM/GPU, the hardware-appropriate verified model from
  `GET /api/v1/models/recommendations`, download progress, and a path to Friend AI. No
  assumed hardware values are displayed.

### Fixed - windowed launch, database isolation, planner robustness (2026-11-05)

- **Windowed (console-less) builds now start.** PyInstaller `console=False` sets
  `sys.stdout`/`sys.stderr` to `None`; uvicorn's formatter calls `sys.stdout.isatty()` while
  constructing `Config`, which aborted start-up and silently fell back to the Qt lab. The
  logging setup installs devnull streams before any import uses them, and the local engine
  passes `log_config=None` because logging is already configured.
- **`RACON_DB_PATH` is honoured** by `get_db_path()`. The test suite had been opening the
  real per-user database; its startup recovery marked a live job "interrupted by process
  restart" while the engine was still running.
- **Planner output normalisation:** model-written task ids (`Task 1`, `1`, `task.1`) are
  slugified, duplicates get stable suffixes, dependency references are remapped, missing
  `title`/`requirements`/`depends_on` fields are repaired, and impossible dependency-cycle
  back-edges are removed and reported in `planner_note`. Length limits, unknown
  dependencies and all other schema rules are still enforced strictly. Planning is bounded
  to 120 s / 768 tokens, with an honest single-task fallback on timeout.
- **Linux Docker build** writes the inner shell script with LF endings instead of passing a
  multi-line argument through PowerShell (which truncated it).

### Added - agentic orchestration (2026-11-05)

- **Orchestration package** (`backend/racon/orchestration/`): planner agent, bounded plan
  schema, DAG validation with cycle detection, weighted worker selection, two real
  dispatchers (local engine and coordinator task plane), synthesis agent, and a durable job
  lifecycle service.
- **Durable job state**: new `orchestration_jobs`, `orchestration_tasks` and
  `orchestration_events` tables. Every state transition is persisted with a timestamp.
- **Task decomposition**: the planner asks the runtime for a JSON plan (≤ 12 tasks), validates
  it strictly, performs one repair attempt, and falls back to an honest single-task plan when
  the runtime or the plan is unusable. `planner_mode` and `planner_note` record which path ran.
- **Dependency execution**: independent tasks run concurrently up to `max_parallel_tasks`;
  dependent tasks wait for completed inputs. A failed dependency marks its dependents failed
  with the blocking ids named.
- **Retries and reassignment**: tasks retry up to their declared limit, preferring a worker
  that has not already failed the task.
- **Synthesis**: completed, failed and unexecuted tasks are all passed to the synthesizer,
  which must name gaps. If the synthesis runtime is unavailable the job becomes `partial`
  and the structured task outputs are preserved instead of a fabricated answer.
- **Cancellation and recovery**: cancelling stops dispatch and marks outstanding tasks
  cancelled. On process start, non-terminal jobs are marked failed with an explicit
  "interrupted by process restart" reason - no silent resume.
- **Jobs API**: `POST /api/v1/jobs` (202), `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`,
  `GET /api/v1/jobs/{id}/events` (SSE), `POST /api/v1/jobs/{id}/cancel`.
- **Task Center UI**: request form (model, execution mode, decomposition, parallelism), job
  list, live job detail with per-task DAG state, task outcomes, final answer and cancel.
- **Hardware model recommendation**: `GET /api/v1/models/recommendations` compares measured
  RAM against declared requirements and uses real GGUF estimates for installed models;
  unmeasurable cases return `fits: null` with a reason. The Model Manager shows the result.
- **Local GGUF import**: `POST /api/v1/engine/models/import` copies and validates a local
  GGUF file; the fake `alert()` import stub is gone.

### Added - Hugging Face distribution and cloud registry

- **Hugging Face GGUF downloads** (`racon/models/huggingface.py`): repository validation,
  LFS SHA-256 discovery, HTTPS-only URLs, and integration with the verified model manager.
  Built-in Q4_K_M entries for SmolLM2 360M, Qwen 2.5 0.5B, and Llama 3.2 1B.
- **Model install API**: `GET /api/v1/models/hf/files`, `POST /api/v1/models/hf/install`,
  `GET /api/v1/models/hf/whoami`; `POST /api/v1/models/pull` now routes GGUF catalog ids to
  the model manager instead of Ollama. Unverified Hub files are refused.
- **Hardware-ranked install**: recommendations include Hugging Face entries with
  `downloadable`, `repo_id`, `filename`, `sha256`, and a one-click install button in the
  Model Manager.
- **Optional Supabase registry** (`racon/cloud/supabase.py`): read-only status and model
  metadata endpoints (`/api/v1/cloud/status`, `/api/v1/cloud/models`). No DDL, no writes,
  and credentials are never returned. The dashboard shows the measured registry state.
- **Verification tools**: `scripts/verify_huggingface.py` (live catalog + real download proof)
  and `scripts/build_linux_docker.ps1` (Ubuntu build in Docker).

### Fixed

- Coordinator task claim is now atomic: `assigned → running` once, expiry enforced, so a task
  is not delivered to every subsequent claim. Results are bound to the assigned node.
- Workers now verify the coordinator's Ed25519 signature on every claimed task envelope
  before executing it.
- Worker heartbeats run on a dedicated thread, so a long generation no longer stalls them;
  `active_tasks` reports the task actually executing.
- Logs tab no longer crashes on object payloads; Benchmarks tab reads the real record fields.
- Removed invented hardware fallbacks (`Intel UHD 630`, `16.0 GB`, `i3`) and the false
  `0.0.0.0:8765` bind claim from the Overview.
- The local node telemetry sampler now writes a heartbeat and measured free RAM through an
  existing `Database.update_node_heartbeat` method instead of raising a swallowed
  `AttributeError`.

### Added

- **Unified runtime layer** (`racon/runtime/`): one `InferenceRuntime` contract with real
  `LlamaCppRuntime` (supervises `llama-server`, OpenAI-compatible HTTP, SSE streaming) and
  `OllamaRuntime` (`/api/chat`, NDJSON streaming). Streaming, cancellation, token accounting,
  crash classification and clean teardown behave identically across both.
- **Model manager** (`racon/models/`): catalog with operator overrides, HTTPS-only downloads,
  resumable `.part` files, SHA-256 verification, free-space guard, safe deletion, and memory
  estimates derived from real GGUF header geometry.
- **Engine service** (`racon/services/engine_service.py`): single place that binds runtime,
  model store and history; records every request including failures.
- **Engine API** (`backend/racon/api/routes_engine.py`): `/v1/models`, authenticated
  `/v1/chat/completions` with SSE, and `/api/v1/engine/*` for status, load/unload, models,
  estimates, history, events and usage.
- **Playground UI**: streaming chat with a stop button and a metrics panel reporting only
  measured TTFT, duration, tokens/second and token counts.
- **Desktop shell** (`racon/desktop.py`): single loopback port, per-install token injected at
  launch, pywebview window with Edge/Chromium/browser fallbacks.
- **Database migrations** (`racon/storage/migrations.py`): versioned, idempotent, tracked with
  `PRAGMA user_version`; adds `model_installs`, `inference_history`, `engine_events`,
  `provider_config`.
- **Supabase introspection** (`scripts/introspect_supabase.py`): read-only schema capture;
  produced `docs/SUPABASE_SCHEMA.md` and `docs/supabase_schema.json` (59 shared tables).
- `ARCHITECTURE.md` (root) and `docs/DEVELOPMENT_STATUS.md`.

### Changed
- `python -m racon` now launches the product desktop shell; `--lab` starts the PySide6 window
  and `--headless` keeps the provider agent.
- Default bind address is `127.0.0.1`; CORS is an explicit allow-list.
- `/v1/*` on the local engine now requires the API token, like `/api/v1/*`.
- The local API token is generated per install and persisted (`racon_token.secret`, mode 0600).
  The shared `racon-local-dev-token` bypass has been removed.
- Supabase project URL and keys are read from the environment only; no credential defaults
  remain in Python source.
- The test suite no longer runs live-Laya download tests by default and no longer depends on a
  developer's `.env`.

### Removed
- Canned completion returned as a successful, signed worker result.
- Fabricated job execution ("Processed by …") in `routes_jobs.py`.
- Simulated model-pull progress and the `[RACoN Simulation]` inference reply in `routes_models.py`.
- The second, simulated OpenAI router in `routes_openai.py` (the module keeps only
  `resolve_local_gguf`).
- Canned completion in `docker/llama_server.py`; it now proxies a real backend and returns 503
  when unconfigured.
- Advertised `processing_speed_tps: 30.0` for the local node (now `null` = unmeasured).

### Fixed
- `pyproject.toml` used a non-standard build backend and declared no packages or scripts.
- `requirements.txt` was missing `pywebview`, which the shipped desktop launcher requires.
- Version numbers were inconsistent across `racon`, `backend.racon`, `pyproject` and the
  installers; `racon/version.py` is now the single source.

## [1.0.10] - 2026-11-01
### Fixed
- **Ubuntu app did not start: "Could not find the Qt platform plugin 'xcb' in ''"**. The Linux bundle
  shipped Qt libraries but no `PySide6/Qt/plugins` tree, so `racon` exited at startup while
  `racon --headless` still worked. `RACoN_Linux.spec` now collects every PySide6 plugin as a binary
  (so its shared-library dependencies are resolved), and a frozen build points
  `QT_QPA_PLATFORM_PLUGIN_PATH` at the bundled plugins before creating the application.
- **Ubuntu builds made in a headless container**. The build docs now state that the Qt/X11 runtime
  libraries are required on the build host; without them PyInstaller silently drops `libqxcb.so`.

## [1.0.9] - 2026-11-01
### Fixed
- **The head split layers onto its own RPC server**: the head PC enrolls as a contributor and reports its
  own `rpc_endpoint`, which discovery accepted as a worker. The supervisor planned 13/13 layers onto two
  processes on one PC and restarted the head every ~20 s. Endpoints that resolve to this PC are now dropped
  from discovery and from `cluster.json` planning.
- **503 loop from a slow worker load**: a split is aborted when the llama-server log stops growing
  (`rpc_stall_timeout_s`, default 45 s) instead of waiting out the whole window, and membership changes that
  do not change the split no longer restart the head.
- **Layer split kept out of the way by default**: `worker_layer_policy` defaults to `auto`, so a model that
  fits the head runs head-only and a worker is never contacted.
- **`rpc-server` wrote every tensor to disk**: the local file cache (`-c`) is off by default. Re-enable with
  `RACON_RPC_CACHE=1` or `racon.cluster worker --cache`.

### Added
- `max_worker_layers` caps each worker's share so a forced split's first load fits the startup window.
- Measured RPC load rate per worker (`worker_rates.json`) caps the next split to what the link can move in
  `rpc_startup_timeout_s` (now 300 s).
- `worker_layer_policy` (`auto` / `always` / `never`) and `rpc_stall_timeout_s` in `cluster.json`.
- [docs/RPC_LINK_MEASUREMENTS.md](docs/RPC_LINK_MEASUREMENTS.md): live measurements on the Ubuntu worker
  (2 layers = 43 s load, 0.65 tok/s vs 9.9 tok/s head-only at ~1.3 MiB/s link) and the force/limit recipe.
- `scripts/verify_rpc_worker.ps1`: starts a throwaway head on port 8091, asserts the RPC device lines, measures
  generation, and stops it, so a link can be re-measured without touching the live head.

## [1.0.8] - 2026-11-01
### Fixed
- **“Distributed RPC Worker” chat mode reported No backend**: the mode requires a local GGUF and
  skipped the gateway when the installed app did not bundle the model. It is now named
  “Local Model + RPC Workers” and falls back to the local/public gateway when no local GGUF exists,
  so Windows and Linux worker PCs still get a streamed answer.

## [1.0.7] - 2026-11-01
### Fixed
- **RPC workers received zero layers**: `-ngl 0` registered RPC0 but offloaded no layers. The head
  now offloads exactly the plan's worker layers, selects only the RPC devices (`-dev RPC0,...`),
  and sends worker-only tensor splits, so the Linux worker actually computes model layers.
- **Slow worker stalls**: workers are gated by Tailscale RTT, and an RPC split that does not
  become healthy within `rpc_startup_timeout_s` (default 120 s) falls back to head-only GPU and
  disables that worker for the session instead of leaving the gateway at 503.
- **Windows chat did not stream**: the desktop chat tried the local GGUF/llama-cli path first,
  which returns at the end. It now prefers the local head gateway on `127.0.0.1:8000`, then the
  public gateway, so Windows streams tokens like Linux. The local GGUF remains a fallback.

## [1.0.6] - 2026-10-31
### Added
- **Automatic worker layer-shard download**: after enrollment the Worker Agent downloads and
  verifies its assigned shard in the background, with progress in the activity log and the shard
  name/layer range in the Worker Agent panel. RPC stays active during the download.
- **Shard preparation is independent of RPC startup**, so a slow or failed shard download no
  longer prevents the worker from accepting RPC layers.

## [1.0.5] - 2026-10-31
### Fixed
- **503 "Loading model" after a worker joins**: a mixed GPU + RPC tensor split could stall during
  model load. When RPC workers are active the head now runs CPU-only (`-ngl 0`) so loading
  completes; head-only mode still uses the GPU.
- **Expired worker sessions**: a worker now re-enrolls when `/tasks/claim` returns HTTP 401,
  instead of showing "running" while every task claim is rejected.
- **llama-server diagnostics**: head output is always written to `llama-server.log`; it was
  previously discarded when `--log-file` was not supplied.

## [1.0.4] - 2026-10-31
### Fixed
- **SIGILL on older Ubuntu CPUs**: the bundled llama.cpp CPU backend was compiled for this
  development PC's AVX2 CPU, so workers on older machines died with exit code `-4` (SIGILL).
  llama.cpp is now built with runtime CPU dispatch (`GGML_CPU_ALL_VARIANTS`), providing baseline
  x64, SSE4.2, AVX, AVX2 and AVX-512 backends and selecting the best one at startup.
- **Signal diagnostics**: an `rpc-server` exit caused by a signal now reports its name, and SIGILL
  explains that the CPU does not support the build instead of showing a bare `-4`.

## [1.0.3] - 2026-10-31
### Fixed
- **Ubuntu RPC worker startup**: `libgomp.so.1` is bundled next to the llama.cpp binaries, so
  `rpc-server` starts on a stock Ubuntu installation without the `libgomp1` package.
- **Actionable RPC failures**: `rpc-server` output is always written to `rpc-server.log`; if it
  exits or cannot bind, the worker reports the exit code, the last log lines and the log path in
  the activity log and in the Worker Agent panel.
- **RPC readiness check**: the agent now waits for the RPC port to accept connections instead of
  assuming success after a fixed delay, and terminates a half-started server.
- **Hardware and Worker Agent readability**: both panels scroll instead of squeezing content, and
  long OS, GPU, key and RPC text wraps instead of being clipped.

### Changed
- Linux releases now target Ubuntu 24.04+ (glibc 2.38) and document that floor; build on Ubuntu
  22.04 to produce 22.04-compatible artifacts.

## [1.0.2] - 2026-10-31
### Fixed
- **Ubuntu desktop crash (segmentation fault)**: worker enrollment, shard-download progress and
  startup messages were written to `QTextEdit` from background Python threads. Qt widgets are
  main-thread only, so X11/Wayland could abort the whole process. All worker updates now cross a
  Qt signal and are applied on the GUI thread.
- **Enrollment 502 handling**: the app now reports a coordinator outage as “head node may be
  offline” instead of a bare HTTP status, and auto-connect retries every 60 seconds.
- **Headless worker resilience**: `racon --headless` retries enrollment with exponential backoff
  instead of exiting on the first coordinator error, so systemd no longer restart-loops.
- **Shard downloads**: transient DNS/network failures are retried three times before giving up.
- **Worker start failure**: the agent loop only starts after the RPC worker actually started, and
  the Start button is no longer re-enabled while preparation is still running.
- **Linux `.deb` packaging**: the package is staged in a native temp directory so `dpkg-deb` accepts
  the permissions when the repository lives on a Windows drvfs mount (`/mnt/f`); `libgomp1` is now
  declared as a dependency for the bundled llama.cpp runtime.

### Changed
- Version is sourced from `racon/config.py` in the window title and gateway metadata.
- Public Tailscale Funnel was repointed from the stale Docker endpoint on port 8443 to the live
  coordinator ingress on port 8765.

## [1.0.1] - 2026-10-28
### Added
- **Real-Time Token Streaming (SSE)**: Chat UI now parses Server-Sent Events (`stream: true`) chunk-by-chunk from the public gateway and local coordinator, delivering low-latency real-time response rendering.
- **SSD Storage & Write-Wear Guard (`racon/runtime/storage_guard.py`)**: Enforces daily NVMe/SSD write ceilings (default 30 GB/day) to prevent runaway model re-download wear cycles on consumer drives.
- **Quantized KV Cache Support (`-ctk q8_0 -ctv q8_0`)**: Added support in cluster head server for quantized attention caches, cutting memory consumption in half.
- **Dynamic Installer Manifest Binding**: Public website UI (`website/app.js`, `website/index.html`) automatically discovers and links to the latest version and checksum from `manifest.json`.

### Fixed
- **Windows Defender False Positive (Error 225)**: Disabled UPX compression (`upx=False`) in PyInstaller build specifications (`RACoN.spec`, `RACoN_Desktop.spec`) to eliminate heuristic ML false-positive quarantine on clean Windows machines.
- **Chat Fallback Resilience**: Added automatic graceful fallback from streaming to standard chat completions upon network interruptions.

## [Unreleased]
### Added
- **Ubuntu/Linux client and worker**: `RACoN_Linux.spec`, `scripts/build_linux.sh`, a per-user
  `install.sh`, desktop entry and `.deb` output. Linux nodes enroll through the same coordinator,
  advertise `rpc-server` over Tailscale, and are planned alongside Windows workers.
- **Headless Linux worker**: `racon --headless` joins the cluster and serves RPC tasks without a
  desktop session; `install.sh --service` registers a systemd user unit.
- **Cross-platform llama.cpp discovery**: bundled tools are found with or without `.exe`, Linux
  install directories (`/opt/racon/llama`, `/opt/llama.cpp/build/bin`) are searched, and
  `RACON_LLAMA_DIR` overrides the location. Bundled `.so` files are exposed via `LD_LIBRARY_PATH`.
- **Linux telemetry**: CPU brand from `/proc/cpuinfo`, GPU name from `nvidia-smi`/`lspci`, and
  `systemd-inhibit` sleep prevention for a Linux coordinator.
- **Dual-platform website**: the download section offers Windows and Ubuntu artifacts, detects the
  visitor's OS, and reads a unified `manifest.json` with per-platform SHA-256 values.
- **`scripts/publish_website.sh`**: publishes Linux artifacts and merges platform entries without
  removing the Windows download.
- **Zero-config join**: installed apps auto-enroll with a built-in join code, auto-approve, and start
  an RPC worker bound to their Tailscale IP. Operator controls in `docs/ZERO_CONFIG.md`, decision in
  ADR-015.
- **Automatic cluster discovery**: `racon.cluster serve` reads approved RPC workers from the coordinator
  admin API and replans on membership change; `cluster.json` workers are a fallback.
- **Per-node gateway keys**: issued once at enrollment and written to `gateway.json`, so chat works on
  PCs without a local model.
- **Persistent invites**: invite codes survive coordinator restarts
  (`%LOCALAPPDATA%\RACoN\racon\coordinator\invites.json`).
- **Autostart**: installer option creates a Startup shortcut; `RACoN.exe --autostart` runs in the tray.
- **Remote gateway chat**: PCs without a local GGUF model now answer through the public gateway when
  `RACON_API_KEY` (or `%LOCALAPPDATA%\RACoN\racon\gateway.json`) is configured. Helper:
  `scripts/configure_chat_gateway.ps1`.
- **Local model discovery**: chat looks for `RACON_GGUF_MODEL`, the project path, then downloaded models
  under `%LOCALAPPDATA%\RACoN\racon\models` instead of one hardcoded drive path.
- **Honest chat failures**: when every backend fails, the reply lists each backend and its error instead of
  a fake "Cluster is active" message.
- **Public website** (`website/`): static site with project overview, installer download with SHA-256 manifest, API
  docs and live health status. Served by nginx in Docker on `127.0.0.1:8081`.
- **Cloudflare Tunnel** routing for `racon.metademic.org` (`website/cloudflared/config.yml`,
  `scripts/website_tunnel.ps1`). Runbook: `docs/WEBSITE_CLOUDFLARE.md`.
- **Installer publishing** (`scripts/publish_website.ps1`): copies the current installer and writes its manifest.
- **Gateway rate limits**: 30 requests/min per client IP (counted before auth) and 60 requests/min per API key on
  `/v1/*`, returning `429` with `Retry-After`.
- **`max_tokens` cap** on the public gateway, set by `max_tokens_limit` in `cluster.json`.

### Changed
- `scripts/publish_website.ps1` now merges the Windows entry into the unified manifest instead of
  wiping the downloads folder, preserving the Linux release.
- `os_name` reported at enrollment is truncated to the protocol limit, fixing enrollment rejection
  on Linux kernel version strings.
- Tailscale address detection now matches the full `100.64.0.0/10` CGNAT range.
- The manual "Start Worker" path binds the detected Tailscale/LAN address instead of loopback.
- Linux device names such as `CPU:` are accepted by the head's `--list-devices` parser.
- Installer publisher URL points to `https://racon.metademic.org`.
- Rebuilt `RACoN.exe` and `RACoN_Setup_v0.1.0.exe` from current source.

## [0.1.0] - 2026-10-20
### Added
- **Production Architecture**: Restructured project layout into `apps/desktop` (Tauri v2 + React 18 / Vite / TypeScript), `backend/racon` (FastAPI + SQLAlchemy 2.0 + SQLite WAL), `cli` (Typer), `protocols`, and `docs`.
- **FastAPI Backend**:
  - Ephemeral token authentication middleware bound strictly to loopback `127.0.0.1`.
  - Comprehensive REST API endpoints: `/health`, `/api/v1/status`, `/api/v1/nodes`, `/api/v1/models`, `/api/v1/scheduler`, `/api/v1/jobs`, `/api/v1/benchmarks`, `/api/v1/logs`, `/api/v1/settings`.
- **Model Runtimes**:
  - Live Ollama runtime integration discovering installed models (`qwen3.5:0.8b`, `qwen2.5:1.5b`) and executing local prompt completions.
  - Distributed runtime protocol abstraction and task state machine (`queued -> validating -> scheduled -> running -> completed / failed / cancelled`).
- **Hardware Telemetry**:
  - Real-time CPU, RAM, and process RSS monitoring using `psutil` and Windows Registry.
  - Safe GPU detection with non-fabrication principles for Intel UHD 630.
- **Scheduler & Laya Advisor**:
  - Deterministic multi-objective scheduler (Lowest Latency, Highest Throughput, Resource Efficiency, Balanced).
  - Isolated Laya Router adapter supporting Deterministic, Shadow, and Laya-Assisted modes with automatic fallback.
- **Tauri v2 Desktop Interface**:
  - 8 fully functional technical panels: Overview, Nodes, Models, Scheduler Lab, Jobs, Benchmarks, Logs, Settings.
- **Companion CLI**:
  - Typer CLI with `racon status`, `racon node list`, `racon worker start/stop`, `racon models list`, `racon scheduler preview`, `racon benchmark run`, `racon jobs list`, `racon doctor`.
- **Persistence**:
  - SQLite database with WAL mode and foreign keys for durable local storage.
  - Optional Supabase cloud adapter configured without blocking offline operation.
