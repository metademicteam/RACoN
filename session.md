# RACoN session handoff — 2026-11-08 (updated)

**Project:** `F:\RACoN` (branch `main`, version `1.2.0`)
**Status:** RACoN Network Gateway implemented and live-verified; client installers rebuilt with
the streaming worker.

---

## RACoN Network Gateway — 2026-11-08

**Goal:** any agent harness points at one OpenAI-compatible URL with a RACoN key. A query
streams from one worker; a task is planner-decomposed across workers, streamed, and merged.

### Implemented

New package `backend/racon/network/`:
- `api_keys.py` — DB-backed `rk_` keys, SHA-256 at rest, scopes, rate limits, revocation.
- `ratelimit.py` — per-key sliding window.
- `intent.py` — deterministic query/task classifier; `racon-single`, `racon-task`,
  `racon-single:<model>`, `racon-task:<model>` aliases.
- `catalog.py` — `racon` aliases + online worker models + local fallback models.
- `broker.py` — task/job delta fan-out with bounded replay.
- `gateway.py` — target selection, single-worker streaming, retry-before-first-token,
  task stream with `task_delta` + synthesis, non-stream aggregation.

Key changes elsewhere:
- `backend/racon/api/app.py` — `rk_`/`x-api-key` on `/v1/*`, OpenAI error envelope,
  rate-limit headers, CORS.
- `backend/racon/api/routes_keys.py` — admin key CRUD.
- `backend/racon/api/routes_engine.py` — network routing, tools/tool_choice/response_format,
  content parts, tool-call SSE.
- `backend/racon/api/routes_worker.py` — `POST /tasks/progress`.
- `racon/coordinator/tasks.py` — progress sequence + ring buffer.
- `racon/agent/worker.py` — `chat_completion` streaming via Ollama (and engine fallback);
  advertises `streaming_chat`; normalized tool calls.
- `protocols/models.py` — `TaskType.CHAT_COMPLETION`, `TaskProgressMessage`.
- `backend/racon/orchestration/*` — `submit_stream`, job/task delta publishing, streamed
  dispatcher, planner/synthesis model separation, per-task budget, streaming-worker filter,
  active-task placement penalty.
- `racon/runtime/*` — tool-aware messages/chunks/results, tool/response_format forwarding,
  Ollama tool-call normalization.
- Admin console: **API keys** tab.
- Docs: `docs/RACON_NETWORK.md`, `docs/API_SPECIFICATION.md`, `ARCHITECTURE.md`,
  `CHANGELOG.md`, website API section.

### Live verification (local coordinator + source worker)

| Check | Result |
|---|---|
| `POST /api/v1/admin/keys` | key created, plaintext once, hash stored |
| `GET /v1/models` with key | `racon`, aliases, worker/local models |
| explicit model non-stream | `Hello! It's a pleasure to meet you.` via `node-7837bbc45722`, usage 36/11 |
| explicit model stream | 15 SSE frames, real token deltas, usage, `[DONE]` |
| auto `racon` quick | selected `node-7837bbc45722`, mode `single` |
| `racon-task:qwen2.5:0.5b` | planner `llm`, 2 tasks (`compare-inference`, `recommendation`), 40 task deltas + 106 content deltas, final `status: completed` |
| tool call non-stream | `finish_reason: tool_calls`, OpenAI shape, arguments JSON string |
| tool call stream | `delta.tool_calls` + `finish_reason: tool_calls` |
| unit/integration | full suite **372 passed, 2 skipped, 3 deselected** |

### Deployment state

- Coordinator image rebuilt with gateway + API-key + admin UI + Ollama planner engine.
- `.env` now sets `RACON_RUNTIME=ollama` so planner/synthesis use the host's
  `qwen2.5:1.5b` (the coordinator still falls back to GGUF if the runtime is switched back).
- Workers must advertise `streaming_chat`. The Windows source worker did; the Linux
  `node-ac57a79d14c4` still runs an old build and is excluded from streamed routing until
  updated.
