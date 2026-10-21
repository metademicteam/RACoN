"""Hardware discovery module for RACoN.

Provides accurate, non-fabricated hardware telemetry using psutil,
platform APIs, Windows Registry, and safe GPU inspection interfaces.
Intel UHD 630 and other integrated GPUs report unsupported/None metrics
rather than fabricated values.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import psutil

logger = logging.getLogger(__name__)


@dataclass
class GpuInfo:
    """GPU capability and telemetry model.

    Values are strictly None when not reliably measurable by system APIs.
    No simulated or fabricated numbers are provided for unsupported GPUs.
    """
    name: Optional[str] = None
    vendor: Optional[str] = None
    vram_total_mb: Optional[float] = None
    vram_used_mb: Optional[float] = None
    utilization_pct: Optional[float] = None
    supported: bool = False
    detection_method: str = "none"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return asdict(self)


@dataclass
class HardwareSnapshot:
    """A comprehensive point-in-time snapshot of local hardware resources."""
    # CPU
    cpu_name: str = "Unknown"
    cpu_logical_count: int = 0
    cpu_physical_count: Optional[int] = None
    cpu_utilization_pct: float = 0.0
    cpu_freq_mhz: Optional[float] = None

    # Memory (in Megabytes)
    ram_total_mb: float = 0.0
    ram_available_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_utilization_pct: float = 0.0

    # Process RSS Memory (in Megabytes)
    process_memory_mb: float = 0.0

    # Operating System & Architecture
    os_name: str = "Unknown"
    os_version: str = "Unknown"
    os_architecture: str = "Unknown"
    python_version: str = "Unknown"

    # GPU
    gpu: GpuInfo = field(default_factory=GpuInfo)

    # Metadata & Error Tracking
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    discovery_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation for API serialization."""
        data = asdict(self)
        return data


def get_cpu_name() -> str:
    """Retrieve the authentic CPU brand string.

    Uses Windows Registry on Windows systems for the exact processor model,
    falling back to platform.processor() or 'Unknown'.
    """
    try:
        if platform.system() == "Windows":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            raw_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            if raw_name:
                # Normalize excessive internal spacing often present in registry strings
                return " ".join(str(raw_name).strip().split())
    except Exception as e:
        logger.debug("Windows registry CPU lookup failed: %s", e)

    # Fallback to platform processor
    fallback = platform.processor()
    return fallback.strip() if fallback else "Unknown"


