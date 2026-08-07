# -*- mode: python ; coding: utf-8 -*-
# 在 Windows 上执行: python scripts/build.py win
# 使用目录模式（onedir），避免 onefile 解压系统 DLL 时 Permission denied。

import sys
from pathlib import Path

SPECDIR = Path(SPECPATH).resolve()
sys.path.insert(0, str(SPECDIR))

from bundle_support.collect_datas import collect_package_resources

block_cipher = None
datas, binaries, hiddenimports = collect_package_resources()

# 这些应由系统提供，打进包后 onefile 解压常触发 Permission denied
_SYSTEM_DLL_PREFIXES = (
    "ucrtbase",
    "api-ms-win-",
    "vcruntime",
    "msvcp",
    "msvcr",
    "vcomp",
    "concrt",
)


def _is_system_dll(name: object) -> bool:
    lower = str(name).lower().replace("\\", "/")
    base = Path(lower).name
    return any(base.startswith(prefix) for prefix in _SYSTEM_DLL_PREFIXES)


a = Analysis(
    ['main.py'],
    pathex=[str(SPECDIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
a.binaries = [entry for entry in a.binaries if not _is_system_dll(entry[0])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Tally',
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
    icon=str(SPECDIR / 'assets' / 'app.ico'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Tally',
)
