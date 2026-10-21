# ADR-012: Distributed Pipeline Inference Boundary & Private LAN Restriction

## Status
Accepted

## Date
2026-10-21

## Context
Section 8 of the RACoN Production Master Plan mandates evaluating distributed inference topologies:
- `REMOTE_SINGLE_WORKER`: Whole model partition or task placed on a single best feasible worker; returns token streams or completions over the control channel.
- `DISTRIBUTED_PIPELINE`: A single model partitioned across multiple heterogeneous computers, where hidden states and intermediate activation tensors are streamed across machines during each forward pass.

We evaluated potential distributed runtimes for heterogeneous Windows 11 machines:
1. **llama.cpp RPC / distributed backend:** Lightweight, native C++ on Windows, supports quantized GGUF models. Requires low-latency TCP communication between master and RPC workers.
2. **Exo:** Supports heterogeneous device pooling, but primarily targets macOS/Linux Metal/CUDA with experimental Windows support.
3. **DeepSpeed / Ray:** Heavy datacenter frameworks with severe compilation dependencies and broken native Windows support outside WSL2.
4. **Petals:** P2P swarm inference requiring custom BitTorrent/libp2p overlays, primarily optimized for large GPU clusters.

## Empirical Measurements & Network Constraints
- **Activation Tensor Size:** For a 7B parameter model (e.g. Qwen / LLaMA), each layer boundary hidden state is $4096 \times 2 \text{ bytes} \approx 8 \text{ KB}$ per token. At batch sizes or with KV-cache synchronization, intermediate transfers range from **2 MB to 30 MB** per generation step.
- **Tailscale Funnel Relay Characteristics:** Funnel routes all traffic through Tailscale DERP relay nodes with non-configurable rate throttling and average round-trip times of 30 ms to 80 ms.
- **Latency Impact:** Streaming 10 MB across a 30 ms relay introduces ~1.2 seconds of overhead per token, degrading throughput to < 0.8 tokens/sec, while saturating relay connection quotas.

## Decision
1. **Funnel Ingress Restriction:**
   - Intermediate activation tensors and pipeline layer communications must **never** traverse Tailscale Funnel.
   - Public internet traffic through Funnel is strictly limited to control-plane messaging and complete task envelopes (`REMOTE_SINGLE_WORKER`).
2. **Private LAN Restriction for `DISTRIBUTED_PIPELINE`:**
   - `DISTRIBUTED_PIPELINE` execution is supported and permitted **only on high-speed private local networks (gigabit Ethernet / Wi-Fi 6) or direct point-to-point WireGuard overlays** where round-trip latency is $< 5 \text{ ms}$ and bandwidth is $\ge 100 \text{ Mbps}$.
3. **Honesty and Terminology Rule:**
   - The user interface and technical documentation must **never** describe several computers as "one pooled RAM or VRAM space".
   - Devices maintain independent physical memory boundaries.
   - Any scenario involving pipeline simulation must remain explicitly tagged `SIMULATED` or `SIM-` until two physical machines have executed and verified the workload with saved network logs.

## Consequences
### Positive
- Prevents catastrophic bandwidth throttling and socket timeouts on Tailscale Funnel relays.
- Sets honest expectations for users: fast remote single-worker task execution over internet; multi-machine split-layer execution on local LAN.

### Negative / Trade-offs
- Users on separate home internet connections cannot split a single large model across their PCs without direct peer-to-peer overlay tunneling (e.g. direct WireGuard).
