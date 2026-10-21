# ADR-009: Invite-Only Enrollment with Mandatory Operator Approval

## Status
Accepted

## Date
2026-10-21

## Context
Exposing a coordinator endpoint to the internet means scanners, bots, and malicious actors will discover the URL. If the coordinator allowed unrestricted anonymous enrollment, attackers could register thousands of fake workers to flood the registry, exhaust coordinator memory, skew scheduling decisions, or intercept task payloads.
"Reachable is not open."

## Decision
We enforce strict invite-only onboarding with cryptographic identity verification and manual operator approval (Decision D5):
1. **Invite Code Lifecycle:**
   - Single-use or strictly capped multi-use invite codes created by the operator.
   - Codes are stored hashed using SHA-256 (never plaintext).
   - Time-bound with explicit expiration timestamps; revocable immediately by the operator.
2. **Cryptographic Node Identity:**
   - The worker agent generates a local Ed25519 keypair.
   - The private key is securely stored using Windows DPAPI / Credential Manager.
   - The agent submits its public key and invite code during enrollment.
3. **Mandatory Approval State:**
   - Newly enrolled nodes enter the `PENDING_APPROVAL` state.
   - In `PENDING_APPROVAL`, a worker cannot receive tasks, participate in scheduler pools, or lease resources.
   - The operator must explicitly review the node's capability snapshot and approve it via the loopback-only Admin UI.
   - Auto-approval is forbidden by default and may only be enabled per specific invite code classes under explicit operator policy.
4. **Immediate Revocation:**
   - The operator can revoke any node at any time. Revocation takes effect immediately on the coordinator, dropping existing WebSockets and rejecting any subsequent requests.

## Consequences
### Positive
- Prevents Sybil attacks and unauthorized resource hijacking.
- Ensures only verified contributor hardware joins the active cluster.

### Negative / Trade-offs
- Requires human operator intervention before a new contributor can begin executing workloads.
