"""Upload RACoN GGUF model shards to Hugging Face Hub."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from huggingface_hub import HfApi

def upload_shards(repo_id: str = "toriqulhaque/RACoN-Gemma-3-1B-Shards", shards_dir: str = "models/shards/gemma-3-1b"):
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF_TOKEN environment variable not set.", file=sys.stderr)
        sys.exit(1)

    api = HfApi(token=token)
    shards_path = Path(shards_dir)
    if not shards_path.exists():
        print(f"ERROR: Shards directory not found: {shards_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Uploading shards from {shards_path} to {repo_id}...")

    # Upload shards_manifest.json first
    manifest_file = shards_path / "shards_manifest.json"
    if manifest_file.exists():
        print(f"Uploading {manifest_file.name}...")
        api.upload_file(
            path_or_fileobj=str(manifest_file),
            path_in_repo=manifest_file.name,
            repo_id=repo_id,
            repo_type="model",
        )
        print(f"Uploaded {manifest_file.name}")

    # Upload each shard gguf file
    for shard in sorted(shards_path.glob("*.gguf")):
        print(f"Uploading {shard.name} ({shard.stat().st_size / (1024*1024):.1f} MB)...")
        api.upload_file(
            path_or_fileobj=str(shard),
            path_in_repo=shard.name,
            repo_id=repo_id,
            repo_type="model",
        )
        print(f"Uploaded {shard.name} successfully!")

    print("All shards uploaded successfully to Hugging Face Hub!")

if __name__ == "__main__":
    upload_shards()
