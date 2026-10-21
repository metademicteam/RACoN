# RACoN Desktop — MVP Acceptance Criteria & Gate Matrix

**Baseline:** RACoN Lab v0.1.0  
**Specification:** `e:\downloads\RACoN_Detailed_Architecture.md`  

---

## 1. Acceptance Gates

### Gate 0 — Baseline Locked
- **Objective:** Establish baseline code integrity and clean test environment.
- **Criteria:**
  - Automated tests run and cataloged.
  - No user data or repository artifacts at risk.
- **Status:** **PASSED**

### Gate 1 — Local Correctness & Safety Invariants
- **Objective:** Verify deterministic placement rules, hard constraints, Laya advisory engine, and concurrent database lifecycle.
- **Criteria:**
  - **Deterministic Correctness:** Enforce hard constraints (RAM capacity, device runtime, worker online/busy status).
  - **Laya Invariant:** In `LAYA_SHADOW` mode, deterministic choice is strictly preserved; in `LAYA_ASSISTED` mode, Laya recommendation is filtered against pre-validated `feasible_ids`. If invalid or failed, fallback to deterministic winner is guaranteed.
  - **Laya Integration:** Live `laya-0.4.12` `Router.predict` with `model="typed-decisions"` runs against candidates and records checkpoint name.
  - **Concurrency:** Thread-safe SQLite WAL connections with `busy_timeout=5000` and serialized write lock. QThread worker cancellation during benchmark runs completes without UI hang or database corruption.
- **Status:** **PASSED** (Verified with `tests/test_concurrency_and_lifecycle.py` and `tests/test_live_laya.py`)

### Gate 2 — Windows Packaging
- **Objective:** Standalone Windows one-folder distribution without requiring Python or dev tools.
- **Criteria:**
  - Build one-folder distribution `dist/RACoN_Lab/RACoN_Lab.exe`.
  - Launch application from outside the virtual environment.
  - Bundle JSON scenarios and default configurations.
  - Ensure `%LOCALAPPDATA%\RACoN` is used for user database and runtime logs.
  - Verify optional Laya imports safely without crashing if uninitialized.
- **Status:** **IN PROGRESS** (Building via PyInstaller `RACoN_Lab.spec`)

### Gate 3 — Live Local Worker Service
- **Objective:** Single live local worker process reporting genuine telemetry.
- **Criteria:**
  - Node identity, capability snapshot, heartbeat poller.
  - UI displays live local worker state instead of only scenario JSON.
- **Status:** Planned

### Gate 4 — Private LAN Control Plane
- **Objective:** Coordinator protocol over private LAN.
- **Criteria:**
  - Authenticated node registration over local network.
  - Capability freshness, heartbeat timeout, reservation locks.
- **Status:** Planned

### Gate 5 — Real Distributed Inference
- **Objective:** Verified multi-worker layer execution.
- **Criteria:**
  - Real workload split across verified worker runtimes with end-to-end latency benchmarks.
- **Status:** Planned

### Gate 6 — Broader Production Release
- **Objective:** Installer package (Inno Setup / MSI), digital signing, update/rollback.
- **Status:** Planned

---

## 2. Test Execution Matrix (Gate 0 & Gate 1)

| Test Module | Focus Area | Cases | Status |
|---|---|---|---|
| `tests/test_scheduler.py` | Deterministic scheduling, scoring, hard limits | 8 scenarios | **PASSED** |
| `tests/test_hardware.py` | Hardware discovery, non-fabrication of GPU | CPU/RAM/GPU | **PASSED** |
| `tests/test_storage.py` | SQLite schema, repository queries, migrations | Repositories | **PASSED** |
| `tests/test_concurrency_and_lifecycle.py` | Thread-local SQLite, 10-thread concurrent writes, QThread lifecycle | 4 cases | **PASSED** |
| `tests/test_live_laya.py` | Real neural Laya Router prediction (`typed-decisions`) | 3 cases | **PASSED** |
