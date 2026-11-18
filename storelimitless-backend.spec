
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).resolve()
FRONTEND_DIST = ROOT / "frontend" / "dist"

if not FRONTEND_DIST.is_dir():
    raise FileNotFoundError(
        f"Frontend build not found: {FRONTEND_DIST}\n"
        "Build it first with: cd frontend && npm install && npm run build"
    )

frontend_datas = [
    (
        str(path),
        str(
            Path("frontend")
            / "dist"
            / path.relative_to(FRONTEND_DIST).parent
        ),
    )
    for path in FRONTEND_DIST.rglob("*")
    if path.is_file()
]

# Collect gdown's package files, binaries, hidden imports, and metadata.
gdown_datas, gdown_binaries, gdown_hiddenimports = collect_all("gdown")
gdown_datas += copy_metadata("gdown")

a = Analysis(
    ["core/main.py"],
    pathex=[str(ROOT)],
    binaries=gdown_binaries,
    datas=frontend_datas + gdown_datas,
    hiddenimports=[
        "passlib.handlers.bcrypt",
    ] + gdown_hiddenimports,
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
