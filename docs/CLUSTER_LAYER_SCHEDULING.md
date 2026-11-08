# Cluster Layer Scheduling and Fallback

This describes how RACoN splits a model's transformer layers across worker PCs, how it measures hardware, and
how it recovers when a worker drops. It extends [CLUSTER_API.md](CLUSTER_API.md) and follows ADR-007 and ADR-012.

## 1. Goals

- Run a model too large for one PC by assigning contiguous layer ranges to several PCs.
- Size each share from measured hardware, not from what a worker says about itself.
- Keep the main PC (the head) as the fallback: if a worker goes offline, its layers run on the head.
- Keep weights and activations on the private Tailscale network. Funnel carries only API traffic.

## 2. Components

| Module | Responsibility |
|---|---|
| `racon/cluster/probe.py` | Runs `llama-cli --list-devices --rpc ...` and reads each device's total and free memory. |
| `racon/cluster/discovery.py` | Reads approved workers from the coordinator and drops this PC's own RPC endpoint. |
| `racon/cluster/planner.py` | Pure function. Turns measured memory into contiguous layer ranges and a `--tensor-split`. |
| `racon/cluster/throughput.py` | Stores measured MiB/s per worker and turns it into a layer cap. |
| `racon/cluster/supervisor.py` | Tracks worker reachability, plans the split, restarts the head, records load rate. |
| `racon/cluster/head.py` | Owns the `llama-server` process. Builds its command and watches the load log. |
| `racon/cluster/gateway.py` | Public API with API keys. Unchanged by scheduling. |

## 3. How a worker gets its share

1. The worker runs `python -m racon.cluster worker --bind <tailscale-ip>`. This exposes `rpc-server` on that
   IP only, memory-only by default (`-c` off; set `RACON_RPC_CACHE=1` or `--cache` to keep tensors on disk).
2. The head discovers the worker from the coordinator registry. Its own RPC endpoint is filtered out: the head
   PC also enrolls as a contributor, and splitting to its own `rpc-server` doubles the model on one machine.
3. `worker_layer_policy` decides whether to split at all:
   - `auto` (default) - split only when the head cannot hold the whole model.
   - `always` - split whenever a worker is live (experiments, or a deliberately distributed setup).
   - `never` - never split.
   In `auto`, the head first probes only its own devices. When they hold the model, the worker is never
   contacted, so a slow link cannot delay or bounce startup.
4. On each reconcile the supervisor:
   - checks the worker's TCP port,
   - runs `llama-cli --list-devices --rpc <live workers>` so llama.cpp reports each device's free memory,
   - computes each device's capacity:
     `capacity_layers = floor(free_mb * 0.7 / bytes_per_layer_mb)`,
     where `bytes_per_layer_mb` is the model file size divided by the block count,
   - applies `max_worker_layers`, or a rate-based cap from `worker_rates.json` when no explicit cap is set,
   - splits layers in proportion to capacity (largest-remainder rounding, so the sum is exact),
   - drops any worker that receives fewer than 2 layers, and gives its layers back to the head,
   - fails the plan if the devices together cannot hold every layer.
5. The head restarts `llama-server` with `--rpc` in plan order and `-ts` set to the per-worker fractions.

The 0.7 factor leaves about 30% of free memory for the KV cache and compute buffers.

### Load window and measured rate

Every offloaded tensor must reach the worker before the head becomes healthy. On a slow link the transfer
can take minutes, so:

- `_wait_ready` watches the llama-server log. A log that stops growing for `rpc_stall_timeout_s`
  (default 45 s) aborts the attempt instead of waiting out the whole window.
- `rpc_startup_timeout_s` (default 300 s) is the hard cap for the load.
- On a successful split the supervisor records the actual `RPC0 model buffer size` and load time, writes the
  MiB/s to `worker_rates.json`, and caps the next split so `worker_mb / rate` fits the window.

See [RPC_LINK_MEASUREMENTS.md](RPC_LINK_MEASUREMENTS.md) for the live numbers on the Ubuntu worker.

### Why memory, not benchmarks

Memory is what limits whether a model fits. llama.cpp reports it per RPC device, so placement uses a number the
worker cannot misreport. Compute speed is not weighted yet. A benchmark on enrollment would add that, but it is a
separate change.

## 4. Failure and fallback

llama.cpp cannot add or remove devices in a running process. So a worker change means a head restart.

- A worker must fail two consecutive reachability checks before it is dropped. This avoids restarting for one
  dropped packet.
- On drop, the supervisor replans with the remaining workers. The dropped worker's layers move to the head,
  which holds the full model file (806 MB for Gemma-3-1B), so it can run any layer with no download.
