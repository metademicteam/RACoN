# ADR-014: Cloudflare Tunnel for the Public Website and API

## Status
Accepted

## Date
2026-10-26

## Context
The public website and OpenAI-compatible API need a stable HTTPS hostname, `racon.metademic.org`, served from the
operator's Windows PC. The PC is behind a home NAT and has no inbound ports. ADR-008 lists `CloudflareTunnelIngress`
as a planned `PublicIngress` adapter. `metademic.org` DNS is hosted on Cloudflare.

Tailscale Funnel (ADR-007) is still the ingress for the coordinator. It is beta, has bandwidth limits, and its
hostname is `*.ts.net`, not a custom domain. The website needs a custom domain.

## Decision
1. Use a **named Cloudflare Tunnel** (`cloudflared tunnel run racon`) to publish `racon.metademic.org`. It is an
   outbound-only connection from the PC, so no router change or inbound port is needed.
2. Route by path with ingress rules:
   - `/v1/*` and `/health` go to the API gateway on `127.0.0.1:8000`.
   - Everything else goes to the website container on `127.0.0.1:8081`.
3. Bind every service to loopback. The tunnel is the only public entry point.
4. The gateway keeps its own controls: API-key auth, the `/v1` path allow-list, a 256 KB body cap, a `max_tokens`
   cap, and per-IP and per-key rate limits.
5. Funnel stays a supported alternative through the `PublicIngress` port. This ADR does not remove it.

## Consequences
### Positive
- Custom domain and TLS handled by Cloudflare. No certificate management on the PC.
- Outbound-only connection. Nothing listens on a public interface.
- Works with the existing `PublicIngress` abstraction. Only a new adapter is needed.

### Negative / Trade-offs
- The site is available only while this PC is on and `cloudflared` is running. There is no failover.
- Cloudflare terminates TLS and can see request bodies. Public workloads are already documented as observable
  (SECURITY.md section 2). This does not change that.
- Rate limits are in memory and per process. A multi-gateway deployment would need shared state.
- Requires a one-time `cloudflared tunnel login` by the operator. This cannot be automated.

## Related
- ADR-007: Funnel scope (control plane only). Model weights still never cross any public ingress.
- ADR-008: `PublicIngress` port. `CloudflareTunnelIngress` is the second adapter.
- Runbook: `docs/WEBSITE_CLOUDFLARE.md`.
