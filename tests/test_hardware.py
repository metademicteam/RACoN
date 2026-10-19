"""Tests for the hardware discovery module of RACoN Lab."""
import subprocess
import psutil
import pytest
from racon.hardware import (
    discover_hardware,
    get_lightweight_update,
    HardwareSnapshot,
    GpuInfo,
    get_cpu_name,
)


def test_discover_hardware_returns_snapshot():
    """discover_hardware() returns a HardwareSnapshot with valid fields."""
    snapshot = discover_hardware()
    assert isinstance(snapshot, HardwareSnapshot)
    assert isinstance(snapshot.cpu_name, str)
    assert isinstance(snapshot.cpu_logical_count, int)
    assert isinstance(snapshot.ram_total_mb, float)
    assert isinstance(snapshot.ram_available_mb, float)
    assert isinstance(snapshot.ram_used_mb, float)
    assert isinstance(snapshot.ram_utilization_pct, float)
    assert isinstance(snapshot.process_memory_mb, float)
    assert isinstance(snapshot.os_name, str)
    assert isinstance(snapshot.os_version, str)
    assert isinstance(snapshot.os_architecture, str)
    assert isinstance(snapshot.python_version, str)
    assert isinstance(snapshot.gpu, GpuInfo)
    assert isinstance(snapshot.discovery_errors, list)


def test_cpu_name_not_empty():
    """cpu_name is a non-empty string."""
    snapshot = discover_hardware()
    assert isinstance(snapshot.cpu_name, str)
    assert len(snapshot.cpu_name.strip()) > 0


def test_cpu_count_positive():
    """cpu_logical_count > 0."""
    snapshot = discover_hardware()
    assert snapshot.cpu_logical_count > 0


def test_ram_total_positive():
    """ram_total_mb > 0."""
    snapshot = discover_hardware()
    assert snapshot.ram_total_mb > 0


def test_ram_available_positive():
    """ram_available_mb > 0 and <= ram_total_mb."""
    snapshot = discover_hardware()
    assert snapshot.ram_available_mb > 0
    assert snapshot.ram_available_mb <= snapshot.ram_total_mb


def test_process_memory_positive():
    """process_memory_mb > 0."""
    snapshot = discover_hardware()
    assert snapshot.process_memory_mb > 0


def test_os_info_populated():
    """os_name, os_version, os_architecture are non-empty."""
    snapshot = discover_hardware()
    assert isinstance(snapshot.os_name, str) and len(snapshot.os_name.strip()) > 0
    assert isinstance(snapshot.os_version, str) and len(snapshot.os_version.strip()) > 0
    assert isinstance(snapshot.os_architecture, str) and len(snapshot.os_architecture.strip()) > 0


def test_python_version_populated():
    """python_version is non-empty."""
    snapshot = discover_hardware()
    assert isinstance(snapshot.python_version, str)
    assert len(snapshot.python_version.strip()) > 0


def test_gpu_info_is_gpu_info():
    """gpu field is a GpuInfo instance."""
    snapshot = discover_hardware()
    assert isinstance(snapshot.gpu, GpuInfo)


def test_discovery_errors_is_list():
    """discovery_errors is a list (may be empty)."""
    snapshot = discover_hardware()
    assert isinstance(snapshot.discovery_errors, list)


def test_lightweight_update_returns_dict():
    """get_lightweight_update() returns a dict."""
    update = get_lightweight_update()
    assert isinstance(update, dict)


def test_lightweight_update_has_expected_keys():
    """Result has cpu_utilization_pct, ram_available_mb, ram_used_mb, ram_utilization_pct, process_memory_mb."""
    update = get_lightweight_update()
    expected_keys = {
        "cpu_utilization_pct",
        "ram_available_mb",
        "ram_used_mb",
        "ram_utilization_pct",
        "process_memory_mb",
    }
    assert expected_keys.issubset(update.keys())


def test_lightweight_cpu_utilization_range():
    """CPU utilization is between 0 and 100 (inclusive)."""
    update = get_lightweight_update()
    cpu_util = update.get("cpu_utilization_pct")
    assert cpu_util is not None
    assert isinstance(cpu_util, (int, float))
    assert 0.0 <= cpu_util <= 100.0


def test_missing_gpu_metrics_not_fabricated(monkeypatch):
    """For Intel UHD 630 (or any iGPU without nvidia-smi), vram_total_mb, vram_used_mb, utilization_pct should be None."""
    def fake_subprocess_run(cmd, *args, **kwargs):
        if "nvidia-smi" in cmd[0]:
            raise FileNotFoundError("nvidia-smi not found")
        if len(cmd) > 2 and "Win32_VideoController" in cmd[2]:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="Intel(R) UHD Graphics 630\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("racon.hardware.platform.system", lambda: "Windows")
    monkeypatch.setattr("racon.hardware.subprocess.run", fake_subprocess_run)

    snapshot = discover_hardware()
    assert snapshot.gpu.name == "Intel(R) UHD Graphics 630"
    assert snapshot.gpu.vendor == "Intel"
    assert snapshot.gpu.vram_total_mb is None
    assert snapshot.gpu.vram_used_mb is None
    assert snapshot.gpu.utilization_pct is None
    assert snapshot.gpu.supported is False


def test_discover_hardware_never_raises(monkeypatch):
    """Even if something goes wrong internally, discover_hardware should not raise."""
    def raise_error(*args, **kwargs):
        raise RuntimeError("Simulated CPU count failure")

    monkeypatch.setattr(psutil, "cpu_count", raise_error)

    snapshot = discover_hardware()
    assert isinstance(snapshot, HardwareSnapshot)
    assert len(snapshot.discovery_errors) > 0
    assert any("CPU count" in err for err in snapshot.discovery_errors)


def test_get_cpu_name_returns_string():
    """get_cpu_name() returns a string."""
    cpu_name = get_cpu_name()
    assert isinstance(cpu_name, str)
    assert len(cpu_name.strip()) > 0
