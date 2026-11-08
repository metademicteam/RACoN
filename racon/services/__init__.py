"""RACoN services.

The Qt-based laboratory services are imported lazily. The headless API and the Docker
server must be able to import ``racon.services.engine_service`` without installing PySide6.
"""

from __future__ import annotations

from typing import Any

__all__ = ["BenchmarkService", "MonitoringService"]


def __getattr__(name: str) -> Any:
    if name == "BenchmarkService":
        from racon.services.benchmark_service import BenchmarkService

        return BenchmarkService
    if name == "MonitoringService":
        from racon.services.monitoring_service import MonitoringService

        return MonitoringService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
