# RACoN — Production Build Plan: From Local Prototype to Internet-Reachable Desktop Product

**Target Milestone:** RACoN Production Desktop with Tailscale Funnel Coordinator  
**Baseline:** RACoN Lab v0.1.0  
**Specification:** `e:\downloads\RACoN_Detailed_Architecture.md` + Production Master Prompt  

---

## 1. Executive Summary & Acceptance Test

**Ultimate Acceptance Test:**
> A person with a clean Windows 11 PC (no Python, no Tailscale, no developer tools, no router changes) downloads one signed installer, installs it, opens the app, enrolls with an invite code, and, after operator approval, their machine shows up as a real authenticated worker and completes a real task assigned by the coordinator. Logs and benchmark records prove it happened.

---

## 2. Gap Analysis: Current Repository vs. Production Goal

| Domain | Current State (v0.1.0 Baseline) | Production Target | Gap & Action Required |
|---|---|---|---|
| **Architecture** | PySide6 modular monolith with simulated JSON scenarios | Hub-and-Spoke: Coordinator host behind Funnel, Contributor PCs dialing out | Decouple Agent process, implement Coordinator service with dual listeners (ADR-005, ADR-006, ADR-010) |
| **Ingress** | Local loopback only (`127.0.0.1`) | Internet-reachable via Tailscale Funnel on coordinator | Implement `PublicIngress` port with `TailscaleFunnelIngress` adapter, external reachability probe, kill switch (ADR-007, ADR-008) |
| **Protocol** | Internal dataclasses (`racon.scheduler.models`) | Versioned typed protocol package (`protocols/`) | Create `protocols/` with Pydantic v2 models, strict size limits, version negotiation, and contract tests |
| **Worker Identity** | Scenario IDs (`SIM-CPU-01`, etc.) | Ed25519 keypair per machine, DPAPI storage | Implement cryptographic node identity, invite code hashing (SHA-256), coordinator signed task envelopes |
| **Enrollment** | Hardcoded JSON scenarios | Invite-only with operator approval workflow | Implement invite manager, `PENDING_APPROVAL` trust state machine, operator approval UI |
| **Task Execution** | Simulated candidate scoring in UI | Allow-listed real local task execution (`REMOTE_SINGLE_WORKER`) | Worker runtime adapter executing allow-listed tasks (e.g. Ollama inference) with resource ceilings |
| **Desktop UI** | 4 tabs (Dashboard, Scheduler Lab, History, Settings) | Contributor & Requester experience + Lab | Add Welcome/Consent, Enrollment, My Contribution, Tasks, Network Status screens while preserving Lab/History |
| **Packaging** | One-folder PyInstaller build (`RACoN_Lab.exe`) | Per-user Windows installer (Inno Setup) | Create Inno Setup script, build separation (Public Contributor vs Operator Coordinator), update manifest |

---

## 3. Architecture Decision Records (ADRs)

The following foundational ADRs are documented in `docs/adr/`:
- **ADR-001**: Native PySide6 Desktop Application
- **ADR-002**: Deterministic Safety Invariant with Laya Advisory
- **ADR-003**: Thread-Safe SQLite WAL Concurrency
- **ADR-004**: Lazy Laya Initialization
- **ADR-005**: Hub-and-Spoke Topology via Coordinator Funnel Ingress
- **ADR-006**: Dual Listeners on Coordinator (Public Worker API vs. Loopback Admin API)
- **ADR-007**: Funnel Scope Limited to Control-Plane Traffic and Small Payloads
- **ADR-008**: Public Ingress Port Abstraction (`PublicIngress`)
- **ADR-009**: Invite-Only Enrollment with Mandatory Operator Approval
- **ADR-010**: Decoupled Worker Agent Process with Authenticated Loopback IPC
- **ADR-011**: Configurable Coordinator Endpoint Resolution

---

## 4. Key Architectural Risks & Mitigations

1. **Tailscale Funnel Reliability & Beta Status:**
   - *Risk:* Funnel is in beta, subject to bandwidth limits, connection drops, and relay throttling.
   - *Mitigation:* Strict control-plane only restriction (ADR-007); WebSocket with HTTP long-poll fallback; 24h stability measurements recorded in `docs/measurements/`.
2. **Internet Exposure of Coordinator:**
   - *Risk:* Internet scanners, DDoS, brute-force enrollment, or admin endpoint discovery.
   - *Mitigation:* Dual listeners (ADR-006: admin routes physically absent from public port); hashed single-use invite codes; Ed25519 request signing; global rate limits; one-click kill switch.
3. **Untrusted Worker Environment:**
   - *Risk:* Contributor machine runs malicious code or returns falsified results.
   - *Mitigation:* Zero arbitrary code execution; allow-listed task types only; deterministic verification; honest labeling of simulated vs. observed metrics; plain-language privacy notice.
4. **Windows Resource Constraints (Intel Core i3 / 16GB RAM):**
   - *Risk:* Heavy runtimes, multiple background daemons, or unconstrained models crashing the machine.
   - *Mitigation:* Configurable CPU/RAM ceilings, lazy model initialization, honest GPU reporting (no fabricated metrics for Intel UHD 630).

---

## 5. Sequence of Human-Required Steps

The following steps require human action and cannot be automated:

1. **Coordinator Tailscale Setup:**
   - Sign in to Tailscale on the coordinator host.
   - Ensure the tailnet policy includes the `funnel` attribute for the node:
     ```json
     "nodeAttrs": [
       {"target": ["tag:coordinator"], "app": {"tailscale.com/cap/funnel": [{}]}}
     ]
     ```
   - Enable HTTPS certificates in the Tailscale admin console.
   - Ensure coordinator power plan prevents Windows sleep while serving.

2. **Code Signing & Distribution:**
   - Obtain a code-signing certificate (EV or standard) for Windows executables/installers.
   - Designate HTTPS hosting for update manifests and installer downloads.

3. **Multi-Machine Testing Environment:**
   - Provide a second physical Windows 11 PC connected to a separate internet connection (e.g. mobile hotspot or external Wi-Fi) to validate clean-install enrollment and remote task execution.
   - Provide a clean Windows 11 VM with no developer tools or Python installed to verify the installer.

4. **Legal & Compliance:**
   - Supply plain-language Privacy Notice and Terms of Participation regarding contributor resource sharing and public task handling.

5. **Open Enrollment Sign-Off:**
   - Conduct final security review before enabling open-enrollment flags.
