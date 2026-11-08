"""Measure RACoN API and agentic-scheduler latency against a live server.

Only real measurements are reported: client wall clock, server-reported durations, and
the per-call rows the engine wrote to ``inference_history``.

Examples::

    python scripts/bench_latency.py --mode chat --model Qwen2.5-0.5B-Instruct-Q4_K_M
    python scripts/bench_latency.py --mode job  --model Qwen2.5-0.5B-Instruct-Q4_K_M --max-tokens 2048
    python scripts/bench_latency.py --mode both --model my-model --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

DEFAULT_PROMPT = (
    "Research the trade-offs between CPU and GPU inference for small language models, "
    "compare them, and prepare a short recommendation."
)
TERMINAL = frozenset({"completed", "partial", "failed", "cancelled"})


class Client:
    def __init__(self, base_url: str, token: str, timeout: float = 900.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["X-RACON-TOKEN"] = token

    def request(self, method: str, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, headers=self.headers, method=method
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def stream(self, path: str, body: dict[str, Any]):
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={**self.headers, "Accept": "text/event-stream"},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=self.timeout)


def _sse_events(response):
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def bench_chat(client: Client, args) -> dict[str, Any]:
    body = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": args.prompt},
        ],
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "stream": True,
    }
    started = time.perf_counter()
    first_delta: Optional[float] = None
    usage: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    text: list[str] = []
    with client.stream("/v1/chat/completions", body) as response:
        for event in _sse_events(response):
            delta = (event.get("choices") or [{}])[0].get("delta") or {}
            if delta.get("content"):
                if first_delta is None:
                    first_delta = time.perf_counter()
                text.append(str(delta["content"]))
            if event.get("usage"):
                usage = event["usage"]
            if event.get("racon"):
                meta = event["racon"]
    total = time.perf_counter() - started
    return {
        "mode": "chat",
        "wall_s": round(total, 2),
        "ttft_s": round(first_delta - started, 2) if first_delta else None,
        "completion_tokens": usage.get("completion_tokens"),
        "tok_s": meta.get("generation_tokens_per_second"),
        "server_duration_ms": meta.get("duration_ms"),
        "server_ttft_ms": meta.get("time_to_first_token_ms"),
        "chars": len("".join(text)),
    }


def bench_job(client: Client, args) -> dict[str, Any]:
    before = client.request("GET", "/api/v1/engine/history?limit=200").get("history") or []
    before_ids = {row.get("request_id") for row in before}

    started = time.perf_counter()
    job = client.request(
        "POST",
        "/api/v1/jobs",
        {
            "prompt": args.prompt,
            "model_name": args.model,
            "max_tokens": args.max_tokens,
            "decompose": args.decompose,
            "execution_mode": args.execution_mode,
            "max_parallel_tasks": args.max_parallel_tasks,
        },
    )
    job_id = job["id"]
    transitions: list[tuple[str, float]] = []
    last: Optional[str] = None
    terminal: dict[str, Any] = {}
    while True:
        terminal = client.request("GET", f"/api/v1/jobs/{job_id}")
        status = str(terminal.get("status") or "")
        if status != last:
            transitions.append((status, round(time.perf_counter() - started, 2)))
            last = status
        if status in TERMINAL:
            break
        time.sleep(0.25)

    wall = time.perf_counter() - started
    after = client.request("GET", "/api/v1/engine/history?limit=200").get("history") or []
    calls = [
        {
            "duration_ms": row.get("duration_ms"),
            "ttft_ms": row.get("ttft_ms"),
            "completion_tokens": row.get("completion_tokens"),
            "tok_s": row.get("tokens_per_second"),
            "status": row.get("status"),
        }
        for row in reversed(after)
        if row.get("request_id") not in before_ids
    ]
    phases = {
        name: round(end - begin, 2)
        for (name, begin), (_, end) in zip(transitions, transitions[1:])
    }
    return {
        "mode": "job",
        "job_id": job_id,
        "status": terminal.get("status"),
        "wall_s": round(wall, 2),
        "server_duration_ms": terminal.get("duration_ms"),
        "planner_mode": terminal.get("planner_mode"),
        "tasks": len(terminal.get("tasks") or []),
        "result_chars": len((terminal.get("result") or "")),
        "error": terminal.get("error"),
        "transitions": transitions,
        "phase_s": phases,
        "inference_calls": calls,
    }


def _print_report(result: dict[str, Any]) -> None:
    if result["mode"] == "chat":
        print(
            f"chat  wall={result['wall_s']}s ttft={result['ttft_s']}s "
            f"tokens={result['completion_tokens']} tok/s={result['tok_s']} "
            f"server={result['server_duration_ms']}ms"
        )
        return
    print(
        f"job   {result['job_id']} status={result['status']} "
        f"wall={result['wall_s']}s server={result['server_duration_ms']}ms "
        f"tasks={result['tasks']} planner={result['planner_mode']}"
    )
    if result.get("error"):
        print(f"      error: {result['error']}")
    for name, seconds in (result.get("phase_s") or {}).items():
        print(f"      phase {name:14s} {seconds:8.2f}s")
    for index, call in enumerate(result.get("inference_calls") or [], start=1):
        print(
            f"      call {index}: dur={call['duration_ms']}ms "
            f"ttft={call['ttft_ms']}ms tokens={call['completion_tokens']} "
            f"tok/s={call['tok_s']} status={call['status']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("RACON_URL", "http://127.0.0.1:8765"))
    parser.add_argument(
        "--token",
        default=os.environ.get("RACON_AUTH_TOKEN") or os.environ.get("AUTH_TOKEN", ""),
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=("chat", "job", "both"), default="both")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--decompose", action="store_true")
    parser.add_argument(
        "--execution-mode", choices=("auto", "local", "distributed"), default="local"
    )
    parser.add_argument("--max-parallel-tasks", type=int, default=3)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    client = Client(args.base_url, args.token)
    results: list[dict[str, Any]] = []
    for _ in range(max(1, args.repeat)):
        if args.mode in ("chat", "both"):
            results.append(bench_chat(client, args))
        if args.mode in ("job", "both"):
            results.append(bench_job(client, args))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            _print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
