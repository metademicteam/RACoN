# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the RACoN desktop + worker application on Linux.

Build on Ubuntu with llama.cpp binaries available:
    RACON_LLAMA_DIR=/opt/racon/llama pyinstaller RACoN_Linux.spec --noconfirm --clean
"""
import glob
import os
import shutil
from pathlib import Path

block_cipher = None


def find_llama_dir() -> Path:
    candidates = []
    env_dir = os.environ.get("RACON_LLAMA_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend([
        Path("/opt/racon/llama"),
        Path("/opt/llama.cpp/build/bin"),
        Path.home() / "llama.cpp" / "build" / "bin",
        Path.home() / ".local" / "lib" / "racon" / "llama",
    ])
    for candidate in candidates:
        if candidate.is_dir() and (
            (candidate / "rpc-server").is_file() or (candidate / "llama-server").is_file()
        ):
            return candidate
    raise SystemExit(
        "llama.cpp not found. Install it or set RACON_LLAMA_DIR to a folder that contains "
        "rpc-server and llama-server."
    )


LLAMA_DIR = find_llama_dir()


def find_libgomp() -> Path | None:
    """Locate libgomp.so.1 so rpc-server runs on a stock Ubuntu without libgomp1."""
    patterns = (
        "/usr/lib/x86_64-linux-gnu/libgomp.so.1",
        "/usr/lib/aarch64-linux-gnu/libgomp.so.1",
        "/usr/lib64/libgomp.so.1",
        "/usr/lib/libgomp.so.1",
        "/usr/lib/gcc/*/*/libgomp.so.1",
    )
    for pattern in patterns:
        for match in glob.glob(pattern):
            candidate = Path(match)
            if candidate.is_file():
                return candidate
    return None


llama_binaries = []
for entry in sorted(LLAMA_DIR.iterdir()):
    if not entry.is_file():
        continue
    name = entry.name
    if ".so" in name or os.access(entry, os.X_OK):
        llama_binaries.append((str(entry), "llama"))

# llama.cpp links against OpenMP at runtime. Bundle it next to the other llama
# libraries; RACoN prepends that folder to LD_LIBRARY_PATH for every child.
libgomp = find_libgomp()
if libgomp:
    staging = Path(os.environ.get("TMPDIR", "/tmp")) / "racon-libgomp"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / "libgomp.so.1"
    shutil.copyfile(libgomp.resolve(), staged)
    llama_binaries.append((str(staged), "llama"))
else:
    print("WARNING: libgomp.so.1 not found. Install libgomp1 so the Linux bundle is self-contained.")

datas = [
    ("racon/scenarios/sample_scenarios.json", "racon/scenarios"),
    ("apps/desktop/icon.png", "apps/desktop"),
]

hiddenimports = [
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "psutil",
    "platformdirs",
    "sqlite3",
    "cryptography",
    "httpx",
    "fastapi",
    "uvicorn",
    "pydantic",
    "protocols",
    "protocols.models",
    "protocols.crypto",
    "racon",
    "racon.app",
    "racon.config",
    "racon.hardware",
    "racon.core.update_manifest",
    "racon.agent",
    "racon.agent.crypto_store",
    "racon.agent.worker",
    "racon.agent.ipc",
    "racon.coordinator",
    "racon.coordinator.registry",
    "racon.coordinator.invites",
    "racon.coordinator.tasks",
    "racon.coordinator.ingress",
    "racon.coordinator.server",
    "racon.coordinator.power_guard",
    "racon.scenarios",
    "racon.scenarios.loader",
    "racon.scheduler",
    "racon.scheduler.deterministic",
    "racon.scheduler.evaluation",
    "racon.scheduler.laya_adapter",
    "racon.scheduler.models",
    "racon.services",
    "racon.services.benchmark_service",
    "racon.services.monitoring_service",
    "racon.storage",
    "racon.storage.database",
    "racon.storage.repository",
    "racon.ui",
    "racon.ui.chat_panel",
    "racon.ui.benchmark_history",
    "racon.ui.dashboard",
    "racon.ui.main_window",
    "racon.ui.scheduler_lab",
    "racon.ui.settings",
    "racon.ui.worker_panel",
    "racon.ui.styles",
    "racon.runtime",
    "racon.runtime.distributed_gguf",
    "racon.runtime.rpc_worker",
    "racon.runtime.storage_guard",
]

a = Analysis(
    ["racon/__main__.py"],
    pathex=["."],
    binaries=llama_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "torch", "transformers", "tokenizers"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="racon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="racon",
)
