# Zero-Config Join and Automatic Distributed Inference

A new user installs RACoN, opens it once, and the PC joins the cluster and starts contributing. No URL,
invite code, API key, or `cluster.json` edit is required.

## What happens automatically

1. **Join.** On launch, the worker agent enrolls against the built-in coordinator
   (`https://desktop-b7l73cl.tail6cb521.ts.net`) using the built-in join code `RCN-PUBLIC-JOIN`.
   The coordinator auto-approves it.
2. **RPC endpoint.** The agent binds `rpc-server` to its Tailscale IP (LAN IP if Tailscale is absent,
   never `0.0.0.0`) and reports `<ip>:50052` during enrollment.
3. **Gateway key.** The coordinator issues a per-node gateway API key. The app writes
   `%LOCALAPPDATA%\RACoN\racon\gateway.json`, so chat works even without a local model.
4. **Cluster discovery.** On the head PC, `racon.cluster serve` reads approved nodes from the
   coordinator's loopback admin API and rebuilds the RPC worker list. It replans when a worker joins
   or drops. `cluster.json` workers are only a fallback.
5. **Autostart.** The installer's Startup shortcut launches `RACoN.exe --autostart` at logon. The window
   stays hidden in the tray while the worker runs.

## Operator controls

| Goal | Action |
|---|---|
| Disable public auto-join | Start the coordinator with `--no-auto-join` |
| Change the join code | Set `RACON_JOIN_CODE` on the coordinator and on clients |
| Point clients at another coordinator | Set `RACON_COORDINATOR_URL` on clients |
| Revoke a node | `POST http://127.0.0.1:8766/admin/nodes/<node_id>/revoke` |
| Revoke a node's gateway key | `python -m racon.cluster keys revoke <id>` |
| Turn off autostart | Delete `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\RACoN.lnk` |
| Disable auto-connect per run | Set `RACON_AUTO_CONNECT=0` |

## After a reboot on the head PC

The head PC runs the website, coordinator, model head, gateway and tunnel. One-time setup:

```powershell
.\scripts\install_head_autostart.ps1   # adds "RACoN Head Stack" to the Startup folder
```

After that, signing in starts everything through `scripts\start_head.ps1`:

| Component | Port | Started by |
|---|---|---|
| Website container | 8081 | `docker compose up -d` (container also has `restart: unless-stopped`) |
| Coordinator | 8765 (admin 8766) | `python -m racon.coordinator.server` |
| Model head + gateway | 8090 / 8000 | `python -m racon.cluster serve` |
| Cloudflare tunnel | outbound | `cloudflared tunnel run racon` |
| Tailscale Funnel | 443 → 8765 | Tailscale service (config persists) |

Docker Desktop must be allowed to start at sign-in; the script waits for it.

If something did not start, run `.\scripts\start_head.ps1` again. It is safe to run repeatedly.
Stop everything with `.\scripts\stop_head.ps1`. Logs are in `%LOCALAPPDATA%\RACoN\logs`.

Nothing runs while the PC is off or signed out: the site and API are offline, and workers wait to
reconnect on their next heartbeat.

## Verification

```powershell
# Coordinator public surface
curl https://desktop-b7l73cl.tail6cb521.ts.net/discover
curl https://desktop-b7l73cl.tail6cb521.ts.net/health

# Approved nodes and their RPC endpoints (head PC only)
Invoke-RestMethod http://127.0.0.1:8766/admin/nodes

# Head sees workers
python -m racon.cluster status
```

## Security notes

The built-in join code shipped in the installer is public by design. While it is enabled, anyone with
the installer can join and receives a rate-limited gateway key. Review `docs/adr/ADR-015-zero-config-join-and-discovery.md`.
Public workloads remain observable by the machine that runs them; see `docs/SECURITY.md`.