- Windows and Linux were rebuilt with the gateway/streaming worker and published:
  - Windows `79,609,070` bytes, SHA-256 `c23c29b69ae58bcd40c361b1094aa796036cdf5eb843e78e94be7744e2f1b422`
  - Linux `102,616,268` bytes, SHA-256 `805c80ccc73960735c2fe4cf18363727935cdda2b60e17084567f2931bdb11ce`
  - `.deb` `79,490,420` bytes, SHA-256 `8eff389c05afaa0258a98db8357d0121a5256f19085de7ddc0578811b7606e81`
- All three were downloaded from the public site and hash-verified.
- The installed Windows app is running and advertises `streaming_chat`; it served the final
  live `racon` request through the public Funnel URL.
- All temporary `live-*` test keys in the coordinator DB were revoked (0 active).

### Remaining work

1. Update the Linux worker (reinstall the new tarball) so it advertises `streaming_chat`
   and can receive task shards.
2. Planner quality: the coordinator's Ollama 1.5B produced a valid 2-task plan; with only the
   bundled 0.5B GGUF the planner honestly falls back to one task. A 4B+ planner is recommended
   for complex plans.
3. Not yet implemented: `/v1/completions`, `/v1/responses`, `/v1/embeddings`, Anthropic
   `/v1/messages`; P2P/Nostr discovery (v1 is coordinator-mediated); cross-coordinator
   federation; per-key billing.
4. Task cancellation still stops the coordinator stream but does not yet send a cancel command
   to a worker mid-generation.

---

---

## Completed in this session

### 1. Docker port conflict fixed
- Cause: three orphan containers from an old Oct 19 compose definition
  (`racon-coordinator`, `racon-ui`, `racon-tailscale`) were still in the `racon` project.
  The old `racon-coordinator` (image `racon-racon-backend`) retried `0.0.0.0:8765` and
  returned `Bind for 0.0.0.0:8765 failed: port is already allocated`.
- Removed the orphans. The live `racn-*` stack was left untouched.
- `docker compose up -d` now reconciles cleanly; `racn-coordinator` is healthy.
- `racon-tailscale` was unauthenticated and not serving traffic. The public URL comes from the
  **host** Tailscale Funnel: `https://desktop-b7l73cl.tail6cb521.ts.net -> 127.0.0.1:8765`.

### 2. Windows installer install fixed
- `C:` was low on space. Docker build cache and orphan cleanup freed space; install now needs
  ~310 MB plus temp space.
- Installer builds and installs with exit code `0`.
- The real install is at `%LOCALAPPDATA%\Programs\RACoN` and the uninstall key points there.
- Installed frontend asset: `index-BE11l2Kd.js` (verified in `index.html`).

### 3. Scoped client token implemented
Separate credentials now exist:

| Token | Env var | Scope |
|---|---|---|
| Administrator | `AUTH_TOKEN` | Everything, including `/api/v1/admin/*` and settings writes |
| Client | `RACON_CLIENT_TOKEN` | `/api/v1/*` and `/v1/*` except admin routes and `PUT /api/v1/settings` |

Behavior:
- Client token on an admin route returns `403 forbidden`; unknown/old token returns `401`.
- Release builds compile **only the client token** via `VITE_DEFAULT_AUTH_TOKEN`.
- The old `racon-local-dev-token` is no longer accepted and is discarded from upgraded
  installs' local storage.
- Docker Compose fails closed when `AUTH_TOKEN` is missing.
- Removed the hardcoded token from the standalone `Dockerfile`; helper scripts read `.env`.
- Admin console copy now explicitly says administrator token.

Live verification through the public URL:

| Request | Result |
|---|---|
| client -> `/api/v1/status` | `200` |
| client -> `/api/v1/admin/overview` | `403` |
| admin -> `/api/v1/admin/overview` | `200` |
| old dev token -> `/api/v1/status` | `401` |
| no token -> `/api/v1/status` | `401` |

The public web bundle contains the client token and **not** the admin token.

