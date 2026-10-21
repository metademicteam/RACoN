# RACoN Platform — Verification & Testing Guide

**Version:** 2.0  
**Updated:** 2026-10-21  

---

## 1. Test Architecture & Directory Structure

The automated test suite is segregated into discrete verification layers:

```
tests/
├── conftest.py                             # Test configuration & path isolation
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
└── test_live_laya.py                       # Opt-in neural Laya decision pipeline (typed-decisions)
```

---

## 2. Test Execution Commands

### Fast Unit & Hardening Suite (Default CI)
Runs all unit, protocol, concurrency, abuse, and integration tests (excluding heavy neural forward pass):
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v -m "not live_laya"
```

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
