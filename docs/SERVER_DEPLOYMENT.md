# RACoN Server Deployment — Windows host, Tailscale, PostgreSQL, Docker

This is the operator guide for running **this Windows PC as the RACoN server**. The whole
control plane runs in one Docker Compose stack:

```
Tailnet / LAN
   │
   ├── http://<tailscale-ip>:8765  ──  racn-coordinator
   │        /enroll /heartbeat /tasks/*   worker ingress (agents)
   │        /api/v1/*                     desktop + admin API
   │        /admin                        operator dashboard
   │        /v1/*                         OpenAI-compatible inference
   │
   ├── http://<tailscale-ip>:3000  ──  racn-webapp (nginx)
   │        /            React desktop UI (proxied to the coordinator)
   │        /admin       admin dashboard (proxied)
   │
   └── racn-postgres (durable state) · racn-model-registry · racn-llama (optional)
```

The important change in this build: **the coordinator worker routes live in the same
FastAPI app as the desktop API**. Before, the container exposed only `/api/v1/*`, so a
worker had nowhere to enroll. `/protocols` is now copied into the image as well; without it
the Ed25519 enrollment code could not import.

---

## 1. Prerequisites

| Component | Requirement |
|---|---|
| Docker Desktop | Running, with Linux containers |
| Tailscale | Installed **on the Windows host** (not a container) and signed in |
| Ollama (optional) | Running on the host at `127.0.0.1:11434`. The image also ships a pinned CPU llama.cpp build, so GGUF models work without Ollama |
| Free ports | 8765 (server), 3000 (web UI). PostgreSQL is internal to the Compose network. |
| Disk | At least 10 GB for models under the `racon-models` volume |

Get the host's tailnet address:

```powershell
tailscale ip -4
# e.g. 100.76.208.54
```

Every URL below uses `100.76.208.54` as an example.

---

## 2. Configure

```powershell
Copy-Item .env.example .env
notepad .env
```

Set at least:

```ini
AUTH_TOKEN=some-long-random-token
POSTGRES_PASSWORD=some-other-long-random-password
RACON_JOIN_CODE=RCN-PUBLIC-JOIN
RACON_AUTO_APPROVE_WORKERS=true
HF_TOKEN=                       # only for gated Hugging Face repos
```

`AUTH_TOKEN` protects the API, the web UI session and the admin dashboard. Do not leave the
development value in place if you enable Tailscale Funnel.

`.env` is shared with the desktop app, so it contains only portable values. Container-only
settings (`RACON_DATA_DIR=/data`, `RACON_MODEL_DIR=/models`, `RACON_RUNTIME=ollama`,
`RACON_OLLAMA_URL=http://host.docker.internal:11434` and `DATABASE_URL`) are set directly in
`docker-compose.yml`.

PostgreSQL is selected by `DATABASE_URL`; Compose builds it from the `POSTGRES_*` values:

```
postgresql+psycopg2://racon:<POSTGRES_PASSWORD>@racn-postgres:5432/racon
```

If `DATABASE_URL` is empty outside Compose, RACoN falls back to its existing SQLite file.
The admin dashboard shows which backend is active.

---

## 3. Start the stack

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f racn-coordinator
```

Expected: `racn-postgres` becomes `healthy`, then `racn-coordinator` becomes `healthy`, then
`racn-webapp` starts.

Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
# status ok
Invoke-RestMethod http://127.0.0.1:8765/worker/health
# status healthy, nodes 0
```

Or run the helper:

```powershell
.\scripts\start_server.ps1
```

The script reads `.env`, detects the Tailscale IP, builds the stack, waits for health and
prints the URLs.

For automatic startup at Windows sign-in (Docker Desktop, the correct Funnel target, and a
watchdog that restarts the coordinator), see
[WINDOWS_AUTOSTART.md](./WINDOWS_AUTOSTART.md).

---

## 4. URLs

