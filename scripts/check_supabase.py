"""Read-only Supabase connectivity check.

Reads `SUPABASE_URL` and a key from `.env` or the environment, then reports per-table HTTP
status and row counts. It never prints key values and performs GET requests only.

Usage:
    python scripts/check_supabase.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
TABLES = ("models", "model_versions", "model_endpoints", "nodes", "scheduler_decisions", "worker_enrollments")


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV.exists():
        return values
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    env = load_env()
    url = env.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    secret = env.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SECRET_KEY")
    publishable = env.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not url:
        print("SUPABASE_URL is not configured.")
        return 2

    key = secret or publishable
    if not key:
        print("No Supabase key is configured.")
        return 2

    import httpx

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    print(f"project host: {url.split('//')[-1]}")
    print(f"key type used: {'secret' if secret else 'publishable'}")

    try:
        with httpx.Client(timeout=20.0) as client:
            for table in TABLES:
                response = client.get(
                    f"{url.rstrip('/')}/rest/v1/{table}",
                    headers={**headers, "Prefer": "count=exact"},
                    params={"select": "id", "limit": "0"},
                )
                count = response.headers.get("content-range", "?").split("/")[-1]
                detail = ""
                if response.status_code >= 400:
                    try:
                        detail = response.json().get("message", "")
                    except Exception:
                        detail = response.text[:200]
                print(f"{table}: HTTP {response.status_code} count={count} {detail}")
    except Exception as exc:
        print(f"Supabase request failed: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
