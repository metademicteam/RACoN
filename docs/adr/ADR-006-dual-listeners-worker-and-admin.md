# ADR-006: Dual Listeners on Coordinator (Public Worker API vs. Loopback Admin API)

## Status
Accepted

## Date
2026-10-21

## Context
When Tailscale Funnel exposes the coordinator's port to the public internet, any route served on that listener becomes addressable by any external internet client.
The coordinator maintains sensitive administrative controls:
- Approving and revoking worker nodes.
- Generating and invalidating invite codes.
- Modifying resource quotas and cluster policies.
- Toggling the Funnel ingress kill switch and maintenance modes.
- Inspecting cluster audit logs and database internals.

If administrative routes share the same port or HTTP listener as worker-facing routes, an authentication bug, misconfiguration, or bypass would expose cluster management to the public internet.

## Decision
We enforce a **strict dual-listener architecture** on the coordinator (Decision D2):
1. **Public Worker-Facing Listener (`WorkerIngress`):**
   - Bound strictly to `127.0.0.1:<worker-port>` (e.g., 8765) and published through Tailscale Funnel to the internet.
   - Contains **only** worker control-plane routes: health check, invite-based enrollment, challenge-response auth, heartbeat/telemetry reporting, task leasing, status updates, and small task output delivery.
   - Contains zero administrative, policy modification, or cluster control endpoints.
2. **Loopback-Only Admin Listener (`AdminControl`):**
   - Bound strictly to `127.0.0.1:<admin-port>` (e.g., 8766) and **never** published via Funnel or external ingress.
   - Accessible only to the local coordinator operator or local UI process on the operator PC.
   - Requires an ephemeral local token generated on coordinator startup.
3. **Automated Verification:**
   - An automated test suite must query every administrative endpoint via the public worker listener URL and verify that it returns `404 Not Found` (route does not exist on that listener), never `401 Unauthorized` or `403 Forbidden`.

## Consequences
### Positive
- Defense in depth: Even if an attacker obtains administrative tokens or credentials, the admin endpoints are physically absent from the public listener's router table.
- Clear architectural separation between public control-plane protocols and private cluster management.

### Negative / Trade-offs
- The coordinator maintains two separate server instances / ASGI apps and two port allocations on loopback.
