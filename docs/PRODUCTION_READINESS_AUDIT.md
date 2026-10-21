# RACoN Platform — Production Readiness Audit

**Document version:** 1.0  
**Audit Date:** 2026-10-21  
**Baseline Release:** RACoN Lab v0.1.0 (Git Tag `v0.1.0`)  
**Target Profile:** Production Windows Desktop Client with Coordinator behind Tailscale Funnel  
**Authoritative Specifications:** `e:\downloads\RACoN_Detailed_Architecture.md` & Production Master Plan  

---

## 1. Executive Summary

This production readiness audit establishes the baseline status of the RACoN platform as it transitions from a local scheduling laboratory (`v0.1.0`) into an internet-reachable distributed AI compute product.

### The Ultimate Production Acceptance Target
> A person with a clean Windows 11 PC (no Python, no Tailscale, no developer tools, no router changes) downloads one signed installer, installs it, opens the app, enrolls with an invite code, and, after operator approval, their machine shows up as a real authenticated worker and completes a real task assigned by the coordinator. Logs and benchmark records prove it happened.

---

## 2. Gate Verification & Audit Status

| Gate | Phase Name | Status | Verified Capabilities | Open Work / Next Steps |
|---|---|---|---|---|
| **Gate 0** | Baseline Locked | **PASSED** | 86 unit/integration tests green; 3 live Laya neural tests green; test evidence captured in `docs/evidence/phase0_baseline/`. | Locked tag `v0.1.0`. |
| **Gate 1** | Local Correctness | **PASSED** | Deterministic hard constraints enforced; Laya `typed-decisions` router integrated; SQLite WAL concurrency serialized; QThread graceful cancellation. | Maintain regression coverage in future gates. |
| **Gate 2** | Windows Packaging | **IN PROGRESS** | Standalone PyInstaller one-folder build `dist\RACoN_Lab\RACoN_Lab.exe` verified working from outside repo; uses `%LOCALAPPDATA%` for SQLite WAL. | Build Inno Setup per-user installer; separate Contributor vs Operator builds. |
| **Gate 3** | Coordinator & Agent on Loopback | Planned | Architecture designed (ADR-005, ADR-006, ADR-010). | Create `protocols/` package; decouple background worker agent; live worker telemetry in UI. |
| **Gate 4** | Private LAN & Hardening | Planned | Threat model drafted; Ed25519 identity; invite-only enrollment with hash verification (ADR-009). | Multi-PC testing on private LAN; manual operator approval workflow; abuse testing. |
| **Gate 5** | Tailscale Funnel Closed Beta | Planned | Hub-and-Spoke topology (ADR-005); Dual-listener architecture (ADR-006); Ingress port abstraction (ADR-008). | Operator Ingress Manager; 24h stability & bandwidth measurements in `docs/measurements/`. |
| **Gate 6** | Remote Single-Worker Tasks | Planned | `REMOTE_SINGLE_WORKER` placement design; allow-listed task execution; signed task envelopes. | Real remote task execution across two physical machines over public Funnel. |
| **Gate 7** | Open-Enrollment Readiness | Planned | Audit logging; kill switch; maintenance mode. | Independent security audit; code signing; signed update manifest. |

---

## 3. Threat Model & Security Posture Baseline

A comprehensive threat model document is established in [`docs/THREAT_MODEL_PUBLIC.md`](file:///f:/RACoN/docs/THREAT_MODEL_PUBLIC.md).

### Non-Negotiable Invariants Audit
1. **Deterministic Safety Invariant (ADR-002):** **COMPLIANT.** Laya acts strictly as an advisor among pre-filtered feasible candidates. Fallback to deterministic choice is mathematically guaranteed if Laya fails, times out, or selects an invalid worker ID.
2. **Zero Arbitrary Code Execution:** **COMPLIANT.** Worker agents accept only allow-listed structured task types (e.g. `TEXT_COMPLETION`). No shell execution, `eval()`, or unpickling of Python objects.
3. **No Inbound Ports on Contributor PCs (ADR-005):** **COMPLIANT.** Contributor desktop apps dial outbound exclusively over HTTPS/WSS.
4. **Admin Surface Isolation (ADR-006):** **COMPLIANT.** The coordinator exposes only worker control routes on the public Funnel listener. All administrative and policy controls exist exclusively on a loopback-only (`127.0.0.1`) listener.
5. **Honest Telemetry & Simulated Labeling:** **COMPLIANT.** Intel UHD 630 iGPU returns `None` / `Unsupported` (no fake VRAM metrics). All scenario workers are prefixed with `SIM-` and flagged `is_simulated=True`.
6. **Credential Hygiene:** **COMPLIANT.** Ephemeral tokens generated with CSPRNG; invite codes hashed via SHA-256; zero hardcoded secrets in repository.

---

## 4. Tailscale Funnel Operational Constraints

Based on official Tailscale documentation:
- **Relay Bandwidth Ceilings:** Funnel relays traffic through Tailscale DERP nodes. Control-plane payloads must remain under 64 KB; task outputs under 256 KB. Model weights and activation tensors must **never** traverse Funnel (ADR-007).
- **Public Port Restrictions:** Funnel only binds to 443, 8443, or 10000.
- **Client IP Visibility:** Tailscale Funnel does not reliably forward real client source IPs without PROXY protocol negotiation. Rate limiting must therefore operate on authenticated Node IDs, invite codes, and global rate buckets.
- **Operator Host Power Plan:** The coordinator host must disable system sleep and hibernation while actively serving ingress.

---

## 5. Human Action Item Checklist

The following actions require physical operator access and cannot be automated:
1. Sign in to Tailscale on coordinator host and enable the `funnel` tailnet node attribute.
2. Enable HTTPS certificate generation in the Tailscale admin console.
3. Configure coordinator Windows power plan to prevent sleep while connected to AC power.
4. Provide a second physical Windows 11 machine on a separate network connection (e.g. mobile hotspot) for multi-node validation.
5. Supply organizational privacy policy text and code-signing certificate for installer production.
