# RPC Layer Transfer on the Ubuntu Worker: Measurements

Measured on 2026-11-01 against the live cluster: Windows head `desktop-b7l73cl` (i3-10100, Intel UHD 630,
`127.0.0.1:8090`), Ubuntu worker `metademic` (i5-3210M, 16 GB, `100.125.77.125:50052`, Tailscale direct over
`192.168.1.16:41641`). Model: Gemma-3-1B Q4_K_M, 762.49 MiB, 26 repeating layers (~29.3 MiB/layer).

All split tests ran on temporary ports (`8091`) with `-dev RPC0` and did not touch the live head on `8090`.

## 1. Results

| Test | Setup | Load | Generation |
|---|---|---|---|
| Head-only (live, Vulkan) | `-ngl 26`, no `--rpc` | ~4 s to healthy | **9.9 tok/s** |
| Split, 2 layers | `llama-cli`, `-ngl 2 -dev RPC0 -ts 1.0` | 45.2 s total (load + 4 tokens) | **0.9 tok/s** |
| Split, 2 layers (repeat, same worker process) | identical command | 52.0 s total | 1.0 tok/s |
| Split, 4 layers | `llama-cli`, `-ngl 4` | 90.7 s total | **0.9 tok/s** |
| Split head, 2 layers | `llama-server` on `8091`, HTTP `/completion` | **43.1 s** to `/health` 200 | **0.65 tok/s** |
| Device probe | `llama-cli --list-devices --rpc ...` | 0.23 s | - |
| Old 12-layer cold test (log) | `llama-server`, `-ngl 12` | `llama_params_fit` alone took 41.89 s; 34 tensor dots then stalled >13 min | - |

Split-head evidence from `split-head.log.err`:

```
llama_model_load_from_file_impl: using device RPC0 (100.125.77.125:50052) (unknown id) - 15895 MiB free
load_tensors: offloading output layer to GPU
load_tensors: offloading 1 repeating layers to GPU
load_tensors: offloaded 2/27 layers to GPU
load_tensors: RPC0[100.125.77.125:50052] model buffer size =   324.59 MiB
```

While the split loaded, the worker's heartbeat reported 17-30% CPU (one core pegged is 25% of its 4 threads)
and 8.1-8.7 GB free RAM. The worker did hold and compute the assigned layers.

## 2. What the numbers mean

- **Effective transfer rate: ~1.3 MiB/s (~10 Mbps).** 2 layers = 58.6 MiB; going from 2 to 4 layers added
  45.5 s, so `58.6 MiB / 45.5 s = 1.29 MiB/s`. That is the network plus the worker's single-threaded
  `rpc-server` handling, not CPU compute.
- **The `-c` disk cache gave no reload benefit.** Repeating the identical 2-layer load on the same
  `rpc-server` process took 52.0 s, not the 45.2 s of the cold run. The cache neither shortened the
  transfer nor survived between loads here, so it only added disk writes on the critical path. That is why
  v1.0.9 runs `rpc-server` memory-only by default.
- **Generation is round-trip bound, not layer bound.** 2 and 4 layers both generate at 0.9 tok/s, i.e.
  ~1.1 s per token. llama.cpp issues many small synchronous RPC calls per token, and each one pays the
  Wi-Fi round-trip (idle `tailscale ping` is 2-30 ms, but under load it is far worse). Fewer layers reduce
  load time but barely help token latency.
- **A layer cap alone cannot make this link interactive.** A 2-layer split already costs 43 s of load and
  15x slower chat than the head GPU.
- **The idle RTT gate passes but does not predict this.** `max_worker_rtt_ms` (50 ms) sees a healthy idle
  link; throughput and latency under load are what matter. RACoN now records the measured rate.

## 3. What changed because of this

- `worker_layer_policy: "auto"` (default): split only when the head cannot hold the model. This model fits the
  head GPU, so the live head runs head-only and the worker stays idle. No restart churn, no 503 loop.
- `max_worker_layers`: explicit per-worker cap so a forced split's first load fits the window.
- Measured-rate cap: the supervisor stores MiB/s per worker in `worker_rates.json` and caps the next split so
  `worker_mb / rate` fits `rpc_startup_timeout_s` (default 300 s).
- `rpc_stall_timeout_s` (default 45 s): a load is aborted when the llama-server log stops growing, instead of
  waiting out the whole timeout.
- `rpc-server` runs **memory-only** by default (`-c` off via `RACON_RPC_CACHE=0`). The old worker ran with the
  local file cache, writing every received tensor to disk before the head could use it. Re-enable with
  `RACON_RPC_CACHE=1` or `racon.cluster worker --cache`.
- The head's own RPC endpoint is filtered out of discovery and the planner. It previously split 13/13 layers
  onto the head's own `rpc-server`, doubling the model on one PC and restarting the head every ~20 s.

## 4. Forcing a split anyway

Live config (`%LOCALAPPDATA%\RACoN\racon\cluster\cluster.json` on the head):

```json
{
  "worker_layer_policy": "always",
  "max_worker_layers": 2,
  "rpc_startup_timeout_s": 300,
  "rpc_stall_timeout_s": 45
}
```

Then restart `python -m racon.cluster serve`. The head log must show `using device RPC0`,
`offloading N repeating layers`, and `RPC0[...] model buffer size`. Expect ~45 s per 2 layers and ~0.9 tok/s
generation on this link. Use `"never"` to keep workers out unconditionally.

To reproduce the measurement without touching the live head:

```powershell
.\scripts\verify_rpc_worker.ps1 -WorkerHost 100.125.77.125 -Layers 2
```

It starts its own `llama-server` on port `8091`, checks the log for the RPC device lines, measures
generation, and stops the server. The manual equivalent:

```powershell
llama-cli.exe -m <model.gguf> --rpc 100.125.77.125:50052 -dev RPC0 -ngl 2 -c 512 -t 2 `
  -n 4 --no-warmup --single-turn -p "Say hi"
```

Scale `-ngl` and compare the deltas; `57.6 s increase / 2 layers / 29.3 MiB` gives the effective MiB/s.

## 5. Threshold for a useful split

At ~10 Mbps with ~40 ms RPC round-trips, layer splitting is only useful when the model does not fit the head
at all, and even then generation is slow. Practical targets: wired gigabit or 5 GHz Wi-Fi on the same LAN
(>500 Mbps, <5 ms RTT), which is the ADR-012 requirement. Re-measure after fixing the worker's link; the
supervisor automatically uses the new rate on the next load.
