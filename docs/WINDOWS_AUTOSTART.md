# Windows logon autostart for the RACoN server

This document describes what must run after this PC signs in, why the public
`/admin` URL previously returned `{"detail":"Not Found"}`, and how to verify or repair it.

## Root cause of the 404

Three separate logon entries were competing:

| Entry | What it did | Problem |
|---|---|---|
| `racn-server-autostart.bat` | Started a Docker stack from `F:\AI_AGENT\RACN\coordinator` and set Tailscale Funnel to **8443** | Wrong stack and wrong Funnel port; it overrode the 8765 route after every boot |
| `RACoN Head Stack.lnk` → `start_head.ps1` | Started `racon.coordinator.server` on 8765 | That app is the worker-ingress-only coordinator. It has **no `/admin` route**, so it answered `{"detail":"Not Found"}` |
| `RACoN.lnk --autostart` | Started the desktop app and its worker | Fine, but its engine binds a random loopback port, so it cannot serve the public Funnel URL |

The unified FastAPI app (the one that serves `/admin`, `/api/v1/*`, `/v1/*` and the worker
routes) runs in the `racn-coordinator` Docker container and publishes **127.0.0.1:8765**.
Tailscale Funnel proxies `https://desktop-b7l73cl.tail6cb521.ts.net` to that port.

## What starts at logon now

1. **Docker Desktop** starts from the Windows Run key (`AutoStart: true`).
2. **`RACoN Head Stack.lnk`** runs `scripts/start_head.ps1` hidden:
   - waits for Docker Desktop (up to 2 minutes);
   - starts the website container (`website/docker-compose.yml`);
   - calls `scripts/start_backend.ps1`, which waits for Docker, runs
     `docker compose up -d` in `F:\RACoN`, and waits for `/health` **and** `/admin`;
   - asserts `tailscale funnel --bg 8765`;
   - starts `scripts/watch_backend.ps1` hidden;
   - starts the model head/API gateway (`racon.cluster serve`) on 8000.
3. **`racn-coordinator`** and the other Compose services use
   `restart: unless-stopped`, so Docker also restarts them on its own.
4. **`RACoN.lnk --autostart`** starts the desktop UI and its provider worker.

The stale `racn-server-autostart.bat` was renamed to
`racn-server-autostart.bat.disabled-<timestamp>` in the Startup folder. It no longer runs and
can no longer move Funnel to 8443.

## Watchdog

`scripts/watch_backend.ps1` runs hidden and persists for the logon session:

- every 30 seconds it checks `http://127.0.0.1:8765/health` and `/admin`;
- if either fails it runs `start_backend.ps1 -Restart`, which restarts the coordinator
  container (and falls back to a source-run API only if Docker is unavailable for the whole
  timeout);
- every 10 minutes it re-asserts the Funnel target;
- a per-session mutex prevents duplicate watchdogs;
- log: `%LOCALAPPDATA%\RACoN\logs\watchdog.log`.

Measured recovery on this host: **about 18 seconds** from `docker stop racn-coordinator`
to local and public `/admin` returning HTTP 200.

## Manual commands

```powershell
# Start or repair the service
.\scripts\start_backend.ps1

# Restart a hung coordinator container
.\scripts\start_backend.ps1 -Restart

# Run the API from source instead of Docker
.\scripts\start_backend.ps1 -SourceOnly

# Full head stack (website, tunnel, backend, watchdog, gateway)
.\scripts\start_head.ps1

# Install/repair the logon shortcut
.\scripts\install_head_autostart.ps1
```

## Verification

```powershell
# Container and port
docker ps --filter name=racn-coordinator
docker compose ps

# Local dashboard (must be 200 and ~48 KB)
(Invoke-WebRequest http://127.0.0.1:8765/health -UseBasicParsing).StatusCode
(Invoke-WebRequest http://127.0.0.1:8765/admin -UseBasicParsing).StatusCode

# Public dashboard (must be 200)
(Invoke-WebRequest https://desktop-b7l73cl.tail6cb521.ts.net/admin -UseBasicParsing).StatusCode

# Funnel target (must show proxy http://127.0.0.1:8765)
tailscale funnel status

# Watchdog process and log
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'watch_backend\.ps1' }
Get-Content "$env:LOCALAPPDATA\RACoN\logs\watchdog.log" -Tail 20
```

## Admin token

The dashboard is public HTML but every API call needs the administrator token. Enter the
`AUTH_TOKEN` value from `F:\RACoN\.env`, or append `?token=<AUTH_TOKEN>` once, or run:

```powershell
.\scripts\open_admin.ps1
```

Do not put the administrator token in a shared or bookmarked public URL.

## Troubleshooting

| Symptom | Check |
|---|---|
| `/admin` returns 404 | Is `racn-coordinator` running? `docker ps`. A `racon.coordinator.server` process on 8765 is the wrong app; `start_backend.ps1` stops it |
| Public URL returns 404 but local works | `tailscale funnel status`; run `tailscale funnel --bg 8765` |
| Public URL returns 404 after every boot | A stale autostart is setting another Funnel port. Check the Startup folder and remove/disable it |
| Container exits or is unhealthy | `docker compose logs --tail 100 racn-coordinator` |
| Docker not ready at logon | The scripts wait; if it still fails, start Docker Desktop and run `.\scripts\start_backend.ps1` |
| Watchdog not running | `.\scripts\start_head.ps1` or start `watch_backend.ps1` manually |
