"""Storage & SSD Write-Wear Guard.

Monitors cumulative daily disk writes for GGUF model files and shards in %LOCALAPPDATA%
to protect consumer NVMe/SSD drive endurance from runaway re-download cycles (ADR-012).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from racon.config import get_data_dir

logger = logging.getLogger(__name__)

DEFAULT_DAILY_WRITE_LIMIT_GB = 30.0  # 30 GB daily download/write ceiling per machine


class StorageQuotaExceededError(RuntimeError):
    """Raised when an operation would exceed the daily SSD write-wear threshold."""
    pass


@dataclass
class StorageWriteMetrics:
    current_date: str
    bytes_written_today: int
    daily_limit_bytes: int
    last_write_timestamp: float

    @property
    def gb_written_today(self) -> float:
        return self.bytes_written_today / (1024 ** 3)

    @property
    def gb_limit(self) -> float:
        return self.daily_limit_bytes / (1024 ** 3)

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.daily_limit_bytes - self.bytes_written_today)


class StorageGuard:
    """Manages disk write metrics and enforces daily SSD wear ceilings."""

    def __init__(
        self,
        metrics_file: Optional[Path] = None,
        daily_limit_gb: float = DEFAULT_DAILY_WRITE_LIMIT_GB,
    ) -> None:
        self.metrics_file = metrics_file or (get_data_dir() / "storage_metrics.json")
        env_limit = os.environ.get("RACON_DAILY_WRITE_LIMIT_GB")
        if env_limit:
            try:
                daily_limit_gb = float(env_limit)
            except ValueError:
                pass
        self.daily_limit_bytes = int(daily_limit_gb * (1024 ** 3))

    def _today_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def load_metrics(self) -> StorageWriteMetrics:
        today = self._today_str()
        if self.metrics_file.exists():
            try:
                data = json.loads(self.metrics_file.read_text(encoding="utf-8"))
                if data.get("current_date") == today:
                    return StorageWriteMetrics(
                        current_date=today,
                        bytes_written_today=int(data.get("bytes_written_today", 0)),
                        daily_limit_bytes=self.daily_limit_bytes,
                        last_write_timestamp=float(data.get("last_write_timestamp", time.time())),
                    )
            except Exception as e:
                logger.warning("Could not read storage metrics (%s); resetting counter.", e)

        # Fresh day
        return StorageWriteMetrics(
            current_date=today,
            bytes_written_today=0,
            daily_limit_bytes=self.daily_limit_bytes,
            last_write_timestamp=time.time(),
        )

    def _save_metrics(self, metrics: StorageWriteMetrics) -> None:
        try:
            self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
            self.metrics_file.write_text(
                json.dumps(
                    {
                        "current_date": metrics.current_date,
                        "bytes_written_today": metrics.bytes_written_today,
                        "daily_limit_bytes": metrics.daily_limit_bytes,
                        "last_write_timestamp": metrics.last_write_timestamp,
                        "gb_written_today": round(metrics.gb_written_today, 3),
                        "gb_limit": round(metrics.gb_limit, 1),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save storage metrics: %s", e)

    def check_write_allowed(self, additional_bytes: int) -> bool:
        """Check whether additional write bytes fit within today's SSD budget."""
        metrics = self.load_metrics()
        return (metrics.bytes_written_today + additional_bytes) <= metrics.daily_limit_bytes

    def record_write(self, bytes_written: int) -> StorageWriteMetrics:
        """Record newly committed disk writes and persist updated metrics."""
        metrics = self.load_metrics()
        metrics.bytes_written_today += max(0, bytes_written)
        metrics.last_write_timestamp = time.time()
        self._save_metrics(metrics)
        return metrics


_GLOBAL_GUARD: Optional[StorageGuard] = None


def get_storage_guard() -> StorageGuard:
    global _GLOBAL_GUARD
    if _GLOBAL_GUARD is None:
        _GLOBAL_GUARD = StorageGuard()
    return _GLOBAL_GUARD
