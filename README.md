# RACoN — Resource-Aware Collaborative Network

**Production-Oriented Local-First Distributed AI Compute Platform with Laya-Assisted Scheduling**

---

## 1. Overview
RACoN is a local-first platform designed to aggregate, manage, and coordinate CPU, GPU, and memory resources for distributed AI execution.
The platform integrates:
1. **Desktop Management Application**: Built with **Tauri v2 + React 18 (TypeScript, Vite, TailwindCSS)**.
2. **Local Coordinator Backend**: High-performance **FastAPI** service bound strictly to loopback (`127.0.0.1:8765`) protected by an ephemeral authorization token.
3. **Hardware Discovery**: Safe, non-fabricated telemetry via `psutil` and native Windows platform interfaces (Intel UHD 630 iGPU safe fallback).
4. **Laya Decision Advisor**: Integrated using official Laya `Router.predict` with typed choice decision primitives in **Deterministic**, **Shadow**, and **Laya-Assisted** modes.
5. **Model Runtimes**: Local model integration with **Ollama** (`127.0.0.1:11434`) and distributed tensor execution protocol abstraction.
6. **Local Persistence**: **SQLAlchemy 2.0 + SQLite WAL** with support for optional Supabase cloud syncing.
7. **Companion CLI**: Typer-based developer CLI (`racon status`, `racon node list`, `racon scheduler preview`, etc.).

---

## 2. Quick Start

### 2.1 Prerequisites
- **Windows 11 (64-bit)**
- **Python 3.12**
- **Node.js v20+**
- **Rust (rustc/cargo 1.80+)**
- *(Optional)* **Ollama** running locally at `http://127.0.0.1:11434`
- *(Optional)* `laya` package (`pip install laya`)

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

#### Option B: Launch Desktop via Tauri Dev
```powershell
# 1. Start FastAPI Coordinator Backend
.\.venv\Scripts\python.exe -m uvicorn backend.racon.api.app:app --host 127.0.0.1 --port 8765

# 2. In another terminal, launch Tauri v2 Desktop UI
cd apps\desktop
npm run tauri dev
```

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

All 80 unit and integration tests cover:
- FastAPI REST API v1 endpoints (health, status, nodes, models, scheduler preview, jobs, benchmarks, settings, logs).
- Hardware telemetry non-fabrication (Intel UHD 630 iGPU safe fallback).
- Ollama runtime inspection and execution error handling.
- Distributed runtime simulation labeling and task state machine transitions (`QUEUED` -> `VALIDATING` -> `SCHEDULED` -> `RUNNING` -> `COMPLETED`/`FAILED`).
- Worker manager local node registration and heartbeat timeout detection.
- Deterministic constraint checking and scoring formulas across all 4 objectives.
- Laya adapter lifecycle, timeout, and fallback.
- SQLite WAL durability and transactions across reconnects.

---

## 5. Architectural Principles

1. **System RAM is not GPU VRAM**: Resource checks explicitly account for device-specific memory constraints.
2. **Strict Non-Fabrication**: Integrated GPUs (e.g., Intel UHD 630) that do not expose standard queryable VRAM metrics report `Unavailable` rather than synthetic numbers.
3. **Laya Decision Scope**: Laya evaluates typed candidate decisions; it does not generate chat responses or override hard constraints.
4. **Local-First Security**: Internal APIs bind strictly to loopback (`127.0.0.1`) and require ephemeral authorization tokens.
5. **No Orphan Processes**: Native Tauri sidecar lifecycle ensures child Python and worker processes terminate cleanly upon exit.

---

## 6. Documentation Index
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Comprehensive system architecture and layer boundaries.
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — Multi-phase milestones and deliverables.
- [`docs/MVP_ACCEPTANCE.md`](docs/MVP_ACCEPTANCE.md) — Acceptance criteria and test matrix.
- [`docs/SECURITY.md`](docs/SECURITY.md) — Threat model and security policy.
- [`docs/LAYA_INTEGRATION.md`](docs/LAYA_INTEGRATION.md) — Laya decision engine integration details.
- [`docs/SCHEDULER.md`](docs/SCHEDULER.md) — Mathematical scoring rules and hard-constraint logic.
- [`docs/DISTRIBUTED_RUNTIME.md`](docs/DISTRIBUTED_RUNTIME.md) — Worker protocol and execution mode boundaries.
- [`docs/TAILSCALE_DOCKER.md`](docs/TAILSCALE_DOCKER.md) — Tailscale mesh remote access and Docker container deployment guide.
- [`docs/BUILD_WINDOWS.md`](docs/BUILD_WINDOWS.md) — Windows packaging and installer compilation.
- [`docs/ZERO_CONFIG.md`](docs/ZERO_CONFIG.md) — Auto-join, autostart, gateway keys and cluster discovery.
- [`CHANGELOG.md`](CHANGELOG.md) — Version history and release notes.
