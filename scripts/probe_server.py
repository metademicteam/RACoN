"""End-to-end probe for a running RACoN server.

It creates a synthetic worker with a real Ed25519 key, enrolls it, sends a heartbeat,
dispatches a task from the admin API, claims it as the worker, reports a signed result,
and prints the resulting admin state. Use it to verify a deployment without a second PC.

Usage:
    python scripts/probe_server.py --server http://100.76.208.54:8765 --token <AUTH_TOKEN>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocols import (  # noqa: E402
    EnrollmentRequest,
    HardwareCapabilities,
    HeartbeatMessage,
    NodeRole,
    PROTOCOL_VERSION,
    TaskResultEnvelope,
    TaskState,
)
from protocols.crypto import (  # noqa: E402
    canonicalize_json,
    generate_keypair,
    public_key_to_hex,
    sign_message,
)


def _headers(token: str) -> dict[str, str]:
    return {"X-RACON-TOKEN": token, "Authorization": f"Bearer {token}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a running RACoN server")
    parser.add_argument(
        "--server",
        default=os.environ.get("RACON_COORDINATOR_URL", "http://127.0.0.1:8765"),
    )
    parser.add_argument("--token", default=os.environ.get("AUTH_TOKEN", ""))
    parser.add_argument("--join-code", default=os.environ.get("RACON_JOIN_CODE", "RCN-PUBLIC-JOIN"))
    parser.add_argument("--model", default="qwen2.5:0.5b")
    args = parser.parse_args()

    base = args.server.rstrip("/")
    client = httpx.Client(timeout=15.0)
    private, public = generate_keypair()
    public_hex = public_key_to_hex(public)
    node_id = f"probe-{public_hex[:12]}"

    try:
        health = client.get(f"{base}/health").json()
        print(f"health      : {health}")
    except Exception as exc:
        print(f"ERROR: server is not reachable at {base}: {exc}", file=sys.stderr)
        return 2

    caps = HardwareCapabilities(
        cpu_name="RACoN probe CPU",
        cpu_cores=4,
        ram_total_mb=8192.0,
        ram_available_mb=6144.0,
        gpu_name=None,
        gpu_vram_mb=None,
        runtimes=["probe"],
        os_name="Probe/1.0",
        is_simulated=False,
        rpc_endpoint=None,
    )
    now = time.time()
    payload = canonicalize_json(
        {
            "node_id": node_id,
            "public_key_hex": public_hex,
            "invite_code": args.join_code,
            "role": NodeRole.CONTRIBUTOR.value,
            "capabilities": caps.model_dump(),
            "protocol_version": PROTOCOL_VERSION,
            "timestamp": now,
        }
    )
    enrollment = EnrollmentRequest(
        node_id=node_id,
        public_key_hex=public_hex,
        invite_code=args.join_code,
        role=NodeRole.CONTRIBUTOR,
        capabilities=caps,
        protocol_version=PROTOCOL_VERSION,
        timestamp=now,
        signature=sign_message(private, payload),
    )
    response = client.post(f"{base}/enroll", json=enrollment.model_dump())
    print(f"enroll      : HTTP {response.status_code} {response.text[:200]}")
    if response.status_code != 200:
        return 1
    enrolled = response.json()
    session_token = enrolled.get("session_token")
    if not session_token:
        print("ERROR: enrollment returned no session token", file=sys.stderr)
        return 1

    hb_now = time.time()
    nonce = uuid.uuid4().hex
    hb_payload = canonicalize_json(
        {
            "node_id": node_id,
            "timestamp": hb_now,
            "nonce": nonce,
            "cpu_util_pct": 7.5,
            "ram_available_mb": 6000.0,
            "active_tasks": [],
        }
    )
    heartbeat = HeartbeatMessage(
        node_id=node_id,
        timestamp=hb_now,
        nonce=nonce,
        cpu_util_pct=7.5,
        ram_available_mb=6000.0,
        active_tasks=[],
        signature=sign_message(private, hb_payload),
    )
    hb_response = client.post(f"{base}/heartbeat", json=heartbeat.model_dump())
    print(f"heartbeat   : HTTP {hb_response.status_code} {hb_response.text[:160]}")

    nodes = client.get(f"{base}/api/v1/admin/nodes", headers=_headers(args.token)).json()
    node = next((item for item in nodes if item["id"] == node_id), None)
    print(f"admin node  : {json.dumps(node, indent=2) if node else 'NOT VISIBLE'}")

    dispatch = client.post(
        f"{base}/api/v1/admin/tasks/dispatch",
        headers=_headers(args.token),
        json={
            "node_id": node_id,
            "model_name": args.model,
            "prompt": "Reply with exactly: probe-ok",
            "max_tokens": 8,
        },
    )
    print(f"dispatch    : HTTP {dispatch.status_code} {dispatch.text[:200]}")
    if dispatch.status_code != 200:
        return 1
    task_id = dispatch.json()["task_id"]

    claim = client.post(
        f"{base}/tasks/claim",
        json={"node_id": node_id, "session_token": session_token},
    )
    print(f"claim       : HTTP {claim.status_code} {claim.text[:240]}")
    if claim.status_code != 200 or not claim.json().get("has_task"):
        print("ERROR: the probe did not receive its task", file=sys.stderr)
        return 1

    result_payload = canonicalize_json(
        {
            "task_id": task_id,
            "node_id": node_id,
            "status": TaskState.COMPLETED.value,
            "result_data": {"response": "probe-ok", "runtime": "probe"},
            "execution_time_ms": 1.0,
            "error": None,
        }
    )
    result = TaskResultEnvelope(
        task_id=task_id,
        node_id=node_id,
        status=TaskState.COMPLETED,
        result_data={"response": "probe-ok", "runtime": "probe"},
        execution_time_ms=1.0,
        error=None,
        worker_signature=sign_message(private, result_payload),
    )
    report = client.post(f"{base}/tasks/report", json=result.model_dump())
    print(f"report      : HTTP {report.status_code} {report.text[:200]}")

    events = client.get(
        f"{base}/api/v1/admin/events?limit=20", headers=_headers(args.token)
    ).json()["events"]
    print("\nrecent routing events:")
    for event in events[-10:]:
        print(
            f"  {event['timestamp']}  {event['source']} -> {event['target']}  "
            f"{event['event_type']}  {event['state'] or ''}  {event['message']}"
        )

    overview = client.get(f"{base}/api/v1/admin/overview", headers=_headers(args.token)).json()
    print(f"\noverview    : {json.dumps(overview['nodes'])}")
    print("PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
