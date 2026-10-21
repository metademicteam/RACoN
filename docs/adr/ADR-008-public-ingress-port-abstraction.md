# ADR-008: Public Ingress Port Abstraction (`PublicIngress`)

## Status
Accepted

## Date
2026-10-21

## Context
Tailscale Funnel is currently a beta feature. It is platform-dependent, requires Tailscale CLI v1.38.3+, and requires a tailnet policy attribute. In future deployments, an operator might prefer alternative ingress solutions such as Cloudflare Tunnels, an SSH reverse tunnel, a VPS reverse proxy (e.g., Caddy/Nginx), or direct public IP binding.
Hardcoding Tailscale Funnel CLI interactions directly into coordinator core logic would couple the platform to a single vendor and beta service.

## Decision
We define an explicit port interface: `PublicIngress` (Decision D4):
1. **Interface Contract (`PublicIngress`):**
   - `enable(local_port: int, public_port: int) -> IngressStatus`
   - `disable() -> IngressStatus`
   - `get_status() -> IngressStatus` (exposes public URL, state, protocol, errors)
   - `probe_reachability(timeout_s: float) -> ReachabilityReport`
2. **First Adapter: `TailscaleFunnelIngress`:**
   - Implements `PublicIngress` via direct, safe invocations of `tailscale.exe funnel` using structured argument lists (never raw shell strings).
   - Validates Tailscale installation, login status, tailnet funnel permissions, and certificate provisioning.
3. **Pluggable Alternatives:**
   - Future adapters (e.g. `DirectHostIngress`, `CloudflareTunnelIngress`, `CustomReverseProxyIngress`) can be swapped via configuration without touching coordinator business logic.

## Consequences
### Positive
- Isolates external network ingress mechanics from coordinator scheduling, registry, and state management.
- Provides unified error handling and diagnostics for ingress failure modes.

### Negative / Trade-offs
- Requires abstracting ingress status and state transitions across different tunnel implementations.
