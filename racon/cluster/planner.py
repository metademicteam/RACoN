"""Layer placement: split a model's transformer blocks across the head and RPC workers.

Pure functions only. Inputs are measured free memory per device; outputs are contiguous, non-overlapping
layer ranges that cover every block exactly once. Workers that cannot hold a useful share are dropped
and their layers fall back to the head.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

HEAD = "head"
# Fraction of a device's free memory the model may use. The rest covers KV cache and compute buffers.
DEFAULT_MEMORY_FRACTION = 0.7
# A worker must be able to hold at least this many layers to be worth a network hop.
DEFAULT_MIN_WORKER_LAYERS = 2


@dataclass(frozen=True)
class LayerAssignment:
    device: str
    layer_start: int
    layer_end: int  # inclusive

    @property
    def count(self) -> int:
        return self.layer_end - self.layer_start + 1


@dataclass(frozen=True)
class Plan:
    total_layers: int
    device_order: Tuple[str, ...]  # matches llama.cpp --rpc ordering after the head
    assignments: Tuple[LayerAssignment, ...]
    dropped_workers: Tuple[str, ...]

    def layers_for(self, device: str) -> int:
        return sum(a.count for a in self.assignments if a.device == device)

    @property
    def tensor_split(self) -> List[float]:
        """Fractions for devices that hold layers, in llama.cpp order: head first, then workers in --rpc order.

        Devices with zero layers are omitted so llama.cpp never receives a zero-weight entry.
        Returns an empty list when only the head holds layers and no RPC device is in use.
        """
        if not self.device_order:
            return []
        order = (HEAD,) + self.device_order
        counts = [(d, self.layers_for(d)) for d in order]
        counts = [(d, c) for d, c in counts if c > 0]
        total = sum(c for _, c in counts)
        return [round(c / total, 4) for _, c in counts] if total else []


def _capacity(free_mb: float, memory_fraction: float, bytes_per_layer_mb: float, total_layers: int) -> int:
    return min(int(free_mb * memory_fraction / bytes_per_layer_mb), total_layers)


def _proportional_counts(total_layers: int, caps: Dict[str, int], order: List[str]) -> Dict[str, int]:
    """Split total_layers across devices in proportion to capacity, never exceeding any device's cap.

    Largest-remainder rounding keeps the sum exact. Devices with zero capacity get zero layers.
    """
    cap_sum = sum(caps[d] for d in order)
    if cap_sum < total_layers:
        raise ValueError(
            f"Devices can hold {cap_sum} of {total_layers} layers; add memory or use a smaller model"
        )
    # Ideal share is total * cap / cap_sum, which is at most the device cap because cap_sum >= total.
    ideal = {d: total_layers * caps[d] / cap_sum for d in order}
    counts = {d: int(ideal[d]) for d in order}
    remainder = total_layers - sum(counts.values())
    by_fraction = sorted(order, key=lambda d: (ideal[d] - counts[d], -order.index(d)), reverse=True)
    for d in by_fraction:
        if remainder <= 0:
            break
        if counts[d] < caps[d]:
            counts[d] += 1
            remainder -= 1
    return counts


def plan_layers(
    total_layers: int,
    head_free_mb: float,
    worker_free_mb: Dict[str, float],
    bytes_per_layer_mb: float,
    worker_order: Optional[List[str]] = None,
    memory_fraction: float = DEFAULT_MEMORY_FRACTION,
    min_worker_layers: int = DEFAULT_MIN_WORKER_LAYERS,
) -> Plan:
    """Split layers across workers and the head in proportion to usable memory.

    Each device's cap is floor(free_mb * memory_fraction / bytes_per_layer_mb). Layers are shared in
    proportion to caps (so every measured worker gets a share, not just the first one), and the head
    is included as a device. Workers that end up with fewer than min_worker_layers are dropped and their
    layers return to the head. Planning fails if the head cannot hold what remains.
    """
    if total_layers <= 0:
        raise ValueError("total_layers must be positive")
    if bytes_per_layer_mb <= 0:
        raise ValueError("bytes_per_layer_mb must be positive")

    order = list(worker_order) if worker_order is not None else sorted(worker_free_mb)
    caps = {d: _capacity(worker_free_mb[d], memory_fraction, bytes_per_layer_mb, total_layers) for d in order}
    head_cap = _capacity(head_free_mb, memory_fraction, bytes_per_layer_mb, total_layers)
    caps[HEAD] = head_cap

    counts = _proportional_counts(total_layers, caps, [HEAD] + order)

    dropped: List[str] = []
    for d in order:
        if counts[d] < min_worker_layers:
            dropped.append(d)
            counts[HEAD] += counts[d]
            counts[d] = 0
    kept = [d for d in order if counts[d] > 0]

    if counts[HEAD] > head_cap:
        raise ValueError(
            f"Head cannot hold {counts[HEAD]} layers (capacity {head_cap}); add worker memory or use a smaller model"
        )

    assignments: List[LayerAssignment] = []
    next_layer = 0
    for d in kept + ([HEAD] if counts[HEAD] > 0 else []):
        n = counts[d]
        if n <= 0:
            continue
        assignments.append(LayerAssignment(device=d, layer_start=next_layer, layer_end=next_layer + n - 1))
        next_layer += n

    return Plan(
        total_layers=total_layers,
        device_order=tuple(kept),
        assignments=tuple(assignments),
        dropped_workers=tuple(dropped),
    )
