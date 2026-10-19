# RACoN Lab Testing Strategy

## Test Suite Execution

Run the complete test suite:
```powershell
python -m pytest tests/ -v
```

## Coverage Breakdown
1. **Deterministic Scheduler (`test_deterministic_scheduler.py`)**:
   - Single valid candidate selection.
   - Rejection on insufficient RAM.
   - Rejection on incompatible device runtime.
   - Rejection on offline/unavailable status.
   - Rejection on 100% full workload.
   - Handling no feasible candidates safely without forcing placements.
   - Handling empty candidate lists.
   - Exact scoring reproducibility across multiple executions.
   - Objective diversity (latency vs. throughput divergence).
   - Validation that highest-scoring candidate is chosen.
   - Score component dictionary population.
   - Verification across all 4 scheduling objectives.
   - Proper segregation in mixed feasible/infeasible scenario.

2. **Laya Adapter (`test_laya_adapter.py`)**:
   - Starting in `UNINITIALIZED` state.
   - Clean handling of missing `laya` package (`UNAVAILABLE`).
   - Clean handling of initialization exceptions (`FAILED`).
   - Mocked successful initialization setting `READY`.
   - Rejection of returned invalid candidate IDs.
   - Immediate 1-candidate shortcut optimization.
   - Prevention of inference calls when uninitialized.
   - Safe error handling on model predict errors.
   - Resource unloading and shutdown lifecycle.

3. **Hardware Discovery (`test_hardware.py`)**:
   - Non-empty CPU name and positive core counts.
   - Real total and available system memory.
   - Non-fabricated GPU metrics for integrated Intel graphics (None/Unavailable).
   - Resilience against discovery failures (never raises).
   - Lightweight periodic metrics collection without blocking.

4. **Storage & Persistence (`test_storage.py`)**:
   - SQLite table creation and schema validation.
   - Saving and retrieving benchmark runs with candidate evaluation breakdowns.
   - Key-value application settings persistence and updates.
   - CSV and JSON benchmark run export.
   - Run history clearance.
   - Data persistence across database reconnection cycles.
