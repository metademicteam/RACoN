"""Verify the Hugging Face integration against the live Hub.

Checks that every built-in Hugging Face catalog entry exists on the Hub with the same
SHA-256 and size, then optionally downloads one artifact through the real model manager
(SHA-256 verified, GGUF header validated).

Usage:
    python scripts/verify_huggingface.py
    python scripts/verify_huggingface.py --download Qwen2.5-0.5B-Instruct-Q4_K_M
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def catalog_entries():
    from racon.models.catalog import build_catalog

    return [entry for entry in build_catalog() if entry.source == "huggingface"]


async def verify_catalog() -> bool:
    from racon.models.huggingface import HuggingFaceClient

    client = HuggingFaceClient()
    ok = True
    for entry in catalog_entries():
        url = entry.download_url or ""
        parts = url.split("huggingface.co/", 1)[-1].split("/")
        if len(parts) < 4:
            print(f"FAIL {entry.id}: unusable download URL {url}")
            ok = False
            continue
        repo_id = f"{parts[0]}/{parts[1]}"
        try:
            files = await client.list_gguf_files(repo_id)
        except Exception as exc:
            print(f"FAIL {entry.id}: {exc}")
            ok = False
            continue
        filename = entry.files[0].filename
        match = next((item for item in files if item.filename == filename), None)
        if match is None:
            print(f"FAIL {entry.id}: {filename} not found in {repo_id}")
            ok = False
            continue
        expected = entry.files[0].sha256
        if match.sha256 != expected:
            print(f"FAIL {entry.id}: Hub SHA {match.sha256} != catalog SHA {expected}")
            ok = False
            continue
        if match.size_bytes != entry.files[0].size_bytes:
            print(f"FAIL {entry.id}: Hub size {match.size_bytes} != catalog {entry.files[0].size_bytes}")
            ok = False
            continue
        print(f"OK   {entry.id}: {filename} {match.size_bytes} bytes sha={match.sha256[:16]}…")
    return ok


def download_model(model_id: str) -> bool:
    from racon.models.catalog import build_catalog, find_entry
    from racon.models.manager import ModelManager

    entry = find_entry(build_catalog(), model_id)
    if entry is None or not entry.download_url:
        print(f"No downloadable catalog entry named '{model_id}'.")
        return False

    models_dir = Path(tempfile.mkdtemp(prefix="racon-hf-verify-"))
    print(f"Downloading {entry.id} -> {models_dir}")
    manager = ModelManager(models_dir=models_dir)

    def progress(done: int, total: int) -> None:
        pct = (done / total * 100) if total else 0.0
        print(f"\r  {pct:5.1f}%  {done / 1048576:.1f} / {total / 1048576:.1f} MB", end="", flush=True)

    result = asyncio.run(manager.download(entry, progress=progress))
    print()
    print(
        f"OK   installed {result.model.id} path={result.model.path} "
        f"size={result.model.size_bytes} verified={result.verified}"
    )
    return result.verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", metavar="MODEL_ID", help="Download this catalog entry after verifying the catalog")
    args = parser.parse_args()

    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN"):
        print("Hugging Face token: configured (value not printed)")
    else:
        print("Hugging Face token: not configured (public repositories still work)")

    ok = asyncio.run(verify_catalog())
    if args.download and ok:
        ok = download_model(args.download)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
