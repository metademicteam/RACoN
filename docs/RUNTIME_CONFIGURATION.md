# Runtime configuration: llama.cpp vs Ollama

RACoN can serve models through either a bundled llama.cpp server or a separate Ollama
daemon. This document records the current deployment choice and the failure that was fixed.

## Symptom

API calls returned HTTP 503:

```json
{"error":{"message":"Cannot reach Ollama at http://host.docker.internal:11434. Start it with 'ollama serve'.","code":"runtime_unavailable"}}
```

Even requests for GGUF files that were installed in `/models` failed, and `model: "auto"`
failed as well.

## Causes

1. The desktop `.env` contained `RACON_RUNTIME=ollama`.
2. `docker-compose.yml` used `RACON_RUNTIME=${RACON_RUNTIME:-llama_cpp}`. Docker Compose
   gives the process environment and `.env` higher priority than the `:-` default, so the
   coordinator container inherited `ollama`.
3. Ollama is not installed on this Windows host, so
   `http://host.docker.internal:11434` is unreachable.
4. The running image was several hours old and predated per-model runtime selection:
   `EngineService.chat()` used the single configured runtime for every model, so even an
   installed GGUF was sent to Ollama.

## What changed

- `docker-compose.yml` now pins the container runtime:

  ```yaml
  - RACON_RUNTIME=${RACON_SERVER_RUNTIME:-llama_cpp}
  ```

  The desktop `.env` can no longer force Ollama into the container. An operator who
  deliberately wants Ollama in the container sets `RACON_SERVER_RUNTIME=ollama`.

- `.env` now uses `RACON_RUNTIME=llama_cpp`. This is the correct default for a host without
  Ollama; the desktop app and the container both ship llama.cpp.

- `EngineService.resolve_runtime()` returns `(runtime, model_id)` and maps known Ollama tags
  to an installed GGUF with the same catalog identity:

  | Requested | Served by | Model id |
  |---|---|---|
  | `Qwen2.5-0.5B-Instruct-Q4_K_M` | llama.cpp | same |
  | `qwen2.5:0.5b` | llama.cpp | `Qwen2.5-0.5B-Instruct-Q4_K_M` |
  | `ollama:qwen2.5:0.5b` | llama.cpp | `Qwen2.5-0.5B-Instruct-Q4_K_M` |
  | `smollm2:360m` | llama.cpp | `SmolLM2-360M-Instruct-Q4_K_M` |
  | `gemma-3-1b-racon` (not installed) | llama.cpp | 409 `model_storage_error` |
  | unknown Ollama tag with no GGUF equivalent | Ollama | original tag |

- The `racn-coordinator` image was rebuilt from the current source so it contains this
  logic.

## Verified results

Against `http://127.0.0.1:8765/v1/chat/completions` after the rebuild:

```
Qwen2.5-0.5B-Instruct-Q4_K_M   OK runtime=llama_cpp content='Hello.'
SmolLM2-360M-Instruct-Q4_K_M   OK runtime=llama_cpp content="'Hugs!'"
gemma-3-1b-racon               HTTP 409 not installed (no Ollama call)
qwen2.5:0.5b                   OK runtime=llama_cpp content='Hello.'
ollama:qwen2.5:0.5b            OK runtime=llama_cpp content='Hello.'
auto                           OK runtime=llama_cpp content='Hello.'
```

`GET /api/v1/engine/status` now reports `runtime.runtime = llama_cpp`,
`health.available = true`, and `available_runtimes = ["llama_cpp"]`.

## Commands

```powershell
# Apply configuration changes and rebuild the server image
docker compose build racn-coordinator
docker compose up -d --force-recreate racn-coordinator

# Confirm the container runtime
docker exec racn-coordinator env | Select-String RACON_RUNTIME

# Confirm the engine
curl.exe -s http://127.0.0.1:8765/api/v1/engine/status `
  -H "X-RACON-TOKEN: <AUTH_TOKEN>"
```

## Using Ollama instead

1. Install Ollama on the host and make sure `http://127.0.0.1:11434/api/tags` answers.
2. Set `RACON_SERVER_RUNTIME=ollama` in `.env` (or the shell before
   `docker compose up -d`).
3. Keep `RACON_OLLAMA_URL=http://host.docker.internal:11434`; the compose file already
   supplies it and `extra_hosts` maps `host.docker.internal` to the host gateway.
4. Recreate the coordinator container.

Files whose tags have an installed GGUF equivalent will still prefer the local GGUF, which
avoids downloading and loading the same model twice.

## Model availability

`GET /v1/models` lists catalog entries with `installed: false` as well. Those are not
servable until downloaded:

- Download through the Model Hub, or
- `POST /api/v1/models/pull` with `{"model_name": "gemma-3-1b-racon"}`.

Until then, use an installed GGUF id or `model: "auto"`.
