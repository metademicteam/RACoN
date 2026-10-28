# ADR-015: Zero-Config Join, Per-Node Gateway Keys, and Worker Discovery

## Status
Accepted

## Date
2026-10-26

## Context
ADR-009 requires an invite code and operator approval for every worker. In practice this made a fresh
install unusable without the operator walking each user through a URL and a code. Chat also needed a
manually created gateway key, and the head needed `cluster.json` edited by hand before it could use
worker PCs.

## Decision
1. **Built-in join code.** The installer ships a default join code (`RCN-PUBLIC-JOIN`, overridable with
   `RACON_JOIN_CODE`). The coordinator accepts it on `/enroll` alongside real invites, and does not
   consume it. Operators disable it with `--no-auto-join`. Invite codes remain supported.
2. **Persistent invites.** Codes are stored in `%LOCALAPPDATA%\RACoN\racon\coordinator\invites.json`
   so a coordinator restart no longer invalidates them.
3. **Per-node gateway key.** On enrollment, an approved node receives a one-time gateway API key. The
   app writes it to `%LOCALAPPDATA%\RACoN\racon\gateway.json`, so chat works without manual setup.
   Keys are stored only as SHA-256 digests and can be revoked per key.
4. **Automatic worker binding.** The agent binds `rpc-server` to its Tailscale IP (falling back to the
   LAN IP, never `0.0.0.0`) and reports that endpoint during enrollment.
5. **Cluster auto-discovery.** `racon.cluster serve` asks the loopback admin API for approved nodes
   with an RPC endpoint and replans when membership changes, instead of relying on `cluster.json`.
6. **Autostart.** The installer offers a Startup shortcut; RACoN starts hidden in the tray and
   auto-connects.
7. **Self-healing.** If the coordinator restarts and forgets a node, the heartbeat `re_enroll` command
   triggers an automatic rejoin with the stored join code.

## Consequences
### Positive
- A new user installs, opens the app, and contributes without entering anything.
- Chat works on PCs that have no local model, through the gateway key issued at enrollment.
- The head uses new workers without file edits or restarts by the operator.

### Negative / Trade-offs
- **The join code is public.** Anyone with the installer can join while auto-join is enabled. This is
  weaker than ADR-009. Mitigations: coordinator kill switch (`--no-auto-join`), per-node revocation,
  per-IP and per-key rate limits, and public-workload visibility notices.
- The gateway key is issued to every approved node. Rotate or revoke keys if one is abused.
- Auto-discovery requires the coordinator admin API on loopback; a head on a different machine must
  point discovery at the coordinator explicitly.
- In-memory registry still forgets nodes on restart; the re-enroll path handles it but brief gaps occur.

## Related
- ADR-009 (invite-only enrollment). This ADR relaxes its default while keeping invites available.
- ADR-006 (admin API stays loopback-only). Discovery depends on this.
- Runbook: `docs/ZERO_CONFIG.md`.
