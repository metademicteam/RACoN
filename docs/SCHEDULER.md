# RACoN Desktop — Scheduling Engine & Objectives

## 1. Candidate Representation
Each compute candidate node is modeled by `WorkerCandidate`:
- `worker_id`: Unique identifier (e.g., `SIM-WORKER-01`, `LIVE-local-node`).
- `is_simulated`: Boolean flag distinguishing real connected hardware from synthetic benchmarks.
- `status`: `AVAILABLE`, `BUSY`, or `UNAVAILABLE`.
- `device_type`: `CPU`, `CUDA`, `MPS`, `XPU`, `ONNX`.
- `ram_total_mb`, `ram_available_mb`: System memory.
- `accelerator_memory_mb`: Measurable GPU/NPU memory (or `None`).
- `processing_speed_tps`: Tokens per second processing speed estimate.
- `network_latency_ms`: Measured round-trip network latency.
- `current_workload_pct`: Active workload percentage (0-100%).
- `model_partition_required_mb`: Memory required for the scheduled task.

---

## 2. Hard-Constraint Validation
Before scoring, candidates must pass hard filters:
1. `status != UNAVAILABLE`: Inactive/offline nodes are rejected.
2. `current_workload_pct < 100`: Fully saturated nodes are rejected.
3. `ram_available_mb >= model_partition_required_mb`: Nodes with insufficient RAM are rejected.
4. `device_type in compatible_runtimes`: Incompatible architectures (e.g. CPU for CUDA-only tasks) are rejected.

---

## 3. Deterministic Scoring Objectives

### Objective A: Lowest Estimated Latency
$$\text{Score} = \left(\frac{1}{\text{effective\_latency}}\right) \times \text{availability\_factor}$$
where:
- $\text{processing\_time} = \frac{\text{model\_partition\_mb}}{\max(\text{processing\_speed\_tps}, 1.0)}$
- $\text{effective\_latency} = \text{processing\_time} + \text{network\_latency\_ms}$
- $\text{availability\_factor} = \frac{100 - \text{current\_workload\_pct}}{100}$

### Objective B: Highest Measured Throughput
$$\text{Score} = \text{effective\_throughput} \times \text{availability\_factor} \times \min(\text{memory\_headroom}, 10.0)$$

### Objective C: Resource Efficiency
$$\text{Score} = \left(1.0 - \min\left(\frac{\text{model\_partition\_mb}}{\text{ram\_available\_mb}}, 1.0\right)\right) \times \text{suitability}$$

### Objective D: Balanced Policy
$$\text{Score} = 0.3 \times \text{norm\_latency} + 0.3 \times \text{norm\_throughput} + 0.2 \times \text{norm\_efficiency} + 0.2 \times \text{norm\_availability}$$
