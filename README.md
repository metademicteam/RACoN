# RACoN — Resource-Aware Collaborative Network

**Production-Oriented Local-First Distributed AI Compute Platform with Laya-Assisted Scheduling**

---

## 1. Overview
RACoN is a distributed AI inference network for **Windows and Ubuntu/Linux**. Providers host
models on their own machines; consumers call an OpenAI-compatible API and RACoN routes each
request to a machine that can serve it.

The platform integrates:

1. **Desktop Application** — React 18 + Vite UI hosted by `racon/desktop.py` in a native
   pywebview window (Edge WebView2 on Windows, WebKitGTK/Chromium on Linux). PySide6 remains
   for the `--lab` window, the `--autostart` tray and the `--headless` provider agent.
2. **Friend AI** — the primary interface. A local persona streams from the on-device runtime;
   a **RACoN network** mode submits a real orchestration job and renders the plan, per-task
   workers, live state, and synthesized answer, with explicit partial/failed reporting.
   First launch offers a verified model chosen from measured hardware.
3. **Local Engine** — FastAPI bound to loopback, protected by a per-install token. Serves the
   UI, the OpenAI-compatible `/v1/*` surface and the management API on a single port.
4. **Runtime Adapters** — a single `InferenceRuntime` contract with real **llama.cpp** and
   **Ollama** implementations. Streaming, cancellation and token accounting are uniform.
5. **Model Manager** — discovery, HTTPS-only download, SHA-256 verification, disk guards and
   memory estimates derived from real GGUF header geometry. Hardware-ranked downloads come
   from Hugging Face (`SmolLM2 360M`, `Qwen 2.5 0.5B`, `Llama 3.2 1B`) and Ollama tags.
6. **Hardware Discovery** — `psutil` plus platform interfaces. Values that cannot be measured
   (integrated-GPU VRAM, for example) are reported as unavailable, never invented.
7. **Agentic Orchestration** — a server-side planner decomposes a request into a validated
   task DAG, a weighted selector places each task on a suitable worker, results are validated,
   and a synthesis agent merges them while naming anything that could not be completed.
8. **Schedulers** — a deterministic scorer today (Laya-assisted as an optional adviser), with
   the orchestration selector as the job-aware placement layer.
9. **Companion CLI** — Typer-based administration client (`racon status`, `racon doctor`, …).
10. **Server mode & operator console** — one Docker container serves the worker ingress
    (`/enroll`, `/heartbeat`, `/tasks/*`), the desktop API and a standalone admin dashboard at
    `/admin`. PostgreSQL stores durable node, job, task and routing state. Workers reach the
    server over Tailscale.
11. **Smart model router & diagnostics** — `model: "auto"` classifies a request
    (code/reasoning/chat/tool/creative/image; quick/moderate/deep), applies hard capability
    filters, and selects an installed model with a fully recorded decision. Workers advertise
    their model inventory; stale heartbeats are excluded. The admin **Diagnostics** tab checks
    PostgreSQL, runtimes, models, workers, routing and token strength.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for module ownership and data flow.

---

## Server deployment (this PC as the server)

The container is the deployable unit. It runs the unified FastAPI app, PostgreSQL and the
web UI together:

```powershell
Copy-Item .env.example .env    # set AUTH_TOKEN and POSTGRES_PASSWORD
.\scripts\start_server.ps1
```

| Surface | URL |
|---|---|
| Desktop web UI | `http://<tailscale-ip>:3000/?token=<AUTH_TOKEN>` |
| Admin dashboard | `http://<tailscale-ip>:8765/admin` |
| Worker ingress | `http://<tailscale-ip>:8765` |

A remote provider joins with:

```powershell
$env:RACON_COORDINATOR_URL = "http://<tailscale-ip>:8765"
$env:RACON_JOIN_CODE = "RCN-PUBLIC-JOIN"
.\scripts\start_worker.ps1 -Server $env:RACON_COORDINATOR_URL -Headless
```

Full instructions, firewall rules, PostgreSQL backup and troubleshooting:
[`docs/SERVER_DEPLOYMENT.md`](docs/SERVER_DEPLOYMENT.md).

---

## 2. Quick Start

### 2.1 Prerequisites
- **Windows 11 (64-bit)** or **Ubuntu 24.04+ (x86_64)**
- **Python 3.12**
- **Node.js v20+** — only to rebuild the UI
- **llama.cpp** with `llama-server` (e.g. `winget install ggml.llamacpp`, or a build under
  `/opt/racon/llama`)
- *(Optional)* **Ollama** at `http://127.0.0.1:11434`
- *(Optional)* **Tailscale** to connect machines over a private mesh
- *(Optional)* `laya` package for the advisory scheduler

---

### 2.2 Launching the Application

#### Option A: One-Click Windows Launcher
```cmd
run.bat
```
or via PowerShell:
```powershell
.\run.ps1
```

#### Option B: Product desktop shell (Windows and Linux)
```powershell
.\.venv\Scripts\python.exe -m racon.desktop
```
This starts the engine on a free loopback port and opens the product UI. It is the same entry
point the installer uses.

#### Option C: Ubuntu / Linux
```bash
./scripts/build_linux.sh
tar -xzf dist/installer/RACoN-linux-x86_64-v1.1.0.tar.gz
cd RACoN-linux-x86_64-v1.1.0
./install.sh
racon                 # product desktop shell
racon --lab           # PySide6 laboratory window
racon --headless      # Ubuntu Server provider agent
```
See [`docs/BUILD_LINUX.md`](docs/BUILD_LINUX.md) for llama.cpp, `.deb` packaging, Tailscale and
cross-OS cluster setup.

---

## 3. Companion CLI Usage

The companion CLI provides terminal-based administration against the local coordinator:

