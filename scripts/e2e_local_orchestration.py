"""End-to-end orchestration proof on one machine.

Submits a real job through the HTTP API, lets the orchestration service plan, dispatch to
the local llama.cpp runtime, synthesize, and persist the result. Exits non-zero unless a
real answer was produced.

Usage:
    python scripts/e2e_local_orchestration.py --model <gguf-stem>
    python scripts/e2e_local_orchestration.py --model <gguf-stem> --decompose

Requires a working `llama-server` (see README prerequisites) and a GGUF discoverable by the
model manager. Orchestration records are isolated to a temporary database; engine inference
history still uses the standard per-user database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOKEN = os.environ.get("RACON_CLIENT_TOKEN") or os.environ.get("AUTH_TOKEN", "")


def discover_model() -> str | None:
    for folder in (ROOT, ROOT / "models"):
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.glob("*.gguf")):
            return candidate.stem
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("RACON_E2E_MODEL") or discover_model())
    parser.add_argument("--prompt", default="Reply with exactly this sentence: RACoN end-to-end test passed.")
    parser.add_argument("--max-tokens", type=int, default=48)
    parser.add_argument("--decompose", action="store_true")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    if not args.model:
        print("No GGUF model found. Pass --model <stem> or set RACON_E2E_MODEL.")
        return 2

    os.environ.setdefault("RACON_AUTH_TOKEN", TOKEN)
    os.environ.setdefault("RACON_DEV_MODE", "true")
    os.environ.setdefault("RACON_CONTEXT_SIZE", "2048")
    os.environ.setdefault("RACON_N_GPU_LAYERS", "0")

    tmp_dir = Path(tempfile.mkdtemp(prefix="racon-e2e-"))
    import backend.racon.storage.db as db_module

    db_module.get_db_path = lambda: tmp_dir / "e2e.db"

    from fastapi.testclient import TestClient

    from backend.racon.api.app import app

    with TestClient(app, headers={"X-RACON-TOKEN": TOKEN}) as client:
        status = client.get("/api/v1/engine/status").json()
        runtime = status.get("runtime", {})
        print(f"runtime={runtime.get('runtime')} available={status.get('health', {}).get('available')}")
        if not status.get("health", {}).get("available"):
            print("Runtime is not available; install llama.cpp first.")
            return 2

        response = client.post(
            "/api/v1/jobs",
            json={
                "name": "E2E local proof",
                "model_name": args.model,
                "prompt": args.prompt,
                "max_tokens": args.max_tokens,
                "decompose": args.decompose,
                "execution_mode": "local",
                "max_parallel_tasks": 2,
            },
        )
        print("submit:", response.status_code)
        if response.status_code != 202:
            print(json.dumps(response.json(), indent=2)[:2000])
            return 1

        job_id = response.json()["id"]
        deadline = time.time() + args.timeout
        last = None
        while time.time() < deadline:
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            marker = (job["status"], tuple((t["task_id"], t["state"]) for t in job.get("tasks", [])))
            if marker != last:
                print(f"[{time.strftime('%H:%M:%S')}] status={job['status']} tasks={marker}")
                last = marker
            if job["status"] in ("completed", "partial", "failed", "cancelled"):
                break
            time.sleep(2)

        print(json.dumps(job, indent=2)[:6000])
        usage = client.get("/api/v1/engine/usage").json()
        print("engine usage:", json.dumps(usage))

        ok = job["status"] in ("completed", "partial") and bool((job.get("result") or "").strip())
        print("E2E_RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
