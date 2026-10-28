# Changelog — RACoN Desktop

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
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