| Surface | URL | Token |
|---|---|---|
| Desktop web UI | `http://<tailscale-ip>:3000/?token=<AUTH_TOKEN>` | query parameter, then stored |
| Admin dashboard | `http://<tailscale-ip>:8765/admin` | asks for `AUTH_TOKEN` (or `?token=`) |
| Worker ingress | `http://<tailscale-ip>:8765` | Ed25519 envelope, no shared token |
| Admin API | `http://<tailscale-ip>:8765/api/v1/admin/*` | `X-RACON-TOKEN` / Bearer |
| OpenAI-compatible | `http://<tailscale-ip>:8765/v1/*` | `X-RACON-TOKEN` / Bearer |

The web UI URL needs the token once because the container cannot inject a per-install token
the way the desktop shell does. The admin dashboard prompts for it and keeps it in
`sessionStorage`.

---

## 5. Admin dashboard

Open `/admin` and enter `AUTH_TOKEN`, or run:

```powershell
.\scripts\open_admin.ps1
```

It polls the server every four seconds:

- **Overview** — online/approved/pending workers, running/completed/failed jobs, database
  backend, coordinator identity, recent node-to-node routing.
- **Workers** — every enrolled node with CPU model and cores, live CPU %, RAM total and
  available (with a usage bar), GPU name and VRAM, OS, runtimes, RPC endpoint, heartbeat age,
  active tasks, and approve/revoke actions.
- **Live routing** — the `routing_events` log: `source → target`, event type, task/job id,
  state and message. It shows enrollment, heartbeats, scheduler assignments, worker claims,
  worker reports, approvals and revocations.
- **Agentic scheduler** — jobs with planner mode, execution mode, per-task state, assigned
  worker, duration, output/error, plus a test-task dispatch form.
- **Downloads & invites** — live model download progress and worker invite codes.
- **Diagnostics** — one-click checks for PostgreSQL, the inference runtime, Ollama,
  llama.cpp, installed models and disk, worker liveness, routing-log freshness, Tailscale and
  token strength. The same data is available at `GET /api/v1/admin/diagnostics`.
- **Smart router preview** — classify a prompt and see every scored model candidate, the
  selected model and the reason.

The log is durable in PostgreSQL (`routing_events`), so it survives a restart. The live node
registry is in memory by design; a worker re-enrolls automatically after a server restart.

---

## 6. Connect a worker

On each provider machine (Windows or Linux):

```powershell
$env:RACON_COORDINATOR_URL = "http://100.76.208.54:8765"
$env:RACON_JOIN_CODE = "RCN-PUBLIC-JOIN"
$env:RACON_DATA_DIR = "$env:LOCALAPPDATA\RACoN"

# Provider agent only (no GUI):
.\.venv\Scripts\python.exe -m racon --headless
```

or start the desktop shell with the same environment; it registers the local engine and the
provider agent can auto-join.

The worker is approved automatically (`RACON_AUTO_APPROVE_WORKERS=true`). Set it to `false`
and use the admin dashboard to approve nodes manually.

The worker needs a runtime that can serve the job's model:

- Ollama at `127.0.0.1:11434`, or
- a GGUF installed in the worker's RACoN model directory (the agent falls back to the local
  RACoN engine, llama.cpp or Ollama, when Ollama is absent).

Use the same model id on the server job and on the worker, or the task fails honestly with
`model_not_found`.

---

## 7. Model downloads

In the web UI open **Model Hub**. The Hugging Face entries are real, SHA-256 verified
downloads:

- `SmolLM2-360M-Instruct-Q4_K_M` (≈ 270 MB)
- `Qwen2.5-0.5B-Instruct-Q4_K_M` (≈ 398 MB)
- `Llama-3.2-1B-Instruct-Q4_K_M` (≈ 808 MB)

They land in the `racon-models` volume mounted at `/models`, shared with the model registry.
Downloads resume from `.part` files and retry transient connection drops.

API equivalent:

```powershell
$headers = @{ "X-RACON-TOKEN" = "<AUTH_TOKEN>"; "Content-Type" = "application/json" }
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/v1/models/pull `
  -Headers $headers -Body '{"model_name":"SmolLM2-360M-Instruct-Q4_K_M"}'

Invoke-RestMethod "http://127.0.0.1:8765/api/v1/models/pull/status?model_name=SmolLM2-360M-Instruct-Q4_K_M" `
  -Headers $headers
```

