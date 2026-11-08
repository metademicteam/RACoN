# RACoN Platform — Verification & Testing Guide

**Version:** 2.1
**Updated:** 2026-11-05

---

## 1. Test Architecture & Directory Structure

The automated test suite is segregated into discrete verification layers:

```
tests/
├── conftest.py                             # Test configuration, token, isolated DB fixture
├── test_protocols.py                       # Protocol models, size limits, Ed25519 signatures
├── test_coordinator_and_agent.py           # Loopback coordinator-agent lifecycle & dual listeners
├── test_abuse_and_hardening.py             # Fuzzing, expired invites, tampered sigs, heartbeat loss
├── test_remote_single_worker.py            # REMOTE_SINGLE_WORKER placement, cancellation, idempotency
├── test_update_manifest.py                 # Secure update manifest verification & SHA-256 checksums
├── test_concurrency_and_lifecycle.py       # Thread-local SQLite WAL concurrency, QThread shutdown
├── test_deterministic_scheduler.py         # Multi-objective placement scores & hard constraints
├── test_hardware.py                        # Hardware discovery & non-fabrication of GPU metrics
├── test_storage.py                         # SQLite schema persistence & repository transactions
├── test_api_endpoints.py                   # FastAPI REST API unit endpoints
├── test_backend_modules.py                 # Worker manager, state machine, runtime interfaces
├── test_orchestration.py                   # Planner, DAG, selector, retries, synthesis, jobs API
├── test_desktop_shell.py                   # Console-less startup, uvicorn logging, file diagnostics
├── test_config_paths.py                    # RACON_DB_PATH override (tests never touch user data)
├── test_recommendations.py                 # Hardware-based model recommendation & telemetry
├── test_engine_service.py                  # Engine metering and runtime binding
├── test_runtime_adapters.py                # llama.cpp and Ollama adapter contracts
├── test_model_manager.py                   # Download, verify, resume, disk guard
├── test_migrations.py                      # Versioned SQLite migrations
└── test_live_laya.py                       # Opt-in neural Laya decision pipeline (typed-decisions)
```

---

## 2. Test Execution Commands

### Fast Unit & Hardening Suite (Default CI)
Runs all unit, protocol, concurrency, abuse, and integration tests (excluding heavy neural forward pass):
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v -m "not live_laya"
```

### Orchestration Suite
Covers plan schema bounds, DAG cycle rejection, planner repair and fallback, model-written
task-id normalisation, the bounded planning timeout, dependency-ordered parallel execution,
retries, partial results, cancellation cleanup, restart recovery, and the HTTP job surface
end to end:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_orchestration.py -v
```

### Desktop Shell and Path Isolation
Verifies that a windowed (console-less) build can start the local engine while
`sys.stdout`/`sys.stderr` are `None`, that diagnostics reach the log file, and that
`RACON_DB_PATH` keeps tests away from the per-user database:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_shell.py tests/test_config_paths.py -v
```

### Recommendation Suite
Verifies that recommendations use measured RAM and real GGUF estimates:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recommendations.py -v
```

### Hugging Face Suite
Verifies repository validation, LFS SHA-256 extraction, gated-repo errors, built-in catalog
integrity, recommendation entries, and download routing:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_huggingface.py -v
```

### Live Hugging Face Verification
Checks the built-in entries against the live Hub, then optionally downloads one artifact
through the real model manager (SHA-256 verified, GGUF validated):
```powershell
.\.venv\Scripts\python.exe scripts\verify_huggingface.py
.\.venv\Scripts\python.exe scripts\verify_huggingface.py --download SmolLM2-360M-Instruct-Q4_K_M
```
Set `HF_TOKEN` in the environment for gated/private repositories. The token is never written
to disk or printed.

### Cloud Registry Suite
Verifies read-only Supabase status, key non-exposure, metadata mapping, and the API:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud.py -v
```

