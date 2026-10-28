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
| `racon/cluster/planner.py` | Pure function. Turns measured memory into contiguous layer ranges and a `--tensor-split`. |
| `racon/cluster/supervisor.py` | Tracks worker reachability, replans on membership change, restarts the head. |
| `racon/cluster/head.py` | Owns the `llama-server` process. Builds its command from a plan. |
| `racon/cluster/gateway.py` | Public API with API keys. Unchanged by scheduling. |

## 3. How a worker gets its share

1. The worker runs `python -m racon.cluster worker --bind <tailscale-ip>`. This exposes `rpc-server` on that IP only.
2. The head adds the worker to `cluster.json`.
3. On each reconcile, the supervisor:
   - checks the worker's TCP port,
   - runs `llama-cli --list-devices --rpc <live workers>` so llama.cpp reports each device's free memory,
   - computes each device's capacity:
     `capacity_layers = floor(free_mb * 0.7 / bytes_per_layer_mb)`,
     where `bytes_per_layer_mb` is the model file size divided by the block count,
   - splits layers in proportion to capacity (largest-remainder rounding, so the sum is exact),
   - drops any worker that receives fewer than 2 layers, and gives its layers back to the head,
   - fails the plan if the devices together cannot hold every layer.
4. The head restarts `llama-server` with `--rpc` in plan order and `-ts` set to the per-device fractions.

The 0.7 factor leaves about 30% of free memory for the KV cache and compute buffers.

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
- Requests in flight during the restart fail once. Clients should retry.

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
| Live split across two RPC workers and the head returns a correct answer | `live_plan.py`: layers 0-8 / 9-16 / 17-25, response `42` |

## 6. Limitations

- **Restart downtime.** Each membership change restarts `llama-server`. Requests in flight fail once. The model
  load time (seconds on this PC) sets the outage length.
- **Memory only.** Placement ignores compute speed. A fast worker and a slow worker with the same memory get the
  same share, so the slow one can limit throughput.
- **Per-layer size is an average.** Embedding and output-head tensors are spread across layers in this estimate.
  A large head tensor can make the real split differ from the plan.
- **Network latency.** Each token crosses every worker hop over Tailscale. Layer sharing helps memory, not speed,
  on high-latency links (ADR-012 requires under 5 ms RTT and at least 100 Mbps for pipeline execution).
- **Unmeasured memory.** If a worker's memory cannot be read (it is unreachable or llama.cpp fails to load its
  backend), it is excluded from the plan rather than guessed.
- **No speed-weighted scheduling.** Adding compute-aware placement is the next step.

## 7. Next steps

1. Speed-weighted placement: a short benchmark on enrollment, stored per worker, folded into the capacity score.
2. Live failover test: kill a worker mid-session and assert the head replans and the next request succeeds.
3. Restart-free membership: keep the head running and send layer changes via a reload, if llama.cpp adds that.
4. Per-worker quotas and rate limits on the gateway before opening it publicly.
