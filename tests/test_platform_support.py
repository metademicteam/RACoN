"""Cross-platform behavior for Linux/Ubuntu support alongside Windows."""
import os

from racon.cluster.probe import parse_list_devices
from racon.hardware import _parse_cpuinfo_name, _parse_lspci_gpu
from racon.runtime.distributed_gguf import (
    bundled_llama_dir,
    find_llama_tool,
    llama_process_env,
    tool_binary_name,
)


def test_tool_binary_name_windows_and_linux():
    assert tool_binary_name("rpc-server", windows=True) == "rpc-server.exe"
    assert tool_binary_name("rpc-server", windows=False) == "rpc-server"
    assert tool_binary_name("rpc-server.exe", windows=False) == "rpc-server.exe"


def test_bundled_llama_dir_honors_env_on_every_platform(tmp_path, monkeypatch):
    (tmp_path / "rpc-server").write_text("", encoding="utf-8")
    (tmp_path / "llama-server").write_text("", encoding="utf-8")
    monkeypatch.setenv("RACON_LLAMA_DIR", str(tmp_path))

    assert bundled_llama_dir() == tmp_path
    assert find_llama_tool("rpc-server") == str(tmp_path / "rpc-server")


def test_llama_process_env_exposes_bundled_libraries_on_posix(tmp_path, monkeypatch):
    (tmp_path / "rpc-server").write_text("", encoding="utf-8")
    monkeypatch.setenv("RACON_LLAMA_DIR", str(tmp_path))
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing")

    env = llama_process_env()
    if os.name == "nt":
        assert env["LD_LIBRARY_PATH"] == "/existing"
    else:
        assert env["LD_LIBRARY_PATH"] == str(tmp_path) + os.pathsep + "/existing"


def test_parse_list_devices_accepts_linux_cpu_device():
    result = parse_list_devices("  CPU: CPU (32000 MiB, 30000 MiB free)\n")
    assert result.local_free_mb == 30000.0


def test_parse_cpuinfo_name():
    text = "processor : 0\nmodel name : AMD Ryzen 7 7840U\ncpu MHz : 3300\n"
    assert _parse_cpuinfo_name(text) == "AMD Ryzen 7 7840U"


def test_parse_lspci_gpu():
    text = "00:02.0 VGA compatible controller: Intel Corporation Raptor Lake-P Graphics (rev 04)\n"
    assert _parse_lspci_gpu(text) == "Intel Corporation Raptor Lake-P Graphics (rev 04)"


def test_resolve_local_gguf_honors_env(tmp_path, monkeypatch):
    from backend.racon.api.routes_openai import resolve_local_gguf

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setenv("RACON_GGUF_MODEL", str(model))

    assert resolve_local_gguf() == str(model)
