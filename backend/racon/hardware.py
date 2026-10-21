"""Hardware discovery and telemetry using psutil, platform, and system interfaces."""
from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


@dataclass
class GpuInfo:
    """GPU information. Values are None/Unavailable when not reliably measurable."""
    name: Optional[str] = None
    vendor: Optional[str] = None
    vram_total_mb: Optional[float] = None
    vram_used_mb: Optional[float] = None
    utilization_pct: Optional[float] = None
    supported: bool = False
    detection_method: str = "none"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HardwareSnapshot:
    """A point-in-time snapshot of local hardware resources."""
    # CPU
    cpu_name: str = "Unknown"
    cpu_logical_count: int = 0
    cpu_physical_count: Optional[int] = None
    cpu_utilization_pct: float = 0.0
    cpu_freq_mhz: Optional[float] = None

    # Memory
    ram_total_mb: float = 0.0
    ram_available_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_utilization_pct: float = 0.0

    # Process
    process_memory_mb: float = 0.0

    # System
    os_name: str = "Unknown"
    os_version: str = "Unknown"
    os_architecture: str = "Unknown"
    python_version: str = "Unknown"

    # GPU
    gpu: GpuInfo = field(default_factory=GpuInfo)

    # Metadata
    discovery_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        return data


def get_cpu_name() -> str:
    """Attempt to get the CPU brand string."""
    try:
        if platform.system() == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            return name.strip()
    except Exception:
        pass
    return platform.processor() or "Unknown"


def detect_gpu() -> GpuInfo:
    """Detect GPU info using available system interfaces."""
    gpu = GpuInfo()

    # Try NVIDIA nvidia-smi first
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000  # CREATE_NO_WINDOW
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 4:
                gpu.name = parts[0].strip()
                gpu.vendor = "NVIDIA"
                gpu.vram_total_mb = float(parts[1].strip())
                gpu.vram_used_mb = float(parts[2].strip())
                gpu.utilization_pct = float(parts[3].strip())
                gpu.supported = True
                gpu.detection_method = "nvidia-smi"
                return gpu
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        logger.debug("nvidia-smi not available: %s", e)

    # Try Windows WMI for GPU name at minimum
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object -First 1 -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5, creationflags=0x08000000
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu.name = result.stdout.strip()
                gpu.detection_method = "wmi"
                # For integrated GPUs like Intel UHD, VRAM/utilization cannot be reliably read
                # Do NOT invent values
                if "intel" in gpu.name.lower():
                    gpu.vendor = "Intel"
                    gpu.supported = False  # No reliable telemetry for Intel iGPU via standard tools
                elif "amd" in gpu.name.lower() or "radeon" in gpu.name.lower():
                    gpu.vendor = "AMD"
                    gpu.supported = False
                return gpu
        except Exception as e:
            logger.debug("WMI GPU detection failed: %s", e)

    return gpu


def discover_hardware() -> HardwareSnapshot:
    """Perform a full hardware discovery. Never raises; captures errors in the snapshot."""
    snap = HardwareSnapshot()

    # CPU
    try:
        snap.cpu_name = get_cpu_name()
    except Exception as e:
        snap.discovery_errors.append(f"CPU name: {e}")

    try:
        snap.cpu_logical_count = psutil.cpu_count(logical=True) or 0
        snap.cpu_physical_count = psutil.cpu_count(logical=False)
    except Exception as e:
        snap.discovery_errors.append(f"CPU count: {e}")

    try:
        snap.cpu_utilization_pct = psutil.cpu_percent(interval=0.1)
    except Exception as e:
        snap.discovery_errors.append(f"CPU util: {e}")

    try:
        freq = psutil.cpu_freq()
        if freq:
            snap.cpu_freq_mhz = freq.current
    except Exception as e:
        snap.discovery_errors.append(f"CPU freq: {e}")

    # Memory
    try:
        mem = psutil.virtual_memory()
        snap.ram_total_mb = mem.total / (1024 * 1024)
        snap.ram_available_mb = mem.available / (1024 * 1024)
        snap.ram_used_mb = mem.used / (1024 * 1024)
        snap.ram_utilization_pct = mem.percent
    except Exception as e:
        snap.discovery_errors.append(f"Memory: {e}")

    # Process memory
    try:
        proc = psutil.Process(os.getpid())
        snap.process_memory_mb = proc.memory_info().rss / (1024 * 1024)
    except Exception as e:
        snap.discovery_errors.append(f"Process memory: {e}")

    # System
    try:
        snap.os_name = platform.system()
        snap.os_version = platform.version()
        snap.os_architecture = platform.machine()
        snap.python_version = platform.python_version()
    except Exception as e:
        snap.discovery_errors.append(f"System info: {e}")

    # GPU
    try:
        snap.gpu = detect_gpu()
    except Exception as e:
        snap.discovery_errors.append(f"GPU: {e}")

    if snap.discovery_errors:
        logger.warning("Hardware discovery issues: %s", snap.discovery_errors)

    return snap


def get_lightweight_update() -> dict:
    """Get only the metrics that change frequently (CPU%, RAM%), for periodic refresh."""
    result = {}
    try:
        result["cpu_utilization_pct"] = psutil.cpu_percent(interval=None)
    except Exception:
        result["cpu_utilization_pct"] = None
    try:
        mem = psutil.virtual_memory()
        result["ram_available_mb"] = mem.available / (1024 * 1024)
        result["ram_used_mb"] = mem.used / (1024 * 1024)
        result["ram_utilization_pct"] = mem.percent
    except Exception:
        result["ram_available_mb"] = None
        result["ram_used_mb"] = None
        result["ram_utilization_pct"] = None
    try:
        proc = psutil.Process(os.getpid())
        result["process_memory_mb"] = proc.memory_info().rss / (1024 * 1024)
    except Exception:
        result["process_memory_mb"] = None
    return result
