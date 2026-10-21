# RACoN Platform — Public Ingress Threat Model

**Status:** Active  
**Version:** 1.0  
**Target:** Coordinator Ingress via Tailscale Funnel & Distributed Windows Worker Clients  

---

## 1. Scope & System Boundaries

This threat model analyzes the exposure of the RACoN Coordinator to the public internet via Tailscale Funnel and the interaction between untrusted contributor machines and coordinator services.

---

## 2. Threat Analysis Matrix

| # | Threat Description | Threat Agent | Attack Vector | Mitigation Strategy | Verification & Test | Residual Risk |
|---|---|---|---|---|---|---|
| **T-01** | **Internet Scanners & Floods** | External botnets, script kiddies | Port scans, HTTP connection exhaustion on Funnel port (443/8443) | Loopback worker listener with global connection limits, short read timeouts (5s), and global IP bucket throttling. Tailscale Funnel relay absorbs raw TCP SYN floods. | Connection flood integration test verifying coordinator responsiveness under load. | Distributed DDoS saturating Tailscale Funnel relay bandwidth. |
| **T-02** | **Enrollment Abuse & Sybil Swarms** | Malicious external actor | Automated scripts attempting to register thousands of fake nodes | Single-use/capped invite codes hashed with SHA-256; rate-limited `/enroll` endpoint (max 5/min per IP/global); mandatory `PENDING_APPROVAL` status requiring operator approval. | Fuzz test submitting 100 invalid enrollment requests; verify immediate HTTP 429 and rejection. | Leaked high-capacity invite code allows multiple nodes to register before detection. |
| **T-03** | **Stolen or Leaked Invite Code** | Unauthorized user | Acquiring an invite code shared in an insecure channel | Single-use by default; time-bounded expiration; immediate one-click revocation in Operator UI; manual approval gate prevents immediate task participation. | Automated test verifying expired or revoked invite codes cannot complete enrollment. | An unauthorized party claims a pending slot before the intended recipient. |
| **T-04** | **Stolen Node Credentials** | Rogue user with local machine access | Extracting private keys from a contributor PC | Ed25519 private keys stored in Windows Credential Store via DPAPI (accessible only to the local user account); short-lived session access tokens (15 minutes). | Test verifying credentials cannot be decrypted outside the originating Windows user context. | Malware executing in the same user session could access DPAPI keys. |
| **T-05** | **Malicious or Modified Worker** | Rogue contributor | Tampering with worker agent to report fake capabilities or inject payloads | Zero arbitrary code execution; tasks strictly allow-listed; capabilities cross-referenced with benchmark results; coordinator task envelopes signed; deterministic validation of task results. | Agent test rejecting non-allow-listed task payloads (e.g. shell commands, pickled objects). | Untrusted worker operator observing task prompt and output. |
| **T-06** | **Replayed or Forged Task Messages** | Network adversary | Intercepting and replaying task completion envelopes or heartbeat pings | Coordinator-signed task envelopes with unique `task_id`, `node_id`, `idempotency_key`, timestamp, and 60-second validity window. Replay cache rejects duplicate nonces. | Unit test attempting replay of identical signed task envelope; verify rejection. | Clock drift exceeding 30 seconds causing false message rejection. |
| **T-07** | **Malformed or Oversized Payloads** | Scanner or malicious client | Sending gigabyte-sized JSON payloads or deeply nested ASTs to crash parser | Strict Pydantic v2 schemas; HTTP body size limit enforced at transport layer (max 64 KB for control messages, max 256 KB for results); recursion depth limit. | Negative test streaming 10 MB payload to `/api/v1/control/report`; verify HTTP 413 Payload Too Large. | Memory allocation spike if parser buffering is misconfigured. |
| **T-08** | **Compromised Relay or Man-in-the-Middle** | Rogue relay node / ISP | Intercepting traffic passing through Tailscale Funnel relays | End-to-end TLS encryption terminating at Tailscale node; payload-level Ed25519 signatures on task envelopes and status reports verify origin authenticity independently of transport TLS. | Signature validation test verifying payload tampering is detected even if TLS is intact. | Tailscale relay metadata leakage (timing and packet sizing). |
| **T-09** | **Compromised Coordinator Host** | Attacker compromising coordinator PC | Gaining control of coordinator to dispatch malicious tasks to workers | Workers enforce strict allow-listed task execution (cannot execute arbitrary code even if ordered by coordinator); tasks are sandboxed; workers enforce local resource ceilings. | Test verifying worker drops task requesting arbitrary process execution or filesystem writes outside sandbox. | Coordinator could dispatch high-volume allow-listed tasks causing contributor compute usage. |
| **T-10** | **Corrupted or Substituted Model Artifacts** | Malicious CDN or mirror | Tampering with downloaded weights to poison inference | Models downloaded over direct HTTPS from verified repositories; coordinator publishes signed model manifest with SHA-256 hashes; agent verifies hash before loading. | Unit test verifying altered model file is rejected before loading into runtime. | Zero-day vulnerability in model deserializer (e.g. safetensors parser). |
| **T-11** | **Resource-Exhaustion Attack on Coordinator** | Hostile worker pool | Flooding coordinator SQLite database with millions of heartbeat records | In-memory heartbeat throttling; SQLite WAL mode with busy timeout; rate-limited write batching; database retention policy pruning stale metrics after 48 hours. | Load test running 50 concurrent simulated worker heartbeats; verify database latency < 50ms. | Disk storage exhaustion on coordinator if logs are unpruned. |
| **T-12** | **Exposure of Prompts & Outputs to Worker Operators** | Curious worker operator | Attaching debugger or network sniffer to inspect prompts and outputs | Public-access mode restricted strictly to `data_class: PUBLIC`; Requester UI plainly warns that prompts/outputs are visible to worker operators; confidential data prohibited. | UI review verifying explicit warning banner on task submission panel. | User accidentally submitting confidential personal data despite warning. |
| **T-13** | **Update-Channel Tampering** | Network adversary | Faking update manifest to distribute malicious installer | Update manifest served over HTTPS, signed with pinned coordinator Ed25519 release key; installer executable hash verified before execution. | Negative test with corrupted signature in update manifest; verify update aborts and alerts user. | Compromise of the developer's private release signing key. |

