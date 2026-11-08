"""Small in-process sliding-window limiter for coordinator requests.

The coordinator is a single process in the Docker deployment. If it is scaled out later,
move this state into Postgres/Redis; the interface is intentionally narrow.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, Deque[float]] = {}

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int, int]:
        """Record one hit and return ``(allowed, remaining, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= max(1, int(limit)):
                retry_after = max(1, int(window_seconds - (now - hits[0])) + 1)
                return False, 0, retry_after
            hits.append(now)
            remaining = max(0, int(limit) - len(hits))
            return True, remaining, 0

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
