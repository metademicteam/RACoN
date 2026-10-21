# ADR-005: Hub-and-Spoke Topology via Coordinator Funnel Ingress

## Status
Accepted

## Date
2026-10-21

## Context
RACoN is transitioning from a local scheduling laboratory to a distributed network where machines can contribute compute resources over the internet.
Target users have clean Windows 11 PCs without public IP addresses, without port forwarding, without developer tooling, and without requiring a Tailscale account or custom VPN configuration.
If every contributor PC were required to run Tailscale Funnel or open inbound NAT/firewall ports, installation and onboarding friction would be insurmountable for mainstream contributors. Furthermore, exposing user PCs to inbound internet traffic creates severe security and privacy hazards.

## Decision
We adopt a **hub-and-spoke topology** (Decision D1):
1. **Coordinator Host (Hub):** Operates on an operator PC or dedicated server. The operator configures Tailscale and enables Tailscale Funnel on a single public port (443, 8443, or 10000). The coordinator provides the publicly reachable HTTPS and WSS endpoint.
2. **Contributor/Requester PCs (Spokes):** Run the RACoN Desktop application and background RACoN Agent. Spoke machines make **only outbound** connections over standard HTTPS and WSS to the coordinator's public Funnel URL.
3. **No Inbound Ports on Spokes:** No user PC opens an inbound port, runs Funnel, or requires port forwarding.
4. **Install-Only Onboarding:** A user simply installs RACoN, enters an invite code, and dials out to the coordinator.

## Consequences
### Positive
- Zero network configuration for contributors: no firewall changes, no port-forwarding, no Tailscale account needed on contributor PCs.
- Contributor devices remain shielded from inbound internet scans, probes, and unsolicited attacks.
- Standard HTTPS/WSS outbound traffic cleanly navigates home NATs, corporate firewalls, and ISP carrier-grade NAT (CGNAT).

### Negative / Trade-offs
- The coordinator host becomes a single ingress hub and must remain online, awake, and accessible.
- All control-plane traffic traverses Tailscale's Funnel relay infrastructure, subject to Funnel bandwidth and connection limits.
- Peer-to-peer data plane connections across spokes are not available via Funnel; bulk data and model activations must never route through Funnel.