---

## 3. Security Boundary Summary

```
[ Contributor PC (Clean Win11) ]
  ├── RACoN Desktop UI (Standard User)
  │     │ Authenticated Loopback IPC
  └── RACoN Agent (Standard User)
        │ - Ed25519 Keypair in Windows DPAPI
        │ - Allow-listed task dispatch only
        │ - Strict RAM / CPU ceilings
        │ - No inbound ports
        │
        └── Outbound HTTPS / WSS
              │
══════════════╪══════════════════════════════════════════════════════════════
              │ Public Internet (Tailscale Funnel Relay, Port 443)
══════════════╪══════════════════════════════════════════════════════════════
              │
[ Coordinator Host (Operator PC / Server) ]
  ├── Public Worker Ingress (127.0.0.1:<worker-port>)  <--- Funnel terminates here
  │     ├── /health (minimal, unauthenticated)
  │     ├── /enroll (rate-limited, hashed invite code verification)
  │     └── /control/* (Ed25519 challenge-response authenticated)
  │
  ├── Private Loopback Admin Control (127.0.0.1:<admin-port>) <--- NEVER IN FUNNEL
  │     ├── /admin/nodes (approve, reject, revoke)
  │     ├── /admin/invites (generate, revoke)
  │     ├── /admin/ingress (Funnel kill switch, maintenance mode)
  │     └── /admin/audit (audit log inspection)
  │
  └── Coordinator Core Engine
        ├── Deterministic Scheduler + Laya Advisor
        └── SQLite WAL Storage (thread-local, busy_timeout=5000)
```
