"""In-process delta fan-out with replay for task and job streams.

The coordinator is one asyncio process in the Docker deployment. Publishers (FastAPI
routes and the orchestration runner) and subscribers (the network gateway) live in the same
event loop, so no cross-thread machinery is required. Buffers let a late subscriber replay
the prefix it missed.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any, Deque, Optional

logger = logging.getLogger(__name__)

DEFAULT_BUFFER_SIZE = 2048


class DeltaBroker:
    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        self._buffer_size = buffer_size
        self._lock = threading.Lock()
        self._buffers: dict[str, Deque[dict[str, Any]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        with self._lock:
            buffer = self._buffers.setdefault(channel, deque(maxlen=self._buffer_size))
            buffer.append(payload)
            subscribers = list(self._subscribers.get(channel, ()))
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover - queues are unbounded
                logger.debug("Dropping delta for a slow subscriber of %s", channel)

    async def subscribe(self, channel: str) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(channel, set()).add(queue)
            replay = list(self._buffers.get(channel, ()))
        return queue, replay

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subscribers = self._subscribers.get(channel)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(channel, None)

    def history(self, channel: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffers.get(channel, ()))

    def clear(self, channel: Optional[str] = None) -> None:
        with self._lock:
            if channel is None:
                self._buffers.clear()
                self._subscribers.clear()
            else:
                self._buffers.pop(channel, None)
                self._subscribers.pop(channel, None)


_BROKER: Optional[DeltaBroker] = None
_BROKER_LOCK = threading.Lock()


def get_delta_broker() -> DeltaBroker:
    global _BROKER
    if _BROKER is None:
        with _BROKER_LOCK:
            if _BROKER is None:
                _BROKER = DeltaBroker()
    return _BROKER


def task_channel(task_id: str) -> str:
    return f"task:{task_id}"


def job_channel(job_id: str) -> str:
    return f"job:{job_id}"
