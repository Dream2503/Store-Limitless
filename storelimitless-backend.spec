# -*- mode: python ; coding: utf-8 -*-

import os

FFMPEG = os.environ["STORELIMITLESS_FFMPEG"]
REDIS = os.environ.get("STORELIMITLESS_REDIS")

binaries = [(FFMPEG, ".")]

if REDIS:
    binaries.append((REDIS, "."))

a = Analysis(
    ["core/main.py"],
    pathex=[],
    binaries=binaries,
    datas=[(".env", ".")],
    hiddenimports=["passlib.handlers.bcrypt"],
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
    a.binaries,
    a.datas,
    [],
    name="storelimitless-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)