"""Hardware discovery and telemetry package for RACoN."""

from backend.racon.hardware.discovery import (
    GpuInfo,
    HardwareSnapshot,
    detect_gpu,
    discover_hardware,
    get_cpu_name,
    get_lightweight_update,
)

__all__ = [
    "GpuInfo",
    "HardwareSnapshot",
    "detect_gpu",
    "discover_hardware",
    "get_cpu_name",
    "get_lightweight_update",
]
