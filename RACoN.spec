# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

import glob
import os

LLAMA_CPP_DIR = next(
    iter(sorted(glob.glob(r"C:\Users\*\AppData\Local\Microsoft\WinGet\Packages\ggml.llamacpp_*"))),
    None,
)
if not LLAMA_CPP_DIR or not os.path.isfile(os.path.join(LLAMA_CPP_DIR, 'rpc-server.exe')):
    raise SystemExit("llama.cpp not found. Install it first: winget install ggml.llamacpp")

llama_binaries = [
    (os.path.join(LLAMA_CPP_DIR, name), 'llama')
    for name in os.listdir(LLAMA_CPP_DIR)
    if name.endswith('.dll') or name in ('rpc-server.exe', 'llama-server.exe', 'llama-cli.exe')
]

datas = [
    ('racon/scenarios/sample_scenarios.json', 'racon/scenarios'),
    ('apps/desktop/icon.ico', '.'),
] + llama_binaries

hiddenimports = [
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'psutil',
    'platformdirs',
    'sqlite3',
    'cryptography',
    'httpx',
    'fastapi',
    'uvicorn',
    'pydantic',
    'protocols',
    'protocols.models',
    'protocols.crypto',
    'racon',
    'racon.app',
    'racon.config',
    'racon.hardware',
    'racon.core.update_manifest',
    'racon.agent',
    'racon.agent.crypto_store',
    'racon.agent.worker',
    'racon.agent.ipc',
    'racon.coordinator',
    'racon.coordinator.registry',
    'racon.coordinator.invites',
    'racon.coordinator.tasks',
    'racon.coordinator.ingress',
    'racon.coordinator.server',
    'racon.coordinator.power_guard',
    'racon.scenarios',
    'racon.scenarios.loader',
    'racon.scheduler',
    'racon.scheduler.deterministic',
    'racon.scheduler.evaluation',
    'racon.scheduler.laya_adapter',
    'racon.scheduler.models',
    'racon.services',
    'racon.services.benchmark_service',
    'racon.services.monitoring_service',
    'racon.storage',
    'racon.storage.database',
    'racon.storage.repository',
    'racon.ui',
    'racon.ui.chat_panel',
    'racon.ui.benchmark_history',
    'racon.ui.dashboard',
    'racon.ui.main_window',
    'racon.ui.scheduler_lab',
    'racon.ui.settings',
    'racon.ui.worker_panel',
    'racon.ui.styles',
    'racon.runtime',
    'racon.runtime.distributed_gguf',
    'racon.runtime.rpc_worker',
    'racon.runtime.storage_guard',
]

a = Analysis(
    ['racon/__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'torch', 'transformers', 'tokenizers'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RACoN',
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
    icon=['apps/desktop/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='RACoN',
)
