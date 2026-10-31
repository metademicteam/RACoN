# Changelog — RACoN Desktop

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
