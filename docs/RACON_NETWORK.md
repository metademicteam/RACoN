# RACoN Network Gateway

RACoN exposes one public OpenAI-compatible endpoint. An agent harness sends a RACoN API key
and either one worker answers (a query) or the planner decomposes the request across workers
and the synthesizer merges the streamed results (a task).

## Endpoint

```
Base URL: https://desktop-b7l73cl.tail6cb521.ts.net/v1
API key:  rk_...
Model:    racon
```

Use the key as either header:

```http
Authorization: Bearer rk_...
```
```http
x-api-key: rk_...
```

The key is scoped to `/v1/*` inference. It cannot open `/api/v1/admin/*`, change settings, or
read logs. The administrator token remains a server-side secret.

## Create a key

Admin console → **API keys** → **Create key**, or:

```bash
curl -s https://desktop-b7l73cl.tail6cb521.ts.net/api/v1/admin/keys \
  -H "X-RACON-TOKEN: $RACON_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"cline","rate_limit_per_min":60}'
```

The response contains `key` once. Only a SHA-256 digest is stored. Revoke with:

```bash
curl -X DELETE https://desktop-b7l73cl.tail6cb521.ts.net/api/v1/admin/keys/<id> \
  -H "X-RACON-TOKEN: $RACON_ADMIN_TOKEN"
```

## Models

`GET /v1/models` returns:

- `racon` — automatic: a query goes to one worker; a complex task is decomposed. When the
  Together AI layer is enabled, it first classifies chat vs task with the decision model.
- `racon-agent` — explicitly use the Together AI decision/agent/merge layer.
- `racon-single` — force one worker/model.
- `racon-task` — force planner decomposition.
- `racon-single:<model>` / `racon-task:<model>` — force the mode and pin a worker model.
- Every model advertised by an online, streaming-capable worker.
- Local coordinator models as a fallback.

## Query vs task

`racon` classifies the request deterministically:

- **Single** — short/quick requests, chat, code generation, and any request with `tools`.
  One worker streams the answer as ordinary OpenAI chunks.
- **Task** — deep, multi-deliverable, comparison/research requests. The planner creates a
  task DAG, tasks run on workers (preferring idle workers), and the synthesizer merges the
  results. Task progress is streamed as standard chunks with an empty `delta.content` and a
  `racon` block (`event: "task_delta"`, `task_id`, `title`, `text`). The final answer streams
  as normal `delta.content`, followed by a final chunk with `racon.status` and
  `racon.missing`.

Tools force the single-worker path: splitting an agent tool loop across machines would break
the conversation.

### Agentic scheduler (Together AI)

When `TOGETHER_ENABLED=true` and `TOGETHER_API_KEY` is set, `racon` and `racon-agent` go
through the agentic layer: the decision model classifies the request, normal chat is answered
by the agent model, and task requests are planned with tool calls, dispatched in parallel to
the fastest suitable workers, then merged and reviewed with a low token budget. Progress is
streamed in the `racon` sidecar; only the final merged answer arrives as `delta.content`.

See [AGENTIC_SCHEDULER.md](./AGENTIC_SCHEDULER.md) for the event shapes, scoring, fallbacks,
and environment variables. Requests with `tools` still use the deterministic single-worker
path.

## Examples

Non-streaming:

```bash
curl -s https://desktop-b7l73cl.tail6cb521.ts.net/v1/chat/completions \
  -H "Authorization: Bearer $RACON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "racon",
    "messages": [{"role":"user","content":"Explain QUIC in two sentences."}]
  }'
```

Streaming:

```bash
curl -N https://desktop-b7l73cl.tail6cb521.ts.net/v1/chat/completions \
  -H "Authorization: Bearer $RACON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"racon","messages":[{"role":"user","content":"Count from 1 to 5."}],"stream":true}'
```

Task with a pinned worker model:

```bash
curl -N https://desktop-b7l73cl.tail6cb521.ts.net/v1/chat/completions \
  -H "Authorization: Bearer $RACON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "racon-task:qwen2.5:0.5b",
    "messages": [{"role":"user","content":"Compare CPU and GPU inference for a small team."}],
    "stream": true
  }'
```

Tools:

```json
{
  "model": "racon-single",
  "messages": [{"role":"user","content":"What is the weather in Paris? Use the tool."}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "Get the current weather",
      "parameters": {"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}
    }
  }]
}
```

Tool calls are returned in the OpenAI shape (`finish_reason: "tool_calls"`, `tool_calls` with
`index`, `id`, `type`, `function.name`, JSON-string `function.arguments`), including while
streaming.

`response_format` is forwarded to the selected runtime. llama.cpp supports JSON schema /
grammar modes; Ollama maps `json_object` and `json_schema` to its `format` field. A backend
that cannot honor a format fails explicitly rather than silently returning prose.

## Agent harness setup

Any OpenAI-compatible harness works:

| Setting | Value |
|---|---|
| Base URL | `https://desktop-b7l73cl.tail6cb521.ts.net/v1` |
| API key | `rk_...` |
| Model | `racon` (or a listed worker model) |

Python:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://desktop-b7l73cl.tail6cb521.ts.net/v1",
    api_key="rk_...",
)
stream = client.chat.completions.create(
    model="racon",
    messages=[{"role": "user", "content": "Summarize QUIC."}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

## Worker requirements

A worker must run a build that advertises the `streaming_chat` feature. Older workers are
excluded from public chat routing because they cannot stream task deltas. Workers connect
outbound to the coordinator; no inbound port, public IP, or Tailscale membership on the
worker is required for the control plane.

The coordinator is the discovery point (coordinator-mediated mesh). It selects approved
workers with a fresh heartbeat (≤30 s), a matching model, and the streaming capability.
Each worker re-reads its installed Ollama tags and GGUF models and reports them (signed) in
every heartbeat, so models installed or removed after enrollment are reflected in routing
on the next beat. NAT'd workers work because they poll `/tasks/claim` and push
`/tasks/progress`.

## Limits

| Limit | Value |
|---|---|
| API key rate limit | configurable, default 60 requests/min |
| Streaming task first token | 90 s before the gateway retries another worker |
| Task mode prompt | 6,000 characters (planner limit) |
| Tasks per plan | 12 |
| Tool requests | always single-worker |
| Context window | `RACON_CONTEXT_SIZE` (API ceiling 262,144 tokens); each model is capped at its trained GGUF window |

The context ceiling must be set on the coordinator **and** on every worker: a worker whose
llama.cpp/Ollama was started with 8192 tokens rejects long prompts even when the gateway
accepts them. Set `RACON_CONTEXT_SIZE=262144` and `RACON_KV_CACHE_TYPE=q8_0` in each
worker's environment and restart its agent. The quantized KV cache keeps a 128k-262k window
inside a small machine's RAM; install a model whose trained context is that large to use the
full window.

## Troubleshooting

- `401 invalid_api_key` — key missing, revoked, or not `rk_`.
- `403 insufficient_scope` — the key was sent to a non-inference path.
- `429 rate_limit_exceeded` — check `Retry-After` and `X-RateLimit-*`.
- `404 model_not_found` — no online streaming worker or local model serves it.
- `503 no_worker` — no approved, fresh worker with a servable model.
- Task waits forever for a worker — the worker is likely an old build. Update it so it
  advertises `streaming_chat`; the coordinator excludes old workers from streamed jobs.