def detect_gpu() -> GpuInfo:
    """Detect local GPU hardware using safe system interfaces.

    Priority:
    1. NVIDIA SMI CLI (reliable dedicated metrics for NVIDIA hardware).
    2. Windows WMI / CIM Video Controller query.
       - Integrated GPUs (such as Intel UHD 630 or AMD APUs) do not expose
         dedicated VRAM or reliable hardware utilization metrics through
         non-intrusive APIs; they are explicitly marked as supported=False
         with None metrics to prevent falsification of telemetry.
    """
    gpu = GpuInfo()

    # 1. Attempt NVIDIA nvidia-smi
    try:
        creation_flags = 0x08000000 if platform.system() == "Windows" else 0
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=creation_flags,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            if len(parts) >= 4:
                gpu.name = parts[0]
                gpu.vendor = "NVIDIA"
                gpu.vram_total_mb = float(parts[1])
                gpu.vram_used_mb = float(parts[2])
                gpu.utilization_pct = float(parts[3])
                gpu.supported = True
                gpu.detection_method = "nvidia-smi"
                return gpu
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        logger.debug("nvidia-smi inspection not available or failed: %s", e)

    # 2. Attempt Windows WMI / CIM query for display controller identity
    if platform.system() == "Windows":
        try:
            creation_flags = 0x08000000
            cmd = (
                "Get-CimInstance Win32_VideoController "
                "| Select-Object -First 1 -ExpandProperty Name"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creation_flags,
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_name = result.stdout.strip()
                gpu.name = gpu_name
                gpu.detection_method = "wmi"

                name_lower = gpu_name.lower()
                if "intel" in name_lower:
                    gpu.vendor = "Intel"
                    # Intel UHD 630 / Iris Xe / HD Graphics share system RAM.
                    # Standard WMI does not report accurate live VRAM utilization.
                    # Non-fabrication policy: set supported=False and metrics=None.
                    gpu.supported = False
                    gpu.vram_total_mb = None
                    gpu.vram_used_mb = None
                    gpu.utilization_pct = None
                elif "amd" in name_lower or "radeon" in name_lower:
                    gpu.vendor = "AMD"
                    gpu.supported = False
                    gpu.vram_total_mb = None
                    gpu.vram_used_mb = None
                    gpu.utilization_pct = None
                elif "nvidia" in name_lower:
                    gpu.vendor = "NVIDIA"
                    gpu.supported = False  # nvidia-smi failed if we got here
                else:
                    gpu.vendor = "Unknown"
                    gpu.supported = False
                return gpu
        except Exception as e:
            logger.debug("WMI GPU query failed: %s", e)

    return gpu


def discover_hardware() -> HardwareSnapshot:
    """Perform a comprehensive hardware discovery.

    Guaranteed never to raise uncaught exceptions; any individual component
    probe errors are captured in `snapshot.discovery_errors`.
    """
    snapshot = HardwareSnapshot()

    # CPU Name
    try:
        snapshot.cpu_name = get_cpu_name()
    except Exception as e:
        snapshot.discovery_errors.append(f"CPU name probe failed: {e}")

    # CPU Logical and Physical Cores
    try:
        snapshot.cpu_logical_count = psutil.cpu_count(logical=True) or 0
        snapshot.cpu_physical_count = psutil.cpu_count(logical=False)
    except Exception as e:
        snapshot.discovery_errors.append(f"CPU count probe failed: {e}")

    # CPU Utilization (short sample interval)
    try:
        snapshot.cpu_utilization_pct = psutil.cpu_percent(interval=0.1)
    except Exception as e:
        snapshot.discovery_errors.append(f"CPU utilization probe failed: {e}")

    # CPU Frequency
    try:
        freq = psutil.cpu_freq()
        if freq is not None:
            snapshot.cpu_freq_mhz = freq.current
    except Exception as e:
        snapshot.discovery_errors.append(f"CPU frequency probe failed: {e}")

    # System Memory (RAM in MB)
    try:
        mem = psutil.virtual_memory()
        snapshot.ram_total_mb = round(mem.total / (1024 * 1024), 2)
        snapshot.ram_available_mb = round(mem.available / (1024 * 1024), 2)
        snapshot.ram_used_mb = round(mem.used / (1024 * 1024), 2)
        snapshot.ram_utilization_pct = float(mem.percent)
    except Exception as e:
        snapshot.discovery_errors.append(f"RAM probe failed: {e}")

    # Process RSS Memory (in MB)
    try:
        proc = psutil.Process(os.getpid())
        snapshot.process_memory_mb = round(
            proc.memory_info().rss / (1024 * 1024), 2
        )
    except Exception as e:
        snapshot.discovery_errors.append(f"Process memory probe failed: {e}")

    # Operating System & Python Runtime
    try:
        snapshot.os_name = platform.system()
        snapshot.os_version = platform.version()
        snapshot.os_architecture = platform.machine()
        snapshot.python_version = platform.python_version()
    except Exception as e:
        snapshot.discovery_errors.append(f"Platform info probe failed: {e}")

    # GPU
    try:
        snapshot.gpu = detect_gpu()
    except Exception as e:
        snapshot.discovery_errors.append(f"GPU probe failed: {e}")

    if snapshot.discovery_errors:
        logger.warning(
            "Hardware discovery completed with non-fatal issues: %s",
            snapshot.discovery_errors,
        )

    return snapshot


def get_lightweight_update() -> dict[str, Any]:
    """Retrieve fast-changing telemetry metrics (CPU%, RAM, Process RSS).

    Designed for high-frequency polling loops (e.g. 1-2s GUI refresh)
    without incurring the cost of registry or WMI lookups.
    """
    result: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_utilization_pct": None,
        "ram_available_mb": None,
        "ram_used_mb": None,
        "ram_utilization_pct": None,
        "process_memory_mb": None,
    }

    try:
        result["cpu_utilization_pct"] = psutil.cpu_percent(interval=None)
    except Exception as e:
        logger.debug("Lightweight CPU util update error: %s", e)

    try:
        mem = psutil.virtual_memory()
        result["ram_available_mb"] = round(mem.available / (1024 * 1024), 2)
        result["ram_used_mb"] = round(mem.used / (1024 * 1024), 2)
        result["ram_utilization_pct"] = float(mem.percent)
    except Exception as e:
        logger.debug("Lightweight RAM update error: %s", e)

    try:
        proc = psutil.Process(os.getpid())
        result["process_memory_mb"] = round(
            proc.memory_info().rss / (1024 * 1024), 2
        )
    except Exception as e:
        logger.debug("Lightweight Process RSS update error: %s", e)

    return result
