# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

datas = [
    ('racon/scenarios/sample_scenarios.json', 'racon/scenarios'),
    ('apps/desktop/icon.ico', '.'),
]

hiddenimports = [
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'psutil',
    'platformdirs',
    'sqlite3',
    'racon',
    'racon.app',
    'racon.config',
    'racon.hardware',
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
    'racon.ui.benchmark_history',
    'racon.ui.dashboard',
    'racon.ui.main_window',
    'racon.ui.scheduler_lab',
    'racon.ui.settings',
    'racon.ui.styles',
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
    excludes=['tkinter', 'matplotlib', 'scipy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RACoN_Lab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='RACoN_Lab',
)
