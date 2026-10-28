# RACoN Multi-PC Cluster and Public API

This runs one model (Gemma-3-1B GGUF) split across several Windows PCs with llama.cpp RPC. The PCs talk over
Tailscale. A small gateway with API keys is the only public surface, so agents can call it over HTTPS.

## Architecture

```
 Agent harness ──HTTPS + API key──▶ Tailscale Funnel ──▶ Gateway :8000   (head PC, public)
                                                            │ key check, size limit, /v1/* only
                                                            ▼
                                                       llama-server :8080  (head PC, loopback only)
                                                            │  --rpc (layer split over Tailscale)
                              ┌─────────────────────────────┴──────────────────────────┐
                              ▼                                                        ▼
                   rpc-server 100.x.a.b:50052                          rpc-server 100.x.c.d:50052
                   (worker PC, Tailscale IP only)                      (worker PC, Tailscale IP only)
```

Rules enforced by the code:

- Only `/health` and `/v1/chat/completions`, `/v1/models`, `/v1/completions`, `/v1/embeddings` are reachable through
  the gateway. Everything else returns 404.
- Every `/v1/*` call needs `Authorization: Bearer rk_...`. Keys are stored as SHA-256 digests only.
- The head binds to `127.0.0.1` with its own random key, so the gateway is the only path to it.
- `rpc-server` has no authentication, so it binds to one Tailscale IP. `0.0.0.0` is refused.
- Tensors and weights never go through Funnel. Funnel carries only API requests and responses (ADR-007).
  Workers reach the head over Tailscale, not Funnel (ADR-012).

## Setup

Run these on each PC. The head PC is where the gateway runs.

For the public `racon.metademic.org` deployment (Cloudflare Tunnel instead of Funnel, plus the website), see
[WEBSITE_CLOUDFLARE.md](WEBSITE_CLOUDFLARE.md). That guide replaces step 8 below.

1. Install llama.cpp: `winget install ggml.llamacpp`. Confirm `llama-server` and `rpc-server` are on PATH.
2. Install Tailscale and sign every PC into the same tailnet.
3. On each **worker** PC, start the RPC worker on its Tailscale IP:
   ```powershell
   python -m racon.cluster worker --bind 100.x.y.z --port 50052
   ```
4. On the **head** PC, write the config and add the worker addresses:
   ```powershell
   python -m racon.cluster init --model-path "F:\RACoN\Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_Q4_k_m.gguf"
   ```
   Then edit `%LOCALAPPDATA%\RACoN\racon\cluster\cluster.json` and set `workers`:
   ```json
   "workers": [
     {"name": "pc-b", "host": "100.x.y.z", "port": 50052}
   ]
   ```
5. Check worker reachability: `python -m racon.cluster status`
6. Create an API key for each agent: `python -m racon.cluster keys create laya-agent`. The key is shown once.
7. Start the head and gateway: `python -m racon.cluster serve`
8. Publish the gateway: `.\scripts\cluster_funnel.ps1 -Mode on`. Only do this after step 7 is healthy. Funnel makes
   the API reachable from the public internet.

## Calling the API

```bash
curl https://<your-node>.<tailnet>.ts.net/v1/chat/completions \
  -H "Authorization: Bearer rk_..." \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma-3-1b","messages":[{"role":"user","content":"Hello"}],"max_tokens":64}'
```

Any OpenAI-compatible client works. Set `base_url` to `https://<node>.<tailnet>.ts.net/v1` and `api_key` to the `rk_` key.

Key management: `keys list`, `keys revoke <id>`. A revoked key stops working on the next request.

## Layer scheduling

The head measures each device with `llama-cli --list-devices` and splits layers in proportion to free memory. A
worker that drops is removed and its layers return to the head. See
[CLUSTER_LAYER_SCHEDULING.md](CLUSTER_LAYER_SCHEDULING.md) for the algorithm, failure policy and limits.

## Limits and honesty notes

- Gemma-3-1B is a small model. Output quality is limited (a 15+27 test returned "22" once in our live run).
  Distribution is for the model being sharded across PCs, not for higher quality.
- Layer splitting over Tailscale adds per-token network round trips. Keep workers on a fast network. Funnel itself
  is not used for the split.
- The gateway is a single process. Rate limits are in memory: 30 requests/min per client IP and 60 requests/min per
  API key on `/v1/*`. Counters reset on restart. There are no billing or per-key token quotas yet.
- `max_tokens` above `max_tokens_limit` (default 2048) is rejected with `400`.
- The 421M `laya` checkpoint is a classifier, not a chat model, so it is not served here. It stays the scheduler
  advisor.
- Each PC keeps its own memory. The cluster does not present a pooled RAM or VRAM space (ADR-012).
