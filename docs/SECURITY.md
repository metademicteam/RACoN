# RACoN Platform — Security Architecture & Guidelines

**Version:** 2.0  
**Updated:** 2026-10-21  

---

## 1. Core Security Invariants

1. **Deterministic Safety:** The deterministic scheduler enforces resource constraints and worker eligibility. Neural decision engines like Laya are non-security advisors and must never bypass hard constraints (ADR-002).
2. **Zero Code Execution on Workers:** Workers never execute arbitrary code, shell commands, or serialized Python objects (`pickle`). Task types are allow-listed (`TEXT_COMPLETION`, `BENCHMARK_PROBE`, `EMBEDDING`).
3. **No Inbound Ports on Contributor Devices:** Contributor PCs run behind home NATs and firewalls, initiating **only outbound** HTTPS/WSS connections to the coordinator's public URL.
4. **Physical Separation of Admin Surface:** Administrative controls exist solely on a loopback-only port (`127.0.0.1:<admin-port>`) and are never published through Tailscale Funnel.
5. **Cryptographic Identity & Message Authentication:**
   - Every worker node generates an **Ed25519** keypair stored securely in the user's local AppData directory.
   - The coordinator signs task envelopes with its private Ed25519 key. Workers verify the signature before processing.
   - Workers sign task results with their private Ed25519 key. The coordinator verifies the signature before accepting.
6. **No Stored Plaintext Secrets:** Invite codes are stored exclusively as SHA-256 hashes. Ephemeral tokens are generated with CSPRNG (`secrets.token_urlsafe(32)`).

---

## 2. Plain-Language Privacy Notice & Contributor Terms

> [!WARNING]
> **PUBLIC WORKLOAD VISIBILITY NOTICE**
> In public cluster mode, all tasks carry the data classification `data_class: PUBLIC`.
> - **Inputs and Outputs May Be Observable:** While traffic in transit is encrypted with TLS, the operator of the remote physical worker machine executing your task has access to the device's RAM and process space. Therefore, prompts, parameters, and generated responses may be visible to worker operators.
> - **Do Not Submit Confidential Content:** Never submit personally identifiable information (PII), proprietary business secrets, medical records, financial data, or credentials to public cluster tasks.
> - **Contributor Resource Donation:** Contributor agents donate CPU cycles and system RAM according to the user-configured resource ceilings. Contributor agents will never read files outside the designated model cache and application directories.

---

## 3. Threat Mitigations & Controls

For detailed vulnerability vectors and residual risk analysis, refer to [`docs/THREAT_MODEL_PUBLIC.md`](file:///f:/RACoN/docs/THREAT_MODEL_PUBLIC.md).

| Threat Vector | Primary Mitigation | Verification |
|---|---|---|
| **Internet Scanners & Floods** | Global connection rate limits; Tailscale relay SYN flood absorption | Load tests; short timeouts |
| **Sybil Swarms / Fake Nodes** | Single-use invite codes hashed via SHA-256; `PENDING_APPROVAL` status | `tests/test_abuse_and_hardening.py` |
| **Tampered Task Messages** | Ed25519 cryptographic signatures on canonicalized JSON envelopes | `tests/test_protocols.py` |
| **Replayed Nonces / Envelopes** | Timestamp windows (60s) + idempotency key cache | `tests/test_remote_single_worker.py` |
| **Malformed / Oversized Payloads** | Strict Pydantic v2 schemas; max 64 KB control envelope, 256 KB result cap | `protocols/models.py` |
| **Hostile Worker Payload Injection** | Strict allow-listed task execution (no shell, no eval, no unpickle) | `racon/agent/worker.py` |
| **Malicious Update Distribution** | Pinned coordinator Ed25519 signature verification on update manifests | `tests/test_update_manifest.py` |

---

## 4. Emergency Kill Switch & Incident Response

In the event of an anomaly, abuse attempt, or coordinator breach:
1. **Trigger Public Access Kill Switch:**
   - In the Operator UI, click **"Stop Public Access"** (or make a local `POST http://127.0.0.1:<admin-port>/admin/ingress/disable`).
   - This immediately executes `tailscale funnel <port> off`, terminating internet exposure at the relay.
2. **Instant Node Revocation:**
   - To revoke an untrusted or malfunctioning worker, run `POST http://127.0.0.1:<admin-port>/admin/nodes/{node_id}/revoke`.
   - Revocation drops the node's session token and rejects all subsequent claims.
3. **Invite Code Invalidation:**
   - Run `POST http://127.0.0.1:<admin-port>/admin/invites/{hash}/revoke` to disable a compromised invite code immediately.
