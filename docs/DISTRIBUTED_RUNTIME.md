# RACoN Desktop — Distributed Runtime Architecture

## 1. Classification of Execution Modes
RACoN strictly distinguishes between three execution environments:
1. **Real Local Inference**: Executes through local model runtimes (such as Ollama on `127.0.0.1:11434`), loading genuine models (e.g., `qwen2.5:1.5b`, `qwen3.5:0.8b`) and measuring real token generation latency and throughput.
2. **Scheduler Simulation**: Generates synthetic candidate representations (`SIM-WORKER-01`, etc.) to evaluate placement algorithms under stress, high network latency, or memory bottlenecks without executing models. All simulated nodes and metrics are explicitly marked.
3. **Distributed Pipeline / Partitioning (Staging)**: Versioned worker protocol defining message exchanges for worker enrollment, capability advertisement, heartbeat monitoring, and tensor activation streaming.

## 2. Worker Protocol & Message Schemas
Worker communication uses typed schemas:
- `WorkerRegistrationRequest`: Host, port, core count, available RAM, detected accelerators.
- `CapabilityReport`: Supported backends (`ollama`, `onnx`, `torch`), supported architectures.
- `Heartbeat`: Stamped health signal (nodes marked offline if stale > 30 seconds).
- `TaskAssignment`: Task ID, model name, input payload, deadline.
- `TaskProgress`: Percentage completion and partial generation telemetry.
- `TaskOutcome`: Final response text, execution duration, and resource cost.
- `Cancellation`: Immediate task termination signal.

## 3. Distributed Execution Blockers & Roadmap
Full multi-PC tensor parallelism across disparate consumer machines requires high-bandwidth interconnects and compatible GPU runtimes. In v0.1, RACoN implements the coordination, scheduling, and local execution pipelines, laying the protocol foundation for network-partitioned cluster backends in v0.2.
