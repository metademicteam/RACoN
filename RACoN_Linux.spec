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

# PySide6's Qt platform plugins (platforms/libqxcb.so and friends) are required for the desktop
# app to start. PyInstaller's stock PySide6 hook drops them when the build host cannot resolve
# their X11 dependencies (a headless container), which produces:
#     qt.qpa.plugin: Could not find the Qt platform plugin "xcb" in ""
# Collect them explicitly as binaries so their shared-library dependencies are analyzed too.
import PySide6


def qt_plugins():
    """Only the Qt platform plugins the app needs.

    Collecting every PySide6 plugin drags in QtWebEngine, Quick3D and the PDF stack (200+ MB)
    through their dependencies. RACoN uses Widgets on X11, so the platform plugins are enough;
    PNG icons are handled inside QtGui.
    """
    root = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
    if not root.is_dir():
        return []
    found = []
    for plugin in sorted((root / "platforms").glob("*.so")):
        found.append((str(plugin), str(Path("PySide6") / "Qt" / "plugins" / "platforms")))
    return found


QT_PLUGINS = qt_plugins()
if not any("libqxcb.so" in src for src, _ in QT_PLUGINS):
    raise SystemExit(
        "PySide6 libqxcb.so not found. Install PySide6 in the build environment before building."
    )


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
    ("apps/desktop/dist", "apps/desktop/dist"),
    ("backend/racon/admin_ui", "backend/racon/admin_ui"),
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
    "racon.runtime.base",
    "racon.runtime.types",
    "racon.runtime.registry",
    "racon.runtime.llamacpp",
    "racon.runtime.ollama",
    "racon.models",
    "racon.models.catalog",
    "racon.models.manager",
    "racon.models.estimate",
    "racon.models.huggingface",
    "racon.services.engine_service",
    "racon.storage.migrations",
    "racon.settings",
    "racon.logging_setup",
    "racon.desktop",
    "racon.cloud",
    "racon.cloud.supabase",
    "backend",
    "backend.racon",
    "backend.racon.api",
    "backend.racon.api.app",
    "backend.racon.api.routes_engine",
    "backend.racon.api.routes_cloud",
    "backend.racon.api.routes_worker",
    "backend.racon.api.routes_admin",
    "backend.racon.api.coordinator_state",
    "backend.racon.storage.models",
    "backend.racon.orchestration",
    "backend.racon.orchestration.service",
    "backend.racon.orchestration.planner",
    "backend.racon.orchestration.executor",
    "backend.racon.orchestration.selector",
    "backend.racon.orchestration.synthesizer",
    "backend.racon.orchestration.schemas",
    "backend.racon.orchestration.dag",
]

a = Analysis(
    ["racon/__main__.py"],
    pathex=["."],
    binaries=llama_binaries + QT_PLUGINS,
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
