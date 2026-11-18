#!/usr/bin/bash

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${VERSION:-0.1.0}"

exec > >(tee "$ROOT/build.log") 2>&1

die() {
    echo "ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<USAGE
Usage: $0 [<linux_server:0|1> <windows_server:0|1>]

Without arguments, builds only the Linux CLOUD executable.

When arguments are provided, each selected OS builds both HOST variants: LOCAL and CLOUD.

Examples:
  $0          # Linux CLOUD executable only
  $0 1 0      # 2 Linux executables (LOCAL + CLOUD)
  $0 0 1      # 2 Windows executables (LOCAL + CLOUD)
  $0 1 1      # all 4 executables
USAGE
}

DEFAULT_CLOUD_ONLY=0

if [[ $# -eq 0 ]]; then
    BUILD_LINUX=1
    BUILD_WINDOWS=0
    DEFAULT_CLOUD_ONLY=1
elif [[ $# -eq 2 ]]; then
    for arg in "$@"; do
        [[ "$arg" == "0" || "$arg" == "1" ]] || { usage; exit 1; }
    done

    BUILD_LINUX="$1"
    BUILD_WINDOWS="$2"
else
    usage
    exit 1
fi

if [[ "$BUILD_LINUX" != "1" && "$BUILD_WINDOWS" != "1" ]]; then
    die "Nothing to build. Select Linux, Windows, or both."
fi

[[ "$VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] \
    || die "Invalid VERSION '$VERSION'. Use only letters, digits, dots, underscores, plus, and hyphens."

SPEC="$ROOT/storelimitless-backend.spec"
REQUIREMENTS="$ROOT/requirements.txt"
CONFIG="$ROOT/core/config.py"
FRONTEND="$ROOT/frontend"
FRONTEND_DIST="$FRONTEND/dist"
SDK_BIN_ROOT="$ROOT/Store-Limitless-api/src/storelimitless/bin"
RELEASE_ROOT="$ROOT/releases"
LINUX_RELEASE_DIR="$RELEASE_ROOT/linux"
WINDOWS_RELEASE_DIR="$RELEASE_ROOT/windows"

command -v python3 >/dev/null 2>&1 || die "python3 is required."
command -v file >/dev/null 2>&1 || die "'file' is required."
command -v npm >/dev/null 2>&1 || die "npm is required to build the frontend."

[[ -f "$SPEC" ]] || die "Missing PyInstaller spec: $SPEC"
[[ -f "$REQUIREMENTS" ]] || die "Missing requirements.txt: $REQUIREMENTS"
[[ -f "$CONFIG" ]] || die "Missing host configuration: $CONFIG"
[[ -f "$FRONTEND/package.json" ]] || die "Missing frontend/package.json"

# Verify that the expected HOST setting exists before making any changes.
python3 - "$CONFIG" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r'^HOST:\s*Literal\[\s*"LOCAL"\s*,\s*"CLOUD"\s*\]\s*=\s*"(?:LOCAL|CLOUD)"\s*(?:#.*)?$',
    re.MULTILINE,
)
if len(pattern.findall(text)) != 1:
    raise SystemExit(
        f"Expected exactly one HOST: Literal[\"LOCAL\", \"CLOUD\"] = \"LOCAL|CLOUD\" assignment in {path}"
    )
PY

# The source config must be restored even if any of the builds fail or are interrupted.
CONFIG_BACKUP="$(mktemp "${TMPDIR:-/tmp}/storelimitless-config.XXXXXX")"
cp "$CONFIG" "$CONFIG_BACKUP"
restore_build_state() {
    if [[ -n "${CONFIG_BACKUP:-}" && -f "$CONFIG_BACKUP" ]]; then
        cp "$CONFIG_BACKUP" "$CONFIG" || echo "WARNING: Could not restore $CONFIG from backup." >&2
        rm -f -- "$CONFIG_BACKUP"
    fi
}
trap restore_build_state EXIT

set_host() {
    local host="$1"
    [[ "$host" == "LOCAL" || "$host" == "CLOUD" ]] \
        || die "Internal error: unsupported HOST '$host'."

    python3 - "$CONFIG" "$host" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
host = sys.argv[2]
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r'^(HOST:\s*Literal\[\s*"LOCAL"\s*,\s*"CLOUD"\s*\]\s*=\s*)"(?:LOCAL|CLOUD)"(\s*(?:#.*)?)$',
    re.MULTILINE,
)
updated, count = pattern.subn(lambda m: f'{m.group(1)}"{host}"{m.group(2)}', text)
if count != 1:
    raise SystemExit(f"Could not set HOST={host} in {path}; expected one HOST assignment, found {count}.")
path.write_text(updated, encoding="utf-8")
PY
    # Prevent a same-second/same-size source edit from reusing stale bytecode.
    rm -f -- "$ROOT/core/__pycache__"/config.*.pyc
    echo "==> core/config.py HOST set to $host"
}

linux_artifact_name() {
    printf 'StoreLimitlessServer.%s._%s' "$1" "$VERSION"
}

windows_artifact_name() {
    local host="$1"
    # Preserve the requested Windows LOCAL filename spelling exactly.
    if [[ "$host" == "LOCAL" ]]; then
        printf 'StoreLimitlessServer.LOCAL_%s.exe' "$VERSION"
    else
        printf 'StoreLimitlessServer.CLOUD._%s.exe' "$VERSION"
    fi
}

copy_local_to_sdk_bin() {
    local os="$1"
    local binary="$2"
    local sdk_dir="$SDK_BIN_ROOT/$os"
    local sdk_name

    mkdir -p "$sdk_dir"
    sdk_name="StoreLimitlessServer_$VERSION"
    [[ "$os" == "windows" ]] && sdk_name+=".exe"

    # The SDK bundle must contain only the normal-named LOCAL executable for
    # this OS, not the CLOUD variant or stale versions of the server binary.
    find "$sdk_dir" -maxdepth 1 -type f -name 'StoreLimitlessServer*' -delete
    cp "$binary" "$sdk_dir/$sdk_name"
    if [[ "$os" == "linux" ]]; then
        chmod +x "$sdk_dir/$sdk_name"
    fi

    echo "==> SDK LOCAL binary ready: $sdk_dir/$sdk_name"
}

echo "========================================"
echo "StoreLimitless Server Build"
echo "Version: $VERSION"
if [[ "$DEFAULT_CLOUD_ONLY" == "1" ]]; then
    echo "Linux server:   CLOUD only (default)"
    echo "Windows server: disabled"
else
    echo "Linux server:   $BUILD_LINUX (LOCAL + CLOUD)"
    echo "Windows server: $BUILD_WINDOWS (LOCAL + CLOUD)"
fi
echo "========================================"

mkdir -p "$ROOT/build" "$RELEASE_ROOT" "$SDK_BIN_ROOT"

# --------------------------------------------------
# Clear old release outputs and SDK binaries before starting this build.
# Keep the releases/ and bin/ root directories, but remove their contents.
# --------------------------------------------------

echo
echo "==> Clearing previous release outputs and SDK binaries"

for output_dir in "$RELEASE_ROOT" "$SDK_BIN_ROOT"; do
    mkdir -p "$output_dir"
    find "$output_dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
done

mkdir -p \
    "$LINUX_RELEASE_DIR" \
    "$WINDOWS_RELEASE_DIR" \
    "$SDK_BIN_ROOT/linux" \
    "$SDK_BIN_ROOT/windows"

echo "==> Previous release outputs and SDK binaries cleared"

# --------------------------------------------------
# Build the frontend once. Both HOST variants share the same browser assets;
# HOST changes server-side startup/hosting behavior in core/config.py.
# --------------------------------------------------

echo
echo "==> Building React frontend"
(
    cd "$FRONTEND"
    if [[ -f package-lock.json ]]; then
        npm ci
    else
        npm install
    fi
    npm run build
)

[[ -f "$FRONTEND_DIST/index.html" ]] \
    || die "Frontend build failed: frontend/dist/index.html not found."
echo "==> Frontend build ready: $FRONTEND_DIST"

# --------------------------------------------------
# Linux server: package LOCAL and CLOUD independently using a dedicated
# PyInstaller work/dist directory for each setting.
# --------------------------------------------------

if [[ "$BUILD_LINUX" == "1" ]]; then
    echo
    echo "==> Preparing Linux build environment"

    LINUX_VENV="$ROOT/build/venv-linux"
    python3 -m venv "$LINUX_VENV"
    "$LINUX_VENV/bin/python" -m pip install --upgrade pip
    "$LINUX_VENV/bin/python" -m pip install -r "$REQUIREMENTS"
    "$LINUX_VENV/bin/python" -m pip install pyinstaller

    for host in LOCAL CLOUD; do
        if [[ "$DEFAULT_CLOUD_ONLY" == "1" && "$host" != "CLOUD" ]]; then
            continue
        fi

        echo
        echo "==> Building Linux HOST=$host"
        set_host "$host"

        LINUX_WORKPATH="$ROOT/build/pyi-linux-$host"
        LINUX_DISTPATH="$ROOT/build/dist-linux-$host"
        rm -rf -- "$LINUX_WORKPATH" "$LINUX_DISTPATH"
        mkdir -p "$LINUX_DISTPATH"

        if [[ "$host" == "LOCAL" ]]; then
            INCLUDE_FRONTEND=1
        else
            INCLUDE_FRONTEND=0
        fi

        INCLUDE_FRONTEND="$INCLUDE_FRONTEND" "$LINUX_VENV/bin/python" -m PyInstaller \
            --clean \
            --noconfirm \
            --workpath "$LINUX_WORKPATH" \
            --distpath "$LINUX_DISTPATH" \
            "$SPEC"

        LINUX_BINARY="$LINUX_DISTPATH/StoreLimitlessServer"
        [[ -f "$LINUX_BINARY" ]] \
            || die "Linux HOST=$host executable was not generated. Check the executable name in the PyInstaller spec."
        file "$LINUX_BINARY" | grep -q 'ELF .* executable' \
            || die "Linux HOST=$host output is not an ELF executable."

        LINUX_OUTPUT="$LINUX_RELEASE_DIR/$(linux_artifact_name "$host")"
        cp "$LINUX_BINARY" "$LINUX_OUTPUT"
        chmod +x "$LINUX_OUTPUT"
        echo "==> Linux HOST=$host ready: $LINUX_OUTPUT"

        if [[ "$host" == "LOCAL" ]]; then
            copy_local_to_sdk_bin linux "$LINUX_BINARY"
        fi
    done
fi

# --------------------------------------------------
# Windows server: use Wine's Windows Python/PyInstaller environment and make
# two independent builds, one for each core.config.HOST setting.
# --------------------------------------------------

if [[ "$BUILD_WINDOWS" == "1" ]]; then
    echo
    echo "==> Preparing Windows build environment using Wine"

    command -v wine >/dev/null 2>&1 \
        || command -v wine64 >/dev/null 2>&1 \
        || die "Wine is required. Install Wine before building the Windows server."
    command -v wineboot >/dev/null 2>&1 || die "wineboot is required."
    command -v winepath >/dev/null 2>&1 || die "winepath is required."
    command -v wget >/dev/null 2>&1 || die "wget is required to download Windows Python."

    if command -v wine >/dev/null 2>&1; then
        WINE_BIN="wine"
    else
        WINE_BIN="wine64"
    fi

    export WINEDEBUG=-all
    export WINEDLLOVERRIDES="mscoree,mshtml="
    export WINEPREFIX="$ROOT/build/wine-windows"
    export WINEARCH=win64
    mkdir -p "$WINEPREFIX"

    if [[ ! -f "$WINEPREFIX/system.reg" ]]; then
        echo "==> Initializing 64-bit Wine prefix"
        "$WINE_BIN" wineboot --init || die "Could not initialize the Wine prefix."
    fi
    "$WINE_BIN" wineboot --wait >/dev/null 2>&1 || true

    WINDOWS_PYTHON="$WINEPREFIX/drive_c/StoreLimitlessPython/python.exe"
    PYTHON_VERSION="3.13.7"
    PYTHON_INSTALLER="$ROOT/build/python-${PYTHON_VERSION}-amd64.exe"
    PYTHON_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-amd64.exe"

    if [[ ! -f "$WINDOWS_PYTHON" ]]; then
        echo "==> Downloading Windows Python $PYTHON_VERSION"
        wget -q --show-progress "$PYTHON_URL" -O "$PYTHON_INSTALLER" \
            || die "Windows Python installer download failed."
        [[ -s "$PYTHON_INSTALLER" ]] || die "Windows Python installer download was empty."
        file "$PYTHON_INSTALLER" | grep -qi 'PE32' \
            || die "Downloaded Python installer is not a Windows executable."

        echo "==> Installing Windows Python"
        "$WINE_BIN" "$PYTHON_INSTALLER" \
            /quiet \
            InstallAllUsers=0 \
            TargetDir='C:\StoreLimitlessPython' \
            PrependPath=0 \
            Include_pip=1 \
            Include_test=0 \
            || die "Windows Python installation failed."
        "$WINE_BIN" wineboot --wait >/dev/null 2>&1 || true
    fi

    [[ -f "$WINDOWS_PYTHON" ]] || die "Windows Python was not found after installation."
    echo "==> Windows Python:"
    "$WINE_BIN" "$WINDOWS_PYTHON" --version

    if ! "$WINE_BIN" "$WINDOWS_PYTHON" -m pip --version >/dev/null 2>&1; then
        echo "==> pip is missing; attempting to bootstrap it with ensurepip"
        "$WINE_BIN" "$WINDOWS_PYTHON" -m ensurepip --upgrade || true
    fi

    if ! "$WINE_BIN" "$WINDOWS_PYTHON" -m pip --version >/dev/null 2>&1; then
        echo "==> ensurepip did not provide pip; downloading get-pip.py"
        GET_PIP="$ROOT/build/get-pip.py"
        wget -q --show-progress "https://bootstrap.pypa.io/get-pip.py" -O "$GET_PIP" \
            || die "Could not download get-pip.py."
        [[ -s "$GET_PIP" ]] || die "get-pip.py download was empty."
        GET_PIP_WINDOWS="$(winepath -w "$GET_PIP")"
        "$WINE_BIN" "$WINDOWS_PYTHON" "$GET_PIP_WINDOWS" \
            || die "Could not bootstrap pip for Windows Python."
    fi

    "$WINE_BIN" "$WINDOWS_PYTHON" -m pip --version \
        || die "pip is still unavailable in the Windows Python installation."

    echo "==> Installing Windows Python dependencies"
    "$WINE_BIN" "$WINDOWS_PYTHON" -m pip install --upgrade pip
    WINDOWS_REQUIREMENTS="$(winepath -w "$REQUIREMENTS")"
    "$WINE_BIN" "$WINDOWS_PYTHON" -m pip install -r "$WINDOWS_REQUIREMENTS"
    "$WINE_BIN" "$WINDOWS_PYTHON" -m pip install pyinstaller
    echo "==> Windows PyInstaller:"
    "$WINE_BIN" "$WINDOWS_PYTHON" -m PyInstaller --version

    WINDOWS_SPEC="$(winepath -w "$SPEC")"

    for host in LOCAL CLOUD; do
        echo
        echo "==> Building Windows HOST=$host"
        set_host "$host"

        WINDOWS_WORKPATH_UNIX="$ROOT/build/pyi-windows-$host"
        WINDOWS_DISTPATH_UNIX="$ROOT/build/dist-windows-$host"
        rm -rf -- "$WINDOWS_WORKPATH_UNIX" "$WINDOWS_DISTPATH_UNIX"
        mkdir -p "$WINDOWS_WORKPATH_UNIX" "$WINDOWS_DISTPATH_UNIX"

        WINDOWS_WORKPATH="$(winepath -w "$WINDOWS_WORKPATH_UNIX")"
        WINDOWS_DISTPATH="$(winepath -w "$WINDOWS_DISTPATH_UNIX")"

        if [[ "$host" == "LOCAL" ]]; then
            INCLUDE_FRONTEND=1
        else
            INCLUDE_FRONTEND=0
        fi

        INCLUDE_FRONTEND="$INCLUDE_FRONTEND" "$WINE_BIN" "$WINDOWS_PYTHON" -m PyInstaller \
            --clean \
            --noconfirm \
            --workpath "$WINDOWS_WORKPATH" \
            --distpath "$WINDOWS_DISTPATH" \
            "$WINDOWS_SPEC"

        WINDOWS_BINARY="$WINDOWS_DISTPATH_UNIX/StoreLimitlessServer.exe"
        [[ -f "$WINDOWS_BINARY" ]] \
            || die "Windows HOST=$host executable was not generated. Check the executable name in the PyInstaller spec."
        file "$WINDOWS_BINARY" | grep -qi 'PE32' \
            || die "Windows HOST=$host output is not a Windows executable."

        WINDOWS_OUTPUT="$WINDOWS_RELEASE_DIR/$(windows_artifact_name "$host")"
        cp "$WINDOWS_BINARY" "$WINDOWS_OUTPUT"
        echo "==> Windows HOST=$host ready: $WINDOWS_OUTPUT"

        if [[ "$host" == "LOCAL" ]]; then
            copy_local_to_sdk_bin windows "$WINDOWS_BINARY"
        fi
    done
fi

echo
echo "========================================"
echo "Build complete"
echo "========================================"

echo
echo "==> Linux release variants:"
if [[ -d "$LINUX_RELEASE_DIR" ]]; then
    find "$LINUX_RELEASE_DIR" -maxdepth 1 -type f -name 'StoreLimitlessServer*' -print | sort
fi

echo
echo "==> Windows release variants:"
if [[ -d "$WINDOWS_RELEASE_DIR" ]]; then
    find "$WINDOWS_RELEASE_DIR" -maxdepth 1 -type f -name 'StoreLimitlessServer*' -print | sort
fi

echo
echo "==> SDK LOCAL binaries:"
find "$SDK_BIN_ROOT" -maxdepth 2 -type f -name 'StoreLimitlessServer*' -print 2>/dev/null | sort || true

# Remove regeneratable artifacts only after all selected builds succeed.
echo
echo "==> Removing temporary build artifacts"
rm -rf -- "$ROOT/build" "$FRONTEND/node_modules" "$FRONTEND_DIST"

# Next.js output and installed packages can be recreated from the lockfile.
BACKEND_STORELIMITLESSBACKEND="$ROOT/backend/storelimitlessbackend"
if [[ -d "$BACKEND_STORELIMITLESSBACKEND" ]]; then
    echo "==> Removing regeneratable backend/storelimitlessbackend artifacts"
    rm -rf -- \
        "$BACKEND_STORELIMITLESSBACKEND/.next" \
        "$BACKEND_STORELIMITLESSBACKEND/node_modules" \
        "$BACKEND_STORELIMITLESSBACKEND/.turbo" \
        "$BACKEND_STORELIMITLESSBACKEND/coverage"
fi

echo "==> Removing Python bytecode caches"
find "$ROOT" \
    -type d \( -name .git -o -name .venv -o -name venv -o -name node_modules -o -name .next \) -prune -o \
    -type d -name '__pycache__' -prune -exec rm -rf -- {} +
find "$ROOT" \
    -type d \( -name .git -o -name .venv -o -name venv -o -name node_modules -o -name .next \) -prune -o \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) -exec rm -f -- {} +

echo "==> Removed build/, frontend build/dependencies, backend Next.js build/dependencies, and project bytecode caches"
echo "==> Release executables are in releases/linux/ and releases/windows/"
echo "==> Kept the selected release variants and LOCAL SDK binary copies"
echo "==> Build cleanup complete"

# This log is useful on failures, but not needed after a successful build.
rm -f -- "$ROOT/build.log"