```powershell
# Check cluster status and hardware gauges
.\.venv\Scripts\python.exe cli\racon_cli.py status

# Run system diagnostic checks
.\.venv\Scripts\python.exe cli\racon_cli.py doctor

# List registered compute nodes
.\.venv\Scripts\python.exe cli\racon_cli.py node list

# List local Ollama models
.\.venv\Scripts\python.exe cli\racon_cli.py models list

# Run a scheduler preview over test scenarios
.\.venv\Scripts\python.exe cli\racon_cli.py scheduler preview --scenario "Multiple Feasible Candidates" --objective balanced --mode deterministic

# Run and record a benchmark
.\.venv\Scripts\python.exe cli\racon_cli.py benchmark run --scenario "High Latency Network" --objective lowest_latency
```

---

## 4. Testing & Verification

Run the full automated test suite:
```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```

All 286 selected tests (3 live-Laya tests are opt-in) cover:
- Agentic orchestration: plan schema bounds, DAG validation and cycle rejection, planner repair and single-task fallback, dependency-ordered parallel execution, retries, partial results, cancellation, restart recovery, and the HTTP job surface end to end.
- Worker selection: hard constraints, weighted scoring, configurable weights, and honest reporting of unmeasured telemetry.
- Runtime adapter contracts for llama.cpp and Ollama — streaming, cancellation, real usage reporting, crash classification.
- Model manager: SHA-256 verification, disk guard, interrupted downloads, refusal of non-HTTPS or unverified sources.
- SQLite migrations, including upgrading a legacy database without data loss.
- FastAPI REST API v1 endpoints (health, status, nodes, models, scheduler preview, jobs, benchmarks, settings, logs).
- OpenAI-compatible `/v1/models` and `/v1/chat/completions`, including SSE framing and authentication.
- Hardware telemetry non-fabrication (integrated-GPU VRAM reported unavailable).
- Worker task state machine, including the invariant that a worker with no runtime reports a failure instead of a placeholder result.
- Deterministic constraint checking and scoring formulas across all 4 objectives.
- Laya adapter lifecycle, timeout, and fallback.
- SQLite WAL durability and transactions across reconnects.

---

## 5. Architectural Principles

1. **System RAM is not GPU VRAM**: resource checks account for device-specific memory.
2. **Strict Non-Fabrication**: anything that cannot be measured is reported as unavailable. There is no simulated inference path in production code.
3. **Laya/agentic scope**: planners propose; deterministic validation decides. They cannot bypass quotas, permissions or hardware limits.
4. **Local-First Security**: the engine binds to loopback with a per-install token. No shared default token ships in any build.
5. **No Orphan Processes**: the runtime adapter terminates `llama-server` and closes its log handle on unload, error and shutdown.

---

## 6. Documentation Index
- [`docs/SERVER_DEPLOYMENT.md`](docs/SERVER_DEPLOYMENT.md) — **server mode**: Windows host, Tailscale, PostgreSQL, Docker, admin dashboard and worker enrollment.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — **current** module ownership, data flow and failure behaviour, including the orchestration layer.
- [`docs/PHASE1_AUDIT.md`](docs/PHASE1_AUDIT.md) — evidence-based repository audit (2026-11-05).
- [`docs/DEVELOPMENT_ROADMAP.md`](docs/DEVELOPMENT_ROADMAP.md) — phased plan with status and acceptance criteria.
- [`docs/TASK_EXECUTION_PROTOCOL.md`](docs/TASK_EXECUTION_PROTOCOL.md) — job/task DAG lifecycle, dispatch, validation and synthesis rules.
- [`docs/WORKER_PROTOCOL.md`](docs/WORKER_PROTOCOL.md) — enrollment, heartbeat, claim, signing and result reporting.
- [`docs/MODEL_REGISTRY.md`](docs/MODEL_REGISTRY.md) — catalog format, install guarantees, memory estimates, shard manifest.
- [`docs/HUGGINGFACE_MODELS.md`](docs/HUGGINGFACE_MODELS.md) — verified Hugging Face downloads, token handling, built-in artifacts.
- [`docs/API_SPECIFICATION.md`](docs/API_SPECIFICATION.md) — every HTTP surface, auth, limits and job schemas.
- [`docs/DEVELOPMENT_STATUS.md`](docs/DEVELOPMENT_STATUS.md) — what is done, verified, broken and next.
- [`docs/SUPABASE_SCHEMA.md`](docs/SUPABASE_SCHEMA.md) — read-only snapshot of the shared control-plane schema.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — historical hub-and-spoke design and ADR-001…015.
- [`docs/SECURITY.md`](docs/SECURITY.md) — threat model and security policy.
- [`docs/SCHEDULER.md`](docs/SCHEDULER.md) — scoring rules and hard-constraint logic.
- [`docs/LAYA_INTEGRATION.md`](docs/LAYA_INTEGRATION.md) — Laya decision engine integration details.
- [`docs/CLUSTER_LAYER_SCHEDULING.md`](docs/CLUSTER_LAYER_SCHEDULING.md) — Stage-2 layer split policy.
- [`docs/RPC_LINK_MEASUREMENTS.md`](docs/RPC_LINK_MEASUREMENTS.md) — measured RPC throughput and its consequences.
- [`docs/BUILD_WINDOWS.md`](docs/BUILD_WINDOWS.md) — Windows packaging and installer compilation.
- [`docs/BUILD_LINUX.md`](docs/BUILD_LINUX.md) — Ubuntu/Linux build, install, headless worker and cross-OS cluster.
- [`docs/ZERO_CONFIG.md`](docs/ZERO_CONFIG.md) — auto-join, autostart, gateway keys and cluster discovery.
- [`CHANGELOG.md`](CHANGELOG.md) — version history and release notes.
