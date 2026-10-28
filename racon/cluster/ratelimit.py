"""In-memory sliding-window rate limiter for the public gateway.

Single process only. Counters reset on restart. That is acceptable for one gateway process on one head PC.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple


class SlidingWindowLimiter:
    def __init__(
        self,
        max_events: int,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        self._max = max_events
        self._window = window_s
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> Tuple[bool, float]:
        """Record one event for `key`. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._max:
                return False, max(0.0, events[0] + self._window - now)
            events.append(now)
            if len(self._events) > 10_000:
                self._prune(cutoff)
            return True, 0.0

    def _prune(self, cutoff: float) -> None:
        for k in [k for k, v in self._events.items() if not v or v[-1] <= cutoff]:
            del self._events[k]
