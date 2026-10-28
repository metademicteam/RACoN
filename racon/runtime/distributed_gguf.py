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


def bundled_llama_dir() -> Optional[Path]:
    """Folder that holds llama.cpp binaries shipped inside the installer, if any."""
    import sys

    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "llama")
    candidates.append(Path(sys.executable).resolve().parent / "llama")
    candidates.append(Path(__file__).resolve().parents[2] / "llama")
    for c in candidates:
        if (c / "rpc-server.exe").is_file() or (c / "llama-server.exe").is_file():
            return c
    return None


def find_llama_tool(tool_name: str) -> Optional[str]:
    """Find a llama.cpp binary: bundled with the app, then PATH, then the WinGet install."""
    exe_name = tool_name if tool_name.endswith(".exe") else f"{tool_name}.exe"

    # 1. Binaries shipped with RACoN (installer or PyInstaller bundle)
    bundled = bundled_llama_dir()
    if bundled and (bundled / exe_name).is_file():
        return str(bundled / exe_name)

    # 2. System PATH
    found = shutil.which(tool_name) or shutil.which(exe_name)
    if found and os.path.exists(found):
        return found

    # 3. Check WinGet Packages directory
    for m in glob.glob(DEFAULT_WINGET_PATTERN):
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
    download_url: Optional[str] = None


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
    hf_repo_id: Optional[str] = None
    shards: List[ShardInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GgufShardManifest:
        data_copy = dict(data)
        shards_data = data_copy.pop("shards", [])
        manifest = cls(**data_copy)
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


def load_manifest(manifest_path_or_dir: str) -> Optional[GgufShardManifest]:
    """Load and parse a shards_manifest.json from a file or directory."""
    p = Path(manifest_path_or_dir)
    if p.is_dir():
        p = p / "shards_manifest.json"
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return GgufShardManifest.from_dict(data)
    except Exception as e:
        logger.error("Failed to load shard manifest from %s: %s", p, e)
        return None


def select_optimal_shard(
    manifest: GgufShardManifest,
    available_ram_mb: float,
    is_master: bool = False,
) -> ShardInfo:
    """Select the optimal model shard based on host role and memory ceiling.
    
    In a 2-node pipeline:
    - Master / Coordinator holds Shard 1 (layers 0..12 + embeddings).
    - Worker RPC nodes hold Shard 2 (layers 13..25 + attention & head).
    If multiple shards exist, worker nodes select the shard matching available RAM.
    """
    if not manifest.shards:
        raise ValueError("Manifest contains no shards.")

    if is_master:
        # Master node holds the first shard (embeddings & early layers)
        return manifest.shards[0]

    # For worker nodes: filter candidate shards that fit within available RAM
    candidates = [s for s in manifest.shards[1:] if s.min_ram_mb <= available_ram_mb]
    if candidates:
        # Pick the most capable shard that fits comfortably
        return candidates[0]

    # If RAM is constrained, pick the shard with the smallest min_ram_mb requirement
    return min(manifest.shards, key=lambda s: s.min_ram_mb)


def download_shard(
    shard: ShardInfo,
    dest_dir: str,
    progress_callback: Optional[Any] = None,
) -> str:
    """Download and verify a shard from Hugging Face Hub or direct download URL.
    
    Verifies SHA-256 before declaring ready.
    """
    import httpx
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target_file = dest / shard.filename

    # If already exists and valid, skip download
    if target_file.exists() and verify_shard(str(target_file), shard.sha256):
        logger.info("Shard %s already exists and verified via SHA-256.", shard.filename)
        return str(target_file)

    # Check SSD wear guard budget before commencing download
    from racon.runtime.storage_guard import get_storage_guard, StorageQuotaExceededError
    guard = get_storage_guard()
    if not guard.check_write_allowed(shard.file_size_bytes):
        raise StorageQuotaExceededError(
            f"Daily SSD write guard triggered: downloading {shard.file_size_bytes / (1024**2):.1f} MB "
            f"would exceed the daily write quota ({guard.load_metrics().gb_limit:.1f} GB/day)."
        )

    # Determine URL
    url = shard.download_url
    if not url:
        repo_id = "toriqulhaque/RACoN-Gemma-3-1B-Shards"
        url = f"https://huggingface.co/{repo_id}/resolve/main/{shard.filename}"

    logger.info("Downloading shard %s from %s (%d bytes)...", shard.filename, url, shard.file_size_bytes)
    tmp_file = dest / f"{shard.filename}.tmp"
    h = hashlib.sha256()
    downloaded_bytes = 0

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download shard: HTTP {resp.status_code} from {url}")
        total = int(resp.headers.get("Content-Length", shard.file_size_bytes))
        with open(tmp_file, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
                    h.update(chunk)
                    downloaded_bytes += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded_bytes, total)

    actual_sha = h.hexdigest()
    if actual_sha.lower() != shard.sha256.lower():
        if tmp_file.exists():
            tmp_file.unlink()
        raise ValueError(
            f"SHA-256 integrity check failed for {shard.filename}! Expected {shard.sha256}, got {actual_sha}"
        )

    # Atomically replace target
    if target_file.exists():
        target_file.unlink()
    tmp_file.rename(target_file)
    guard.record_write(downloaded_bytes)
    logger.info("Shard %s downloaded and SHA-256 verified successfully.", shard.filename)
    return str(target_file)


def ensure_worker_shard(
    manifest_source: Optional[str] = None,
    available_ram_mb: float = 4096.0,
    is_master: bool = False,
    target_dir: Optional[str] = None,
    progress_callback: Optional[Any] = None,
) -> Tuple[ShardInfo, str]:
    """Discover, select, download, and verify the optimal layer shard for this worker.
    
    Returns (ShardInfo, verified_file_path).
    """
    import time
    from racon.config import get_data_dir

    # Search for local manifest or use default bundled path
    manifest: Optional[GgufShardManifest] = None
    search_paths = []
    if manifest_source:
        search_paths.append(manifest_source)
    search_paths.extend([
        "models/shards/gemma-3-1b/shards_manifest.json",
        str(get_data_dir() / "models" / "shards" / "gemma-3-1b" / "shards_manifest.json"),
    ])

    for sp in search_paths:
        manifest = load_manifest(sp)
        if manifest:
            break

    if not manifest:
        # Fallback to default remote Hugging Face manifest definition
        manifest = GgufShardManifest(
            model_name="gemma-3-1b",
            architecture="gemma3",
            total_layers=26,
            total_tensors=340,
            original_file_size=806059360,
            original_sha256="fcd05a80d8f617383259bf674729d35392664829ff9e467600b5392e11212d8c",
            split_strategy="size_450MB",
            created_at=time.time(),
            hf_repo_id="toriqulhaque/RACoN-Gemma-3-1B-Shards",
            shards=[
                ShardInfo(
                    shard_index=1,
                    total_shards=2,
                    filename="gemma-3-1b-split.gguf-00001-of-00002.gguf",
                    file_path="",
                    download_url="https://huggingface.co/toriqulhaque/RACoN-Gemma-3-1B-Shards/resolve/main/gemma-3-1b-split.gguf-00001-of-00002.gguf",
                    file_size_bytes=451855520,
                    sha256="873135c360e50b1c72aa2e3ac828dd8bfdf5c802517e69e19167684f8ac20b16",
                    tensor_count=90,
                    layer_start=0,
                    layer_end=12,
                    min_ram_mb=542.2,
                ),
                ShardInfo(
                    shard_index=2,
                    total_shards=2,
                    filename="gemma-3-1b-split.gguf-00002-of-00002.gguf",
                    file_path="",
                    download_url="https://huggingface.co/toriqulhaque/RACoN-Gemma-3-1B-Shards/resolve/main/gemma-3-1b-split.gguf-00002-of-00002.gguf",
                    file_size_bytes=354204064,
                    sha256="5ade62cc0e9e222248a9b6a9b7d98b821f9ffa9e38348a588fce62140f1ea538",
                    tensor_count=250,
                    layer_start=13,
                    layer_end=25,
                    min_ram_mb=425.0,
                ),
            ],
        )

    shard = select_optimal_shard(manifest, available_ram_mb=available_ram_mb, is_master=is_master)

    # Destination directory
    if not target_dir:
        # Check if local repo folder exists
        local_repo_shard = Path("models/shards/gemma-3-1b") / shard.filename
        if local_repo_shard.exists() and verify_shard(str(local_repo_shard), shard.sha256):
            return shard, str(local_repo_shard.resolve())
        target_dir = str(get_data_dir() / "models" / "shards" / "gemma-3-1b")

    path = download_shard(shard, target_dir, progress_callback=progress_callback)
    return shard, path

