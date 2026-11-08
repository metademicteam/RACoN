"""Measured RPC transfer rates per worker.

A layer split only becomes healthy after every offloaded tensor reaches the worker. On a slow
link that takes minutes, so the head's load window decides whether the split survives. Rather
than guess, the supervisor records how fast each worker actually loaded (`worker_mb / seconds`)
and uses that rate to cap the next split so it fits the window.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

# Older samples stop describing the link after a laptop moves networks; ignore them.
MAX_AGE_S = 7 * 24 * 3600
# One unusually good or bad load should not redefine the link.
EMA_ALPHA = 0.4


def default_rates_path() -> Path:
    from racon.cluster.config import default_cluster_dir

    return default_cluster_dir() / "worker_rates.json"


def load_rates(path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    target = Path(path) if path else default_rates_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    rates: Dict[str, Dict[str, float]] = {}
    for address, entry in data.items():
        try:
            rate = float(entry["mb_per_s"])
            updated = float(entry["updated"])
        except (KeyError, TypeError, ValueError):
            continue
        if rate > 0 and now - updated <= MAX_AGE_S:
            rates[str(address)] = {"mb_per_s": rate, "updated": updated}
    return rates


def record_load(
    addresses: Iterable[str],
    worker_mb: float,
    seconds: float,
    path: Optional[Path] = None,
) -> Dict[str, Dict[str, float]]:
    """Record one successful load. The rate is shared across the workers in that load."""
    addresses = [a for a in addresses if a]
    if not addresses or worker_mb <= 0 or seconds <= 0:
        return load_rates(path)
    sample = worker_mb / len(addresses) / seconds
    rates = load_rates(path)
    now = time.time()
    for address in addresses:
        previous = rates.get(address, {}).get("mb_per_s")
        merged = sample if previous is None else previous + EMA_ALPHA * (sample - previous)
        rates[address] = {"mb_per_s": round(merged, 4), "updated": now}

    target = Path(path) if path else default_rates_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(rates, indent=2), encoding="utf-8")
        temp.replace(target)
    except OSError:
        pass
    return rates


def slowest_rate(addresses: Iterable[str], rates: Optional[Dict[str, Dict[str, float]]] = None) -> Optional[float]:
    """The slowest measured rate among these workers, or None when nothing is known."""
    table = load_rates() if rates is None else rates
    known = [table[a]["mb_per_s"] for a in addresses if a in table]
    return min(known) if known else None


def layers_that_fit(rate_mb_s: float, bytes_per_layer_mb: float, timeout_s: float, headroom: float = 0.8) -> int:
    """How many layers this link can move inside the load window, leaving room for head startup."""
    if rate_mb_s <= 0 or bytes_per_layer_mb <= 0 or timeout_s <= 0:
        return 0
    return max(0, int(timeout_s * headroom * rate_mb_s / bytes_per_layer_mb))
