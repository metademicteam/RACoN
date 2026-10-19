# RACoN Lab v0.1.0

**Resource-Aware Compute Orchestration Network — Laboratory Edition**

RACoN Lab is an experimental desktop application for testing resource-aware scheduling decisions. It demonstrates real hardware discovery, deterministic scheduling, and optional Laya-assisted AI scheduling decisions.

> **Version 0.1 Prototype** — This version does not implement real distributed LLM inference. Scheduling scenarios use simulated worker candidates, clearly labeled as such. No model execution occurs across multiple PCs.

## Features

- **Real Hardware Discovery** — Detects CPU, RAM, GPU, and system information using `psutil` and system interfaces
- **8 Predefined Scheduling Scenarios** — Tests ranging from single feasible worker to no-feasible-solution edge cases
- **4 Scheduling Objectives** — Lowest latency, highest throughput, resource efficiency, and balanced
- **3 Scheduling Modes** — Deterministic, Laya Shadow (advisory), and Laya-Assisted (with fallback)
- **SQLite Persistence** — All benchmark runs saved and exportable as CSV/JSON
- **Dark-Themed Desktop UI** — Built with PySide6, responsive with background thread operations

## Quick Start

### Prerequisites

- **Python 3.10–3.14** (tested with 3.12)
- **Windows 11** (primary target)
- No internet required for normal operation (only for initial Laya checkpoint download)

### Install & Run

```powershell
# Option 1: Automatic setup
.\run.ps1

# Option 2: Manual setup
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m racon
```

### Run Tests

```powershell
# With venv activated:
pytest tests/ -v

# Or without activation:
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

## Application Layout

| Tab | Purpose |
|-----|---------|
| **Dashboard** | Real-time CPU/RAM metrics, hardware info, last decision summary |
| **Scheduler Lab** | Select scenarios, objectives, and modes; run scheduler; view evaluations |
| **History** | Browse past runs, inspect details, export CSV/JSON |
| **Settings** | Default mode/objective, Laya status, paths, diagnostics export |

## Scheduling Scenarios

| # | Scenario | Expected Outcome |
|---|----------|-----------------|
| 1 | Single Feasible Worker | Worker selected |
| 2 | Multiple Feasible Candidates | Best scored worker selected |
| 3 | Insufficient Memory Worker | Rejected — insufficient RAM |
| 4 | Incompatible Runtime Worker | Rejected — wrong device type |
| 5 | High Latency Network | Low-latency worker preferred |
| 6 | Busy Worker | Rejected — fully loaded |
| 7 | Unavailable Worker | Rejected — offline |
| 8 | No Feasible Solution | Clear failure reported |

## Scheduling Modes

### Deterministic (Default)
Rule-based scoring with documented formulas. Identical inputs always produce identical results.

### Laya Shadow
Deterministic scheduler decides. Laya evaluates the same candidates and its recommendation is recorded separately. **Laya does not change the result.**

### Laya-Assisted
Laya selects from deterministic-validated feasible candidates. If Laya fails, times out, or returns an invalid result, the deterministic fallback is used with a logged reason.

## Laya Integration

Laya is **optional**. The application works fully without it.

To enable Laya:
1. Install: `pip install laya`
2. In the app, click **Initialize Laya** (downloads ~400-800 MB checkpoint on first use)
3. Select "Laya Shadow" or "Laya Assisted" mode

Laya uses the `Router.predict` API with a `choice` question to select among scheduling candidates.

## Project Structure

```
racon/
├── __init__.py          # Package version
├── __main__.py          # Entry point (python -m racon)
├── app.py               # Application class
├── config.py            # Paths and configuration
├── hardware.py          # Hardware discovery (psutil, WMI)
├── scheduler/
│   ├── models.py        # Data models (WorkerCandidate, SchedulerResult, etc.)
│   ├── deterministic.py # Rule-based scheduler with 4 objectives
│   ├── laya_adapter.py  # Laya Router integration adapter
│   └── evaluation.py    # Scheduling engine (3 modes)
├── scenarios/
│   ├── sample_scenarios.json  # 8 predefined scenarios
│   └── loader.py        # Scenario parser
├── storage/
│   ├── database.py      # SQLite initialization
│   └── repository.py    # CRUD operations
├── services/
│   ├── benchmark_service.py   # Background scheduler execution
│   └── monitoring_service.py  # Periodic hardware polling
└── ui/
    ├── main_window.py   # Main window with tabs
    ├── dashboard.py     # Hardware metrics panel
    ├── scheduler_lab.py # Scheduler interface
    ├── benchmark_history.py  # History viewer
    ├── settings.py      # Settings panel
    └── styles.py        # Dark theme stylesheet
tests/
├── test_deterministic_scheduler.py
├── test_laya_adapter.py
├── test_storage.py
└── test_hardware.py
```

## Data Storage

- **Database**: `%LOCALAPPDATA%/RACoN Lab/RACoN/racon_lab.db`
- **Logs**: `%LOCALAPPDATA%/RACoN Lab/RACoN/Logs/racon_lab.log`

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: PySide6` | Run `pip install PySide6` in your virtual environment |
| `ModuleNotFoundError: laya` | Laya is optional. Install with `pip install laya` only if needed |
| App won't start | Verify Python 3.10+ with `python --version` |
| GPU shows "Not detected" | Expected for Intel iGPU without nvidia-smi |
| Laya initialization fails | Requires internet for first checkpoint download |

## Limitations (v0.1)

- No real distributed inference — all scheduling scenarios use simulated workers
- No network worker discovery — workers are predefined in scenario files
- No model execution — scheduler evaluates placements but does not run models
- GPU telemetry unavailable for Intel integrated graphics
- Laya is optional and requires manual initialization

## Next Milestone: v0.2

- Real worker discovery via network scanning
- Actual model inference benchmarking on local hardware
- Worker registration and health monitoring
- Multi-computer coordination prototype

## License

Internal prototype — not yet licensed for distribution.