The admin token is stored only in `F:\RACoN\.env` (gitignored). Do not paste it into the
website, docs, installers, or chat.

### 4. Artifacts rebuilt and published

| File | Size | SHA-256 |
|---|---|---|
| `RACoN_Setup_v1.2.0.exe` | 79,609,070 | `c23c29b69ae58bcd40c361b1094aa796036cdf5eb843e78e94be7744e2f1b422` |
| `RACoN-linux-x86_64-v1.2.0.tar.gz` | 102,616,268 | `805c80ccc73960735c2fe4cf18363727935cdda2b60e17084567f2931bdb11ce` |
| `racon_1.2.0_amd64.deb` | 79,490,420 | `8eff389c05afaa0258a98db8357d0121a5256f19085de7ddc0578811b7606e81` |

- Downloaded all three from `https://racon.metademic.org/downloads/` and hashes match.
- Manifest: `F:\RACoN\dist\website_downloads\manifest.json` (Windows + Linux 1.2.0).
- Website release notes now include the scoped-token change.
- Website container `racon-site` rebuilt and healthy.

### 5. Tests
- `tests/test_token_scopes.py`: 7 passed.
- `tests/test_api_endpoints.py`, `tests/test_admin_and_worker_routes.py`,
  `tests/test_cloud.py`: 30 passed.
- Full suite was started after these changes; check the latest run before release use.

---

## Source changes from this session

Modified:
- `backend/racon/core/config.py` — `CLIENT_TOKEN` setting.
- `backend/racon/security/__init__.py` — client token helpers and scope resolution.
- `backend/racon/api/app.py` — admin-only path/method enforcement.
- `apps/desktop/src/api.ts` — client token default, legacy token migration.
- `apps/desktop/src/App.tsx` — token placeholder copy.
- `docker-compose.yml` — requires `AUTH_TOKEN`, passes `RACON_CLIENT_TOKEN`.
- `.env.example` — admin + client placeholders.
- `Dockerfile` — removed hardcoded `AUTH_TOKEN`.
- `scripts/start_server.ps1`, `scripts/open_admin.ps1`, `scripts/probe_server.py`,
  `scripts/e2e_local_orchestration.py`, `scripts/tailscale_info.ps1`,
  `cli/racon_cli.py` — read tokens from env/`.env`.
- `backend/racon/admin_ui/index.html` — administrator-token wording.
- `tests/conftest.py` — pins `AUTH_TOKEN` over a developer `.env`.
- `CHANGELOG.md`, `website/index.html`.

New:
- `tests/test_token_scopes.py`
- `session.md` (this file)

Local secrets:
- `F:\RACoN\.env` contains `AUTH_TOKEN` and `RACON_CLIENT_TOKEN` (gitignored).

---

## Server connection details

| Item | Value |
|---|---|
| Public coordinator | `https://desktop-b7l73cl.tail6cb521.ts.net` |
| Tailnet coordinator | `http://100.76.208.54:8765` |
| Join code | `RCN-PUBLIC-JOIN` |
| Admin URL | `https://desktop-b7l73cl.tail6cb521.ts.net/admin` |
| Admin token | `AUTH_TOKEN` in `F:\RACoN\.env` |
| Client token | `RACON_CLIENT_TOKEN` in `F:\RACoN\.env` |
| Auto-approve workers | `true` |
| Engine models | 2 GGUF installed |

Running containers:
- `racn-coordinator` — healthy
- `racn-webapp` — up
- `racn-postgres` — healthy
- `racn-llama` — healthy
- `racn-model-registry` — healthy
- `racon-site` — healthy

---

## Addendum — worker inventory hardware fix (2026-11-08)

Problem reported: the Linux node was missing from the admin inventory, and remembered
nodes showed dummy hardware (`Unknown`, no OS, no runtimes) even while the Linux app ran.

Root causes:
1. `get_nodes()` never returned capability fields; `_merge_nodes()` therefore fell back to
   `Unknown` for every persisted node.
2. `upsert_node()` only wrote capabilities when a node row was first created, so refreshes
   (OS, GPU, runtimes, models) were discarded.
