# RACoN Lab Architecture

## System Diagram

```
+--------------------------------------------------------------+
|                     PySide6 Desktop UI                       |
|  [Dashboard]   [Scheduler Lab]   [History]   [Settings]     |
+------------------------------+-------------------------------+
                               | Signals / Slots (QThread)
                               v
+--------------------------------------------------------------+
|                       Service Layer                          |
|   MonitoringService                     BenchmarkService     |
+--------------+-----------------------------------+-----------+
               |                                   |
               v                                   v
+-----------------------------+   +----------------------------+
|      Hardware Module        |   |      Scheduling Engine     |
| - psutil CPU / RAM          |   | - DeterministicScheduler   |
| - Registry & WMI discovery  |   | - LayaAdapter (Router API) |
+-----------------------------+   +--------------+-------------+
                                                 |
                                                 v
                                  +----------------------------+
                                  |     Storage Layer (SQLite) |
                                  | - Database & Repository    |
                                  | - BenchmarkRuns & Settings |
                                  +----------------------------+
```

## Layer Responsibilities
- **Hardware Discovery (`racon.hardware`)**: Pure Python and system APIs; queries CPU specs, logical/physical cores, RAM, and GPU telemetry without blocking.
- **Scheduler Core (`racon.scheduler`)**:
  - `DeterministicScheduler`: Hard-constraint validation (RAM headroom, status, workload %, device runtime compatibility) followed by 4 distinct objective scoring algorithms.
  - `LayaAdapter`: Encapsulates Laya `Router` calls behind typed choice questions; handles uninitialized, loading, ready, failed, and unavailable states.
  - `SchedulingEngine`: Coordinates the three execution modes.
- **Services (`racon.services`)**: Offloads computation and I/O to background Qt threads (`QThread`) to keep the UI completely responsive.
- **Storage (`racon.storage`)**: SQLite database with foreign keys and WAL mode.
