"""RACoN GGUF Model Sharding and Partition Manifest Engine.

Handles physical splitting of large GGUF models across contributor nodes,
generates cryptographically verifiable shard manifests (SHA-256), and
inspects layer allocations for pipeline parallelism (ADR-012).
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import shutil
import struct
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Standard winget search path on Windows
DEFAULT_WINGET_PATTERN = r"C:\Users\*\AppData\Local\Microsoft\WinGet\Packages\ggml.llamacpp_*\*"


def find_llama_tool(tool_name: str) -> Optional[str]:
    """Find a llama.cpp binary by searching PATH and default install locations."""
    # 1. System PATH
    found = shutil.which(tool_name)
    if found and os.path.exists(found):
        return found

    # 2. Add .exe on Windows if not provided
    if not tool_name.endswith(".exe"):
        exe_name = f"{tool_name}.exe"
        found = shutil.which(exe_name)
        if found and os.path.exists(found):
            return found
    else:
        exe_name = tool_name

    # 3. Check WinGet Packages directory
    matches = glob.glob(DEFAULT_WINGET_PATTERN)
    for m in matches:
        candidate = os.path.join(m, exe_name)
        if os.path.isfile(candidate):
            return candidate

    return None


@dataclass
class ShardInfo:
    shard_index: int
    total_shards: int
    filename: str
    file_path: str
    file_size_bytes: int
    sha256: str
    tensor_count: int
    layer_start: int
    layer_end: int
    min_ram_mb: float


@dataclass
class GgufShardManifest:
    model_name: str
    architecture: str
    total_layers: int
    total_tensors: int
    original_file_size: int
    original_sha256: str
    split_strategy: str
    created_at: float
    shards: List[ShardInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GgufShardManifest:
        shards_data = data.pop("shards", [])
        manifest = cls(**data)
        manifest.shards = [ShardInfo(**s) for s in shards_data]
        return manifest


def compute_file_sha256(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 hash of a file efficiently in 1MB chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def inspect_gguf_metadata(file_path: str) -> Dict[str, Any]:
    """Extract header metadata and architecture information from a GGUF file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Model file not found: {file_path}")

    meta: Dict[str, Any] = {
        "file_size_bytes": os.path.getsize(file_path),
        "architecture": "unknown",
        "block_count": 0,
        "context_length": 0,
        "embedding_length": 0,
    }

    try:
        with open(file_path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                meta["is_valid_gguf"] = False
                return meta

            meta["is_valid_gguf"] = True
            version = struct.unpack("<I", f.read(4))[0]
            tensor_count = struct.unpack("<Q", f.read(8))[0]
            metadata_kv_count = struct.unpack("<Q", f.read(8))[0]
            meta["version"] = version
            meta["tensor_count"] = tensor_count
            meta["kv_count"] = metadata_kv_count

            def read_str(file_obj):
                length = struct.unpack("<Q", file_obj.read(8))[0]
                return file_obj.read(length).decode("utf-8", errors="replace")

            def read_val(file_obj, val_type):
                if val_type == 0: return struct.unpack("<B", file_obj.read(1))[0]
                elif val_type == 1: return struct.unpack("<b", file_obj.read(1))[0]
                elif val_type == 2: return struct.unpack("<H", file_obj.read(2))[0]
                elif val_type == 3: return struct.unpack("<h", file_obj.read(2))[0]
                elif val_type == 4: return struct.unpack("<I", file_obj.read(4))[0]
                elif val_type == 5: return struct.unpack("<i", file_obj.read(4))[0]
                elif val_type == 6: return struct.unpack("<f", file_obj.read(4))[0]
                elif val_type == 7: return bool(struct.unpack("<B", file_obj.read(1))[0])
                elif val_type == 8: return read_str(file_obj)
                elif val_type == 10: return struct.unpack("<Q", file_obj.read(8))[0]
                elif val_type == 11: return struct.unpack("<q", file_obj.read(8))[0]
                elif val_type == 12: return struct.unpack("<d", file_obj.read(8))[0]
                return None

            for _ in range(metadata_kv_count):
                key = read_str(f)
                vtype = struct.unpack("<I", f.read(4))[0]
                if vtype == 9:  # ARRAY
                    sub_type = struct.unpack("<I", f.read(4))[0]
                    arr_len = struct.unpack("<Q", f.read(8))[0]
                    if sub_type in (0, 1, 7): f.seek(arr_len, 1)
                    elif sub_type in (2, 3): f.seek(arr_len * 2, 1)
                    elif sub_type in (4, 5, 6): f.seek(arr_len * 4, 1)
                    elif sub_type in (10, 11, 12): f.seek(arr_len * 8, 1)
                    elif sub_type == 8:
                        for _ in range(arr_len):
                            slen = struct.unpack("<Q", f.read(8))[0]
                            f.seek(slen, 1)
                else:
                    val = read_val(f, vtype)
                    if key == "general.architecture":
                        meta["architecture"] = str(val)
                    elif "block_count" in key:
                        meta["block_count"] = int(val)
                    elif "context_length" in key:
                        meta["context_length"] = int(val)
                    elif "embedding_length" in key:
                        meta["embedding_length"] = int(val)
    except Exception as e:
        logger.warning("Partial read of GGUF metadata: %s", e)

    return meta


def split_gguf_model(
    model_path: str,
    output_dir: str,
    max_size_mb: int = 450,
    model_name: Optional[str] = None,
) -> GgufShardManifest:
    """Physically split a GGUF model into verifiable shards using llama-gguf-split.
    
    Generates and saves a cryptographic shards_manifest.json.
    """
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Source model does not exist: {model_path}")

    split_tool = find_llama_tool("llama-gguf-split")
    if not split_tool:
        raise RuntimeError("llama-gguf-split executable not found on system.")

    os.makedirs(output_dir, exist_ok=True)
    base_name = model_name or Path(model_path).stem
    out_prefix = os.path.join(output_dir, f"{base_name}-split.gguf")

    meta = inspect_gguf_metadata(model_path)
    orig_sha256 = compute_file_sha256(model_path)

    cmd = [
        split_tool,
        "--split-max-size", f"{max_size_mb}M",
        model_path,
        out_prefix,
    ]
    logger.info("Executing split command: %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    logger.info("Split tool output: %s", res.stdout)

    # Discover generated shards
    pattern = os.path.join(output_dir, f"{base_name}-split.gguf-*-of-*.gguf")
    shard_paths = sorted(glob.glob(pattern))
    if not shard_paths:
        raise RuntimeError(f"Split succeeded but no shard files found matching {pattern}")

    total_shards = len(shard_paths)
    total_layers = meta.get("block_count", 26)
    layers_per_shard = max(1, total_layers // total_shards)

    shards: List[ShardInfo] = []
    for idx, s_path in enumerate(shard_paths):
        s_size = os.path.getsize(s_path)
        s_sha = compute_file_sha256(s_path)
        s_meta = inspect_gguf_metadata(s_path)

        l_start = idx * layers_per_shard
        l_end = min(total_layers - 1, (idx + 1) * layers_per_shard - 1) if idx < total_shards - 1 else total_layers - 1

        # Memory required: shard weights + 25% KV-cache/activation buffer
        min_ram = (s_size / (1024 * 1024)) * 1.25

        shards.append(
            ShardInfo(
                shard_index=idx + 1,
                total_shards=total_shards,
                filename=os.path.basename(s_path),
                file_path=os.path.abspath(s_path),
                file_size_bytes=s_size,
                sha256=s_sha,
                tensor_count=s_meta.get("tensor_count", 0),
                layer_start=l_start,
                layer_end=l_end,
                min_ram_mb=round(min_ram, 1),
            )
        )

    import time
    manifest = GgufShardManifest(
        model_name=base_name,
        architecture=meta.get("architecture", "gemma3"),
        total_layers=total_layers,
        total_tensors=meta.get("tensor_count", 340),
        original_file_size=meta.get("file_size_bytes", os.path.getsize(model_path)),
        original_sha256=orig_sha256,
        split_strategy=f"size_{max_size_mb}MB",
        created_at=time.time(),
        shards=shards,
    )

    # Save manifest
    manifest_path = os.path.join(output_dir, "shards_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2)

    logger.info("Saved shard manifest to %s (%d shards)", manifest_path, len(shards))
    return manifest


def verify_shard(file_path: str, expected_sha256: str) -> bool:
    """Verify the SHA-256 integrity of a shard file."""
    if not os.path.exists(file_path):
        return False
    actual_sha = compute_file_sha256(file_path)
    return actual_sha.lower() == expected_sha256.lower()