3. `node_capabilities` had no columns for OS, RPC endpoint, models or features.
4. The desktop shell started only the local engine; it never enrolled or heartbeated as a
   provider, so a running GUI app could not appear online.
5. `netdefaults` defaulted workers to `127.0.0.1:8765`, which fails on a remote Linux box.

Fixes:
- Capabilities are refreshed on every upsert and returned by `get_nodes()`/`get_node()`.
- Added `os_name`, `rpc_endpoint`, `models_json`, `features_json`; `init_db` adds them to an
  existing database.
- Friendly OS labels (`Ubuntu 24.04.4 LTS`, `Windows 10.0.x`) from `/etc/os-release`.
- `/api/v1/nodes` (the client endpoint used by the desktop app) now applies the same live
  heartbeat check as the admin API, so remembered nodes no longer all report "available"
  (the Overview used to show "5 Ready" with one online machine).
- The desktop shell now starts `RaconAgent` in the background (`RACON_AUTO_CONNECT=0`
  disables it), so the app enrolls and heartbeats.
- Coordinator URL resolution: explicit env -> reachable local coordinator -> public server.
- Verified from source and from the installed Windows app: the node joins as
  `node-7837bbc45722`, heartbeats every few seconds and the admin shows the real i3-10100,
  Intel UHD 630, 4 cores / 16 GB and `ollama,local` runtimes.

Follow-up reported after the first rebuild: the desktop Overview showed the Docker
container's hardware (Debian, 7.7 GB) instead of the Windows machine.

Additional fixes:
- The Overview hardware card and header CPU/RAM now call the **local engine** at
  `window.location.origin` (`getLocalEngineStatus`) and prefer that data. The remote
  `/api/v1/status` is still used for cluster health and task data.
- `racon.desktop.resolve_token` now exports both `RACON_AUTH_TOKEN` and `AUTH_TOKEN`, so a
  project `.env` cannot override the per-install launch token. Before this the local engine
  rejected the token in the window URL.
- Verified with the installed Windows build in a real browser: CPU i3-10100,
  `Windows 10.0.26340 AMD64`, 15.9 GB RAM, Intel UHD 630, 8 logical cores.

Upgrade note: the worker agent leaves `rpc-server` running if the app is force-killed, and
Restart Manager cannot close it, so an installer run can abort with exit code 5. Stop
`rpc-server` (or close the app normally so `agent.stop()` reaps it) before an in-place
install. A job-object kill-on-close guard is still a follow-up.

Linux: the machine must run the rebuilt Linux app (or `racon --headless`) to enroll; an
old running binary cannot be updated in place from this PC.

## Remaining known gaps

- The installer's "Start RACoN when Windows starts" shortcut runs `RACoN.exe --autostart`,
  which now enrolls and heartbeats as a worker, but still opens the desktop window instead
  of starting hidden in the tray.
- The desktop shell still starts an unused local engine even when the UI is connected to the
  remote server.
- First network token latency is ~25–60 s on the 0.5B CPU model because the task executes
  before synthesis streams.
- The Linux `.deb`/tarball package the same frontend bundle as Windows; verify on a clean
  Ubuntu machine before wider distribution.

---

## Useful commands / paths

- Server health:
  ```powershell
  Invoke-RestMethod http://127.0.0.1:8765/health
  ```
- Admin token from `.env` (PowerShell):
  ```powershell
  (Get-Content .env | Where-Object { $_ -match '^AUTH_TOKEN=' }) -replace '^AUTH_TOKEN=',''
  ```
- Server logs:
  ```powershell
  docker logs racn-coordinator --tail 100
  ```
- App log:
  `C:\Users\Haque\AppData\Local\RACoN\racon\Logs\racon.log`
- Installer log:
  `%TEMP%\racon_install_final.log`
- Published downloads:
  `F:\RACoN\dist\website_downloads`
- Linux build log:
  `F:\RACoN\.pytest_cache\linux_build.log`
