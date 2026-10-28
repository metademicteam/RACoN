# RACoN Website and API on racon.metademic.org (Cloudflare Tunnel)

This runs the public RACoN site, installer downloads and OpenAI-compatible API from this PC. A Cloudflare Tunnel
publishes the site at `https://racon.metademic.org`. The site is up only while this PC is on and the tunnel and
gateway are running.

## Architecture

```
Browser / API client
   │ HTTPS
   ▼
Cloudflare edge (racon.metademic.org)
   │ outbound-only tunnel from this PC (cloudflared)
   ▼
cloudflared (this PC)
   ├── /v1/*, /health ──▶ API gateway 127.0.0.1:8000   (API keys, rate limits, /v1 allow-list)
   │                          │ loopback
   │                          ▼
   │                      llama-server head 127.0.0.1:8090 ──▶ RPC workers over Tailscale (optional)
   │
   └── everything else ──▶ Docker: racon-site 127.0.0.1:8081 (static site + installer downloads)
```

Nothing listens on a public interface. The gateway, head and site bind to loopback only, and the tunnel is the only
inbound path. The admin API is not routed anywhere.

## Limits

- Per-IP limit: 30 requests/min on `/v1/*`, counted before authentication.
- Per-key limit: 60 requests/min on `/v1/*`.
- `max_tokens` above `max_tokens_limit` (2048) returns `400`.
- Request bodies over 256 KB return `413`.
- Limits are in memory and reset when the gateway restarts. Use one gateway process.

## One-time setup

Run these as your Windows user, in PowerShell from `F:\RACoN`.

1. Log in to Cloudflare. This opens a browser and asks you to pick the `metademic.org` zone:
   ```powershell
   cloudflared tunnel login
   ```
2. Create the tunnel:
   ```powershell
   cloudflared tunnel create racon
   ```
   Note the tunnel UUID it prints, and the credentials file it writes to `%USERPROFILE%\.cloudflared\<UUID>.json`.
3. Route the hostname to the tunnel. This creates the DNS record:
   ```powershell
   cloudflared tunnel route dns racon racon.metademic.org
   ```
4. Copy `website\cloudflared\config.yml` to `%USERPROFILE%\.cloudflared\config.yml` and replace `<TUNNEL_UUID>`
   in both places with the UUID from step 2.
5. Create the first API key for the site and agents. The key is shown once:
   ```powershell
   .\.venv\Scripts\python.exe -m racon.cluster keys create website-and-agent
   ```

## Running it

Start the head and gateway first, then the site and tunnel:

```powershell
# Terminal 1: model head + API gateway (leave running)
.\.venv\Scripts\python.exe -m racon.cluster serve

# Terminal 2: website container + tunnel
.\scripts\website_tunnel.ps1 -Mode start
```

Check it:

```powershell
.\scripts\website_tunnel.ps1 -Mode status
curl https://racon.metademic.org/health
```

Stop it:

```powershell
.\scripts\website_tunnel.ps1 -Mode stop
```

## Chat on a PC without the model

The desktop chat uses a local GGUF model when it finds one (`RACON_GGUF_MODEL`, the project path, or
`%LOCALAPPDATA%\RACoN\racon\models`). Otherwise it calls the remote gateway.

No manual setup is needed: enrollment issues a per-node gateway key and the app writes
`%LOCALAPPDATA%\RACoN\racon\gateway.json` automatically. For a manual override:

```powershell
.\scripts\configure_chat_gateway.ps1 -ApiKey "rk_..." -GatewayUrl "https://racon.metademic.org"
```

Environment variables `RACON_API_KEY` and `RACON_GATEWAY_URL` take precedence. If every backend fails,
the chat reply lists each backend and its error.

## Publishing a new installer

After compiling `dist\installer\RACoN_Setup_v<version>.exe`:

```powershell
.\scripts\publish_website.ps1 -Version 0.1.0
```

This copies only the current installer to `dist\website_downloads`, writes `manifest.json` with its SHA-256, and the
page picks it up without a container rebuild.

## Known limits

- Single PC. If it sleeps or loses internet, the site and API go down. Keep it on AC power with sleep disabled
  (see `docs/OPERATIONS.md` section 1.3).
- The head model is Gemma-3-1B. Output quality is limited. See `docs/CLUSTER_API.md`.
- Layer splitting across workers goes over Tailscale, not the public internet (ADR-007, ADR-012).
- Port 8080 is used by the existing `racn-llama` container, so the head runs on 8090 and the site on 8081.
