# RACoN Lab MVP Documentation

## 1. Overview
RACoN Lab is a local experimental desktop application built in Python (PySide6) for exploring resource-aware scheduling decisions with optional Laya assistance.

## 2. Implemented Capabilities
- **Real Local Hardware Discovery**:
  - Processor brand, architecture, logical and physical core count via Windows Registry and `psutil`.
  - System memory usage (Total, Used, Available, Utilization %).
  - RACoN Lab process private working set memory tracking.
  - Safe GPU detection with non-fabricated metrics for Intel UHD 630 iGPU and integrated controllers.
- **8 Reproducible Scenarios**:
  - Single Feasible Worker
  - Multiple Feasible Candidates
  - Insufficient Memory Worker
  - Incompatible Runtime Worker
  - High Latency Network
  - Busy Worker
  - Unavailable Worker
  - No Feasible Solution
- **3 Scheduling Modes**:
  - Deterministic (Rule-based, 4 objective scoring formulas)
  - Laya Shadow (Deterministic decides, Laya recommendation recorded in parallel)
  - Laya Assisted (Laya selects among validated feasible candidates, falling back to deterministic rules if unready or invalid)
- **Local SQLite Persistence**:
  - Per-user database path via `platformdirs`.
  - Stores runs, candidates, score breakdown, decision latency, and fallback reasons.
  - Exportable to CSV and JSON.