Live read-only check (reads `.env`; prints counts only):
```powershell
.\.venv\Scripts\python.exe scripts\check_supabase.py
```

### Release Builds
Windows bundle + installer:
```powershell
.\.venv\Scripts\python.exe -m PyInstaller RACoN.spec --noconfirm --clean
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\racon_desktop_setup.iss
```

Linux tarball + `.deb` (PyInstaller cannot cross-compile):
```powershell
.\scripts\build_linux_docker.ps1 -Version 1.1.0 -LlamaTag b12125
```

### Real End-to-End Orchestration Proof
Runs a real job through the HTTP API against `llama-server` and the repository GGUF. It uses a
temporary database and requires a working `llama-server`:
```powershell
.\.venv\Scripts\python.exe scripts\e2e_local_orchestration.py
.\.venv\Scripts\python.exe scripts\e2e_local_orchestration.py --decompose
```
Exit code `0` means a job reached `completed`/`partial` with a non-empty synthesized result.
The script prints the job record, per-task states, and the engine usage counters.

### Opt-In Live Neural Laya Suite
Tests live forward pass using `convaiinnovations/laya` `typed-decisions`:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_live_laya.py -v -s
```

### Protocol Contract Suite
Tests cryptographic key generation, serialization, size boundaries, and canonicalization:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_protocols.py -v
```

### Coordinator & Agent E2E Loopback Suite
Tests dual listeners (admin routes 404 on worker listener), enrollment, approval, and task execution:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_coordinator_and_agent.py -v
```

### Abuse & Failure-Injection Suite
Tests expired invites, tampered signatures, invite exhaustion, and heartbeat timeouts:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_abuse_and_hardening.py -v
```

### Remote Single-Worker Invariant Suite
Tests deadline expiration, cancellation non-overwriting, and idempotency deduplication:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_remote_single_worker.py -v
```

---

## 3. Evidence Catalog

Saved empirical test output and benchmark evidence are archived under `docs/evidence/`:

| Phase / Gate | Evidence File | Summary |
|---|---|---|
| **Phase 0 (Gate 0)** | [`docs/evidence/phase0_baseline/pytest_output.txt`](file:///f:/RACoN/docs/evidence/phase0_baseline/pytest_output.txt) | 86/86 baseline tests passed in 43.86s |
| **Phase 1 (Gate 1)** | [`docs/evidence/phase0_baseline/live_laya_output.txt`](file:///f:/RACoN/docs/evidence/phase0_baseline/live_laya_output.txt) | 3/3 live Laya neural tests passed in 12.61s |
| **Phase 3 (Gate 3)** | [`docs/evidence/phase3_loopback/test_results.txt`](file:///f:/RACoN/docs/evidence/phase3_loopback/test_results.txt) | 6/6 protocol and loopback lifecycle tests passed in 3.88s |
| **Phase 4 (Gate 4)** | [`docs/evidence/phase4_hardening/abuse_test_results.txt`](file:///f:/RACoN/docs/evidence/phase4_hardening/abuse_test_results.txt) | 4/4 abuse and hardening tests passed in 1.00s |
| **Phase 5 (Funnel)** | [`docs/measurements/TAILSCALE_FUNNEL_BENCHMARKS.md`](file:///f:/RACoN/docs/measurements/TAILSCALE_FUNNEL_BENCHMARKS.md) | Empirical crypto, serialization, RTT, and power plan measurements |
| **Phase 6 (Tasks)** | [`docs/evidence/phase6_remote_tasks/remote_worker_results.txt`](file:///f:/RACoN/docs/evidence/phase6_remote_tasks/remote_worker_results.txt) | 3/3 remote single worker invariant tests passed in 4.11s |
| **Phase 7 (Pipeline)**| [`docs/evidence/phase7_pipeline/pipeline_runtime_evaluation.txt`](file:///f:/RACoN/docs/evidence/phase7_pipeline/pipeline_runtime_evaluation.txt) | ADR-012 distributed pipeline boundary & LAN restriction |
