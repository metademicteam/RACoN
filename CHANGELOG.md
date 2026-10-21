# Changelog — RACoN Desktop

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
