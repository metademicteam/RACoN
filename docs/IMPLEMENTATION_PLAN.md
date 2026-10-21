# RACoN Desktop — Implementation Plan

**Baseline:** RACoN Lab v0.1.0  
**Specification:** `e:\downloads\RACoN_Detailed_Architecture.md`  

---

## 1. Project Goal & Architecture
RACoN is a local-first distributed AI compute platform. In **RACoN Lab v0.1.0**, it is structured as a **modular monolith** desktop application in **Python and PySide6 (Qt)**, persisting to SQLite (WAL mode), with hardware discovery and deterministic multi-objective scheduling assisted by the optional **Laya decision engine** (`typed-decisions`).

---

## 2. Immediate 10-Step Action Plan (from Section 18 of Architecture Document)

1. **Lock baseline and verify environment**
   - Run automated test suite; catalog dependencies.
   - [x] Completed.

2. **Upgrade Laya adapter to official typed decision flow**
   - Use `Router.predict(..., model="typed-decisions")` per official Laya documentation.
   - Record `laya_checkpoint` in `SchedulerResult` and decision audit logs.
   - [x] Completed.

3. **Verify scheduler engine behavior**
   - Ensure `LAYA_SHADOW` mode strictly preserves deterministic choice.
   - Ensure `LAYA_ASSISTED` mode strictly validates against `feasible_ids` and guarantees deterministic fallback.
   - [x] Completed.

4. **Harden UI thread and SQLite concurrency**
   - Implement thread-local SQLite connections (`threading.local()`) with `PRAGMA busy_timeout=5000` and `PRAGMA journal_mode=WAL`.
   - Add serialized write lock to prevent write contention.
   - Add graceful cancellation and wait logic (`cancel_or_wait(timeout_ms=3000)`) in `BenchmarkService` and connect to `MainWindow.closeEvent`.
   - [x] Completed.

5. **Create automated concurrency and live Laya tests**
   - Verify concurrent multi-thread database writes.
   - Verify double-click prevention (`is_busy` guard).
   - Test live Laya Router predictions in opt-in test suite (`test_live_laya.py`).
   - [x] Completed.

6. **Prepare Windows packaging**
   - Create `RACoN_Lab.spec` for PyInstaller one-folder release distribution.
   - Ensure scenarios JSON, PySide6 plugins, and dependencies are cleanly bundled.
   - [x] In Progress (Task 1970).

7. **Test standalone execution**
   - Launch `dist\RACoN_Lab\RACoN_Lab.exe` from an external path without activating `.venv`.
   - Verify scenario loading, hardware discovery, and SQLite database creation under `%LOCALAPPDATA%`.
   - [ ] Pending task 1970 completion.

8. **Update desktop shortcut and launcher**
   - Point `C:\Users\Haque\Desktop\RACoN Desktop.lnk` to `dist\RACoN_Lab\RACoN_Lab.exe`.
   - Update `run.bat` and `run.ps1` for development vs. production launches.
   - [ ] Pending task 1970 completion.

9. **Benchmark scheduler and document findings**
   - Record decision latency across deterministic, shadow, and assisted modes.
   - Document memory footprint (~1.8 GB during active Laya prediction) and latency tradeoffs.
   - [ ] Ready to run.

10. **Plan Gate 3 (Live Local Worker Service)**
    - Implement real local worker agent process reporting live telemetry, keeping simulation explicitly labeled.
    - [ ] Next milestone.
