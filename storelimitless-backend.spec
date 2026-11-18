# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from typing import Any

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT: Path = Path(SPECPATH).resolve()
FRONTEND_DIST: Path = ROOT / "frontend" / "dist"
INCLUDE_FRONTEND: bool = os.environ.get("INCLUDE_FRONTEND", "1") == "1"
frontend_datas: list[tuple[str, str]] = []

if INCLUDE_FRONTEND:
    if not FRONTEND_DIST.is_dir():
        raise FileNotFoundError(f"Frontend build not found: {FRONTEND_DIST}\nBuild it first with: cd frontend && npm install && npm run build")

    frontend_datas = [
        (str(path), str(Path("frontend") / "dist" / path.relative_to(FRONTEND_DIST).parent),) for path in FRONTEND_DIST.rglob("*") if path.is_file()
    ]

gdown_datas: list[tuple[str, str]]
gdown_binaries: list[tuple[str, str]]
gdown_hiddenimports: list[str]
gdown_datas, gdown_binaries, gdown_hiddenimports = collect_all("gdown")
gdown_datas += copy_metadata("gdown")

libtorrent_datas: list[tuple[str, str]]
libtorrent_binaries: list[tuple[str, str]]
libtorrent_hiddenimports: list[str]
libtorrent_datas, libtorrent_binaries, libtorrent_hiddenimports = collect_all("libtorrent")
libtorrent_datas += copy_metadata("libtorrent")

a: Any = Analysis(
    ["core/main.py"],
    pathex=[str(ROOT)],
    binaries=gdown_binaries + libtorrent_binaries,
    datas=frontend_datas + gdown_datas + libtorrent_datas,
    hiddenimports=["passlib.handlers.bcrypt"] + gdown_hiddenimports + libtorrent_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz: Any = PYZ(a.pure)

exe: Any = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="StoreLimitlessServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
