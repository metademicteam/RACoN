# ADR-011: Configurable Coordinator Endpoint Resolution

## Status
Accepted

## Date
2026-10-21

## Context
In production, the coordinator's public URL might be a Tailscale Funnel address (`https://node.tailnet.ts.net`), an IP address on a local test LAN, or a custom domain behind a reverse proxy.
If the coordinator URL is hardcoded into executable binaries, any URL rotation, certificate renewal, or network migration would break existing deployed desktop clients and require recompiling the entire application.

## Decision
The coordinator endpoint is resolved dynamically using a layered configuration hierarchy (Decision D7):
1. **Resolution Priority:**
   1. User-specified runtime override via CLI argument (`--coordinator-url`) or UI Settings.
   2. Environment variable override (`RACON_COORDINATOR_URL`).
   3. Local persisted settings file (`%LOCALAPPDATA%\RACoN\config.json`).
   4. Build-time default embedded during release compilation.
2. **Endpoint Validation & Guardrails:**
   - Must parse as a valid URI with scheme `https://` (or `http://127.0.0.1` for local development/testing).
   - In production builds, unencrypted `http://` over remote internet routes is rejected.
   - Endpoint rotation is supported: clients can be redirected to a new coordinator URL provided the redirect message is cryptographically signed by the coordinator's pinned Ed25519 root authority.

## Consequences
### Positive
- Eliminates hardcoded dependencies on specific URLs or network topologies.
- Permits smooth migration across tunnels, domain names, or testing environments without recompiling client binaries.

### Negative / Trade-offs
- Requires robust validation and TLS verification to avoid user misconfiguration.
