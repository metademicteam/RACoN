# Worker Protocol

**Status:** implemented in `protocols/`, `racon/coordinator/`, `racon/agent/worker.py`
**Protocol version:** 1.0.0

---

## 1. Identity and trust

Each worker generates an Ed25519 keypair (`racon/agent/crypto_store.py`). Its `node_id` is
`node-<sha256(public_key_hex)[:12]>` unless overridden. The coordinator assigns a trust
state: `pending`, `approved`, `restricted`, `revoked`, `offline`.

All signed payloads use canonical JSON (`sort_keys=True`, compact separators, UTF-8) and
Ed25519 signatures encoded as 128 hex characters (`protocols/crypto.py`). The signature
field is excluded from its own payload.

## 2. Enrollment

`POST /enroll` with `EnrollmentRequest`:

```json
{
  "node_id": "node-...",
  "public_key_hex": "<64 hex>",
  "invite_code": "...",
  "role": "contributor",
  "capabilities": {
    "cpu_name": "...", "cpu_cores": 8,
    "ram_total_mb": 32768, "ram_available_mb": 16384,
    "gpu_name": "...", "gpu_vram_mb": 8192,
    "runtimes": ["ollama", "local"],
    "os_name": "Windows 11", "is_simulated": false,
    "rpc_endpoint": "100.x.x.x:50052", "assigned_shards": []
  },
  "protocol_version": "1.0.0",
  "timestamp": 1730000000.0,
  "signature": "<128 hex>"
}
```

Response: `status`, `trust_state`, `session_token` (15-minute expiry), `token_expiry`,
`coordinator_public_key`, optional `gateway_key`.

The coordinator verifies the request signature and the invite code before registering the
node. A rejected enrollment never creates a node record.

## 3. Heartbeat

`POST /heartbeat` every ~5 s with a signed `HeartbeatMessage`:

```json
{
  "node_id": "node-...",
  "timestamp": 1730000000.0,
  "nonce": "<16-64 chars>",
  "cpu_util_pct": 23.4,
  "ram_available_mb": 15200.0,
  "active_tasks": ["task-..."],
  "signature": "<128 hex>"
}
```

`active_tasks` carries the task currently executing on the worker and is empty otherwise.
Heartbeats run on a dedicated thread so a long generation cannot make the worker look
offline.

Acknowledgement commands: `re_enroll` (coordinator lost the node), `terminate` (revoked),
`auth_failure`. The agent re-enrolls automatically when a join code is configured.

## 4. Task claim

`POST /tasks/claim` with `{node_id, session_token}`.

Claiming is atomic and exactly-once from the coordinator's perspective:

1. find a task for this node in state `assigned`
2. if `expiry` has passed, mark it `failed` (`Task expired before a worker claimed it.`)
   and continue
3. otherwise transition to `running`, record `started_at`, and return the full signed
   envelope

A second claim while the task is `running` returns `has_task: false`. There is no lease
renewal yet; a worker that dies mid-task leaves the task `running` until an operator
intervenes.

## 5. Coordinator signature verification

The returned envelope contains `data_class`, `expiry`, `idempotency_key`, and
`coordinator_signature`. The worker verifies the signature with the coordinator public key
stored at enrollment **before executing anything**. A task that cannot be verified is
rejected and never executed.

## 6. Execution

Allow-listed task types only:

| Type | Behaviour |
|---|---|
| `benchmark_probe` | deterministic integer loop; returns checksum + measured wall time |
| `text_completion` | real `POST http://127.0.0.1:11434/api/generate` on the worker; empty/unreachable → error |
| `distributed_inference` | llama.cpp RPC pipeline over assigned shards |
| `shard_verification` | SHA-256 verification of a downloaded shard |

Anything else is rejected with `Disallowed task type`. There is no shell execution,
filesystem browsing, or arbitrary code path.

## 7. Result report

`POST /tasks/report` with `TaskResultEnvelope`:

```json
{
  "task_id": "task-...",
  "node_id": "node-...",
  "status": "completed",
  "result_data": {"response": "...", "runtime": "ollama", "eval_count": 128},
  "execution_time_ms": 4200.0,
  "error": null,
  "worker_signature": "<128 hex>"
}
```

The coordinator verifies the worker signature **and** that `node_id` matches the node the
task was assigned to. Results for unknown tasks, terminal tasks, or a different node are
rejected.

## 8. Size limits

Declared in `protocols/models.py`: envelope 64 KiB, result 256 KiB, heartbeat 8 KiB.
Field-level bounds are enforced by Pydantic. The orchestration layer additionally caps task
output at 20,000 characters and dependency context at 12,000 characters.

## 9. Known limitations

1. Coordinator node and task records are in memory; a coordinator restart forgets them.
   Agents recover by re-enrolling on the `re_enroll` command, but in-flight tasks are lost.
   Durable coordinator state is the next Phase 3 item.
2. No progress endpoint yet; progress is inferred from job/task state transitions.
3. The admin API (`127.0.0.1:8766`) has no authentication. It must never be published.