The Ollama-tag entries in the catalog (`llama3.2:1b`, `qwen2.5:0.5b`, …) are pulled by a
local Ollama daemon. If Ollama is not reachable at `OLLAMA_URL`, the pull fails with an
explicit `runtime_unavailable` instead of showing fake progress.

### 7.1 Smart routing

The server image ships llama.cpp, so a downloaded GGUF is immediately servable. Use
`model: "auto"` to let the deterministic router classify the request and pick a capable
installed model:

```powershell
$body = @{
  model = "auto"
  messages = @(@{ role = "user"; content = "Write a Python function that reverses a list" })
  max_tokens = 64
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post http://127.0.0.1:8765/v1/chat/completions `
  -Headers $headers -Body $body
```

The reply includes `racon.router.selected_model`, the classification and every scored
candidate. Preview the decision without generating:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/v1/scheduler/route `
  -Headers $headers -Body '{"prompt":"Prove this theorem step by step"}'
```

Explicit model ids are always honoured; only `auto` is routed. Agentic jobs also accept
`model_name: "auto"` and persist the router decision inside the job plan.

### 7.2 Worker model inventory

A provider agent advertises the Ollama tags and GGUF ids it can serve during enrollment.
The scheduler can then reject a worker that does not advertise the task's model, and a
worker whose heartbeat is older than 30 seconds is marked offline and excluded from
placement. The model list is bound into the signed enrollment payload when present; older
agents that send an empty list remain compatible.

---

## 8. PostgreSQL operations

```powershell
# Interactive shell
docker exec -it racn-postgres psql -U racon -d racon

# Inspect the network log
docker exec -it racn-postgres psql -U racon -d racon `
  -c "select timestamp, event_type, source_node, target_node, state from routing_events order by id desc limit 20;"

# Backup
docker exec racn-postgres pg_dump -U racon racon > racon-backup.sql

# Restore
Get-Content racon-backup.sql | docker exec -i racn-postgres psql -U racon -d racon
```

The schema is created with `Base.metadata.create_all` on startup; the new `routing_events`
table is additive and does not require a migration step.

Postgres is **not** published on the host; the coordinator reaches it as
`racn-postgres:5432` on the Compose network. If you need a remote SQL client, use
`docker exec` or add a temporary published port deliberately.

---

## 9. Windows firewall and Tailscale

Allow inbound access on the Tailscale interface only:

```powershell
New-NetFirewallRule -DisplayName "RACoN API"   -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
New-NetFirewallRule -DisplayName "RACoN Web"   -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
```

Test from another Tailscale device:

```powershell
Invoke-RestMethod http://100.76.208.54:8765/health
```

Do **not** enable Tailscale Funnel for `8765` unless you intend to expose the admin API to the
whole internet. The admin API is token protected, but the worker ingress is deliberately
public to the tailnet.

---

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| Worker says `connection error` | `RACON_COORDINATOR_URL` must be the host Tailscale IP and port 8765; verify `Invoke-RestMethod http://<ip>:8765/health` from the worker |
| Enrollment `rejected` | Join code mismatch (`RACON_JOIN_CODE` on both sides) or invite expired; check `docker compose logs racn-coordinator` |
| Node appears then goes offline | Heartbeats are sent every 5 s; the dashboard marks a node offline after 30 s without one. Check the worker log |
| Task fails `model_not_found` | The worker has no runtime serving that model id; install the same GGUF/Ollama tag |
| Download stalls or fails | Check `docker compose logs -f racn-coordinator`; transient drops resume automatically; verify free space with `docker system df` |
| `psycopg2` error | Ensure the image was rebuilt after adding `psycopg2-binary`; `docker compose build --no-cache racn-coordinator` |
| Admin page blank | Open the browser devtools; the API returns 401 until the correct `AUTH_TOKEN` is entered |
| Postgres unhealthy | `docker compose logs racn-postgres`; confirm the password in `.env` matches the volume's first initialization (change `.env` and delete the volume only if you accept data loss) |

---

## 11. Update / stop

```powershell
docker compose pull
docker compose up -d --build
docker compose down          # keeps volumes
docker compose down -v       # deletes PostgreSQL, models and data volumes
```
