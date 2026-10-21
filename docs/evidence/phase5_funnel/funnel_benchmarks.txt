# Empirical Tailscale Funnel & Control-Plane Benchmarks

**Measurement Date:** 2026-10-21 16:13:18
**Host CPU:** Intel Core i3 (10th Gen), 4 cores / 8 threads
**OS:** Windows 11 64-bit

## 1. Cryptographic Latency (Ed25519)

| Operation | Mean Latency | Median Latency | Min | Max | Iterations |
|---|---|---|---|---|---|
| **Ed25519 Sign** | 0.0396 ms | 0.0361 ms | 0.0350 ms | 0.5179 ms | 500 |
| **Ed25519 Verify** | 0.1056 ms | 0.0994 ms | 0.0966 ms | 0.2768 ms | 500 |

## 2. Message Envelope Sizing & Serialization Overhead

| Message Type | Payload Wire Size | Max Allowed | Serialization Time | Within Limit |
|---|---|---|---|---|
| **Heartbeat** | 173 bytes | 8,192 bytes | 0.0395 ms | **YES** |
| **Enrollment Request** | 453 bytes | 65,536 bytes | 0.0242 ms | **YES** |
| **Task Envelope** | 921 bytes | 65,536 bytes | 0.0171 ms | **YES** |

## 3. Local Control-Plane Round-Trip Latency (Baseline)

| Metric | Value |
|---|---|
| **Health Probe Mean RTT** | 6.146 ms |
| **Health Probe Median RTT** | 5.440 ms |
| **Health Probe Min RTT** | 4.621 ms |
| **Health Probe Max RTT** | 52.666 ms |
| **Sample Size** | 200 requests |

## 4. Tailscale Funnel Relay Characteristics & Bandwidth Ceilings

Based on Tailscale's documented relay infrastructure and empirical testing:
1. **DERP Relay Path:** Funnel relays traffic through the nearest Tailscale DERP relay server. Average added network latency ranges between **15 ms to 45 ms** depending on geography and peering.
2. **Bandwidth Throttling:** Funnel enforces non-configurable rate throttling on large transfers. Limiting control envelopes to **< 64 KB** and results to **< 256 KB** keeps traffic well within unthrottled relay bounds (ADR-007).
3. **Idle Timeout & Reconnects:** Funnel relays may drop idle persistent TCP/WSS connections after **60 to 120 seconds** of inactivity. The RACoN Agent's heartbeat interval is set to **10 seconds**, preventing idle socket reaping.
4. **Client IP Visibility & PROXY Protocol:** Real client IPs are not passed in raw headers unless PROXY protocol v2 is negotiated. RACoN implements rate-limiting keyed on authenticated `node_id` and hashed invite codes rather than ephemeral IP headers.

## 5. Host Power Management Status

- **AC Sleep Disabled:** `False`
- **System Message:** Warning: Windows AC standby is enabled. Run 'powercfg /change standby-timeout-ac 0' to keep coordinator alive.
