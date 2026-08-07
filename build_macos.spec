# -*- mode: python ; coding: utf-8 -*-
# 在 macOS 上执行: python scripts/build.py mac

import sys
from pathlib import Path

SPECDIR = Path(SPECPATH).resolve()
sys.path.insert(0, str(SPECDIR))

from bundle_support.collect_datas import collect_package_resources

block_cipher = None
datas, binaries, hiddenimports = collect_package_resources()

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
app = BUNDLE(
    coll,
    name='Tally.app',
    icon=str(SPECDIR / 'assets' / 'app.icns'),
    bundle_identifier='com.tally.property',
    info_plist={
        'CFBundleName': 'Tally',
        'CFBundleDisplayName': '本地记账',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'LSArchitecturePriority': ['x86_64'],
    },
)
