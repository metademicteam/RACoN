# ADR-007: Funnel Scope Limited to Control-Plane Traffic and Small Payloads

## Status
Accepted

## Date
2026-10-21

## Context
Tailscale Funnel relays traffic through Tailscale DERP/relay servers. Official Tailscale documentation notes that Funnel is in beta, subject to non-configurable bandwidth limits, connection throttling, and relay buffering.
Large Language Model (LLM) weights range from hundreds of megabytes to tens of gigabytes. Distributed model partition activations or tensors can generate dozens of megabytes per inference step. Routing such bulk data over Tailscale Funnel would quickly violate bandwidth policies, induce relay throttling, cause connection drops, and exhaust relay capacity.

## Decision
We strictly restrict Tailscale Funnel traffic to **control-plane messages and small task envelopes/results** (Decision D3):
1. **Permitted over Funnel:**
   - Worker enrollment requests and capability snapshots (< 64 KB).
   - Periodic heartbeats and lightweight status pings (< 8 KB).
   - Task assignment envelopes containing parameters and prompt text (< 64 KB).
   - Small task output results (e.g. text completion tokens, classification outputs < 256 KB).
   - Task cancellation signals, deadline updates, and error reports.
2. **Strictly Prohibited over Funnel:**
   - Model weights, checkpoints, GGUF/Safetensors files.
   - Intermediate activation tensors and pipeline layer communications.
   - Large training datasets or bulk binary blobs.
3. **Model Distribution Rule:**
   - Model artifacts must be downloaded by workers directly from verified upstream providers (e.g., Hugging Face, Ollama local pull) or cached local storage over direct HTTPS connections.
   - Model manifests distributed by the coordinator contain metadata and SHA-256 hashes for workers to verify independently before execution.

## Consequences
### Positive
- Minimizes bandwidth footprint through Funnel relay, ensuring high reliability and avoiding relay throttling.
- Eliminates the risk of Funnel connection saturation blocking heartbeat monitoring or task dispatch.

### Negative / Trade-offs
- True distributed pipeline inference (requiring high-bandwidth activation passing between machines) cannot traverse Funnel; it is explicitly restricted to local LAN or direct private overlays.
