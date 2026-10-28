"""Unit tests for SSD Storage Write-Wear Guard."""
import json
import time
from pathlib import Path
import pytest

from racon.runtime.storage_guard import (
    StorageGuard,
    StorageQuotaExceededError,
    StorageWriteMetrics,
)


def test_storage_guard_initial_state(tmp_path: Path):
    metrics_file = tmp_path / "metrics.json"
    guard = StorageGuard(metrics_file=metrics_file, daily_limit_gb=10.0)

    metrics = guard.load_metrics()
    assert metrics.bytes_written_today == 0
    assert metrics.gb_limit == 10.0
    assert metrics.remaining_bytes == int(10.0 * (1024 ** 3))
    assert guard.check_write_allowed(1024 * 1024) is True


def test_storage_guard_records_writes(tmp_path: Path):
    metrics_file = tmp_path / "metrics.json"
    guard = StorageGuard(metrics_file=metrics_file, daily_limit_gb=1.0)

    # Write 500 MB
    bytes_500mb = 500 * 1024 * 1024
    metrics = guard.record_write(bytes_500mb)
    assert metrics.bytes_written_today == bytes_500mb
    assert round(metrics.gb_written_today, 3) == round(500 / 1024, 3)

    # Check remaining budget
    assert guard.check_write_allowed(bytes_500mb) is True
    # Attempting to write 600 MB more would exceed 1.0 GB total
    assert guard.check_write_allowed(bytes_500mb + 100 * 1024 * 1024) is False


def test_storage_guard_resets_on_new_date(tmp_path: Path):
    metrics_file = tmp_path / "metrics.json"
    guard = StorageGuard(metrics_file=metrics_file, daily_limit_gb=1.0)

    # Write an entry with yesterday's date
    stale_data = {
        "current_date": "2020-01-01",
        "bytes_written_today": 999999999,
        "daily_limit_bytes": 1073741824,
        "last_write_timestamp": 0.0,
    }
    metrics_file.write_text(json.dumps(stale_data), encoding="utf-8")

    # Guard should auto-reset for today
    metrics = guard.load_metrics()
    assert metrics.bytes_written_today == 0
    assert metrics.current_date != "2020-01-01"