- On recovery, the worker rejoins after one successful check and the head replans again.
- A split that stalls (no log growth for `rpc_stall_timeout_s`) or misses `rpc_startup_timeout_s` is killed and
  the head restarts head-only. That worker is disabled for the session so it cannot bounce the gateway.
- Live membership that does not change the split (for example `auto` keeping the head alone) does not restart
  the head.
- Requests in flight during a restart fail once. Clients should retry.

The Hugging Face repo (`toriqulhaque/RACoN-Gemma-3-1B-Shards`) is not used at runtime. It stays a distribution
option for storage-constrained workers that do not hold the full model, which the current RPC mode does not need.

## 5. What is verified

| Check | Evidence |
|---|---|
| Planner covers every layer exactly once | `tests/test_cluster_planner.py::test_plan_covers_every_layer_exactly_once` |
| Every measured worker gets a share | `test_every_measured_worker_gets_a_share` |
| Impossible plans are rejected, not silently truncated | `test_head_too_small_for_its_share_is_rejected` |
| Head command uses plan order and `-ts` | `test_head_command_uses_active_workers_and_split` |
| Worker drop and recovery replan the head | `test_supervisor_replans_after_worker_drops`, `test_supervisor_restores_worker_on_recovery` |
| Layer cap is honored, including a cap of 1 | `test_planner_caps_layers_per_worker`, `test_planner_cap_of_one_keeps_a_small_worker` |
| `auto` keeps workers out when the head fits, and does not restart | `test_auto_policy_keeps_workers_out_when_the_head_holds_the_model`, `test_auto_policy_does_not_restart_when_the_split_is_unchanged` |
| The head's own RPC endpoint is dropped | `test_supervisor_drops_the_heads_own_rpc_endpoint`, `tests/test_rpc_link.py::test_discovery_ignores_the_heads_own_endpoint` |
| `rpc-server` is memory-only unless asked | `tests/test_rpc_link.py::test_rpc_server_uses_memory_only_by_default` |
| Measured rate caps the next split | `test_supervisor_records_measured_rate_and_caps_the_next_split` |
| Live split across two RPC workers and the head returns a correct answer | `live_plan.py`: layers 0-8 / 9-16 / 17-25, response `42` |
| Ubuntu worker holds and computes layers on the live network | `docs/RPC_LINK_MEASUREMENTS.md`: `using device RPC0`, `RPC0 ... model buffer size = 324.59 MiB`, HTTP generation succeeded |

## 6. Limitations

- **Restart downtime.** Each split change restarts `llama-server`. Requests in flight fail once. A slow RPC
  transfer sets the outage length; a stalled one is cut at the stall timeout.
- **Mixed GPU + RPC.** RPC mode selects only the RPC devices (`-dev RPC0,...`), so the head's GPU is excluded
  and the head CPU runs the layers the plan leaves there. Head-only mode keeps the GPU offload.
- **Worker shard download.** The desktop Worker Agent downloads and verifies its assigned split-model
  shard in the background after enrollment and shows progress in the activity log. The RPC layer
  split does not wait for this download; layers still arrive from the head over the RPC connection.
- **Memory only.** Placement uses memory plus the measured transfer rate. It still ignores per-layer compute
  speed, so a slow CPU with a fast link can limit generation.
- **Per-layer size is an average.** Embedding and output-head tensors are spread across layers in this estimate.
  A large head tensor can make the real split differ from the plan.
- **Network latency.** Each token crosses every worker hop over Tailscale. Layer sharing helps memory, not speed,
  on high-latency links (ADR-012 requires under 5 ms RTT and at least 100 Mbps for pipeline execution).
  The Ubuntu worker measured ~10 Mbps and ~1.1 s per generated token, so it is not used by default.
- **Unmeasured memory.** If a worker's memory cannot be read (it is unreachable or llama.cpp fails to load its
  backend), it is excluded from the plan rather than guessed.
- **First split is unmeasured.** Without a stored rate the first forced split uses `max_worker_layers` (or the
  full memory plan when it is 0), so set the cap when the link is slow.

## 7. Next steps

1. Speed-weighted placement: a short benchmark on enrollment, stored per worker, folded into the capacity score.
2. Live failover test: kill a worker mid-session and assert the head replans and the next request succeeds.
3. Restart-free membership: keep the head running and send layer changes via a reload, if llama.cpp adds that.
4. Per-worker quotas and rate limits on the gateway before opening it publicly.
5. Measure a memory-only `rpc-server` on the Ubuntu worker after upgrading it to 1.0.9, to separate disk
   cache cost from network cost on that link.
