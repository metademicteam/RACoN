# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

datas = [
    ('apps/desktop/dist', 'apps/desktop/dist'),
    ('apps/desktop/icon.ico', 'apps/desktop/icon.ico'),
]

hiddenimports = [
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespans',
    'uvicorn.lifespans.on',
    'fastapi',
    'starlette',
    'starlette.staticfiles',
    'starlette.responses',
    'pydantic',
    'sqlalchemy',
    'sqlalchemy.sql.default_comparator',
    'backend.racon.api.app',
    'backend.racon.api.routes_status',
    'backend.racon.api.routes_nodes',
    'backend.racon.api.routes_models',
    'backend.racon.api.routes_scheduler',
    'backend.racon.api.routes_jobs',
    'backend.racon.api.routes_benchmarks',
    'backend.racon.api.routes_logs',
    'backend.racon.api.routes_settings',
    'backend.racon.api.routes_openai',
    'racon.runtime.distributed_gguf',
    'racon.runtime.rpc_worker',
    'webview',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    'clr_loader',
    'pythonnet',
]

a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RACoN_Desktop',
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
    name='RACoN_Desktop',
)
