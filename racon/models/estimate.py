"""Memory estimation for GGUF models.

Everything here is derived from values the GGUF header actually contains. When a value
cannot be derived the field is ``None`` and ``confident`` is False — RACoN never fills in a
plausible-looking number.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from racon.runtime.distributed_gguf import inspect_gguf_metadata

logger = logging.getLogger(__name__)

#: Bytes per KV-cache element. llama.cpp defaults to f16 unless the operator sets
#: ``kv_cache_type``; q8_0 halves it, q4_0 quarters it.
KV_BYTES = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5, "q4_1": 0.5}


def _meta(meta: dict[str, Any], arch: str, key: str) -> Optional[float]:
    raw = (meta.get("metadata") or {})
    for candidate in (f"{arch}.{key}", key):
        if candidate in raw:
            try:
                return float(raw[candidate])
            except (TypeError, ValueError):
                return None
    return None


def estimate_memory(
    model_path: str | Path,
    context_size: int = 4096,
    kv_cache_type: str = "f16",
) -> dict[str, Any]:
    """Estimate resident memory for a GGUF model at a given context size.

    Returns a dict with ``weights_mb`` (measured file size), ``kv_cache_mb`` (derived from
    header metadata or ``None``), ``overhead_mb`` (documented heuristic), ``total_mb`` and
    ``confident``.
    """
    path = Path(model_path)
    size_bytes = path.stat().st_size
    weights_mb = round(size_bytes / (1024 * 1024), 1)

    result: dict[str, Any] = {
        "model": path.name,
        "context_size": context_size,
        "kv_cache_type": kv_cache_type,
        "weights_mb": weights_mb,
        "kv_cache_mb": None,
        "overhead_mb": None,
        "total_mb": None,
        "confident": False,
        "basis": "file size only",
    }

    try:
        meta = inspect_gguf_metadata(str(path))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Memory estimate metadata read failed: %s", exc)
        return result

    arch = str(meta.get("architecture") or "")
    n_layer = meta.get("block_count") or _meta(meta, arch, "block_count")
    n_embd = meta.get("embedding_length") or _meta(meta, arch, "embedding_length")
    n_head = _meta(meta, arch, "attention.head_count")
    n_kv_head = _meta(meta, arch, "attention.head_count_kv")
    key_len = _meta(meta, arch, "attention.key_length")
    value_len = _meta(meta, arch, "attention.value_length")

    if n_layer and n_embd and n_head and n_kv_head:
        head_dim = key_len or (float(n_embd) / float(n_head))
        value_dim = value_len or head_dim
        bytes_per_element = KV_BYTES.get(kv_cache_type, 2.0)
        kv_bytes = (
            2.0  # K and V
            * float(n_layer)
            * float(n_kv_head)
            * (float(head_dim) + float(value_dim))
            * float(context_size)
            * bytes_per_element
        )
        kv_mb = round(kv_bytes / (1024 * 1024), 1)
        # Compute buffers and graph memory scale with batch and context. 8% of weights is a
        # documented heuristic, reported separately from the measured values.
        overhead_mb = round(weights_mb * 0.08, 1)
        result.update(
            {
                "kv_cache_mb": kv_mb,
                "overhead_mb": overhead_mb,
                "total_mb": round(weights_mb + kv_mb + overhead_mb, 1),
                "confident": True,
                "basis": "gguf header: block_count, attention head counts, context size",
            }
        )
    else:
        result["overhead_mb"] = round(weights_mb * 0.08, 1)
        result["total_mb"] = round(weights_mb + (result["overhead_mb"] or 0.0), 1)
        result["basis"] = "file size only; GGUF header lacks attention geometry"

    return result


def estimate_fits(
    model_path: str | Path,
    available_ram_mb: float,
    context_size: int = 4096,
    kv_cache_type: str = "f16",
    headroom: float = 0.9,
) -> tuple[bool, dict[str, Any]]:
    """Decide whether a model fits in the given RAM budget.

    ``headroom`` keeps 10% of the machine's free memory for the OS by default.
    """
    estimate = estimate_memory(model_path, context_size, kv_cache_type)
    total = estimate.get("total_mb")
    if total is None:
        return False, estimate
    return total <= available_ram_mb * headroom, estimate
