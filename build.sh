#!/usr/bin/env bash
clear
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

newest_match() {
    local dir="$1" name="$2"
    find "$dir" -maxdepth 1 -type f -name "$name" -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn \
        | head -1 \
        | cut -d' ' -f2-
}

require_file() {
    [ -f "$1" ] || die "required file not found: $1"
}

version_ge() {
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}

require_file "$ROOT/frontend/src-tauri/tauri.conf.json"
require_file "$ROOT/requirements.txt"
require_file "$ROOT/storelimitless-backend.spec"
require_file "$ROOT/build/dependencies/Memurai.msi"

VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
    "$ROOT/frontend/src-tauri/tauri.conf.json")

[ -n "$VERSION" ] || die "could not determine version from tauri.conf.json"

MEMURAI_MSI="$ROOT/build/dependencies/Memurai.msi"

echo "==> Building StoreLimitless version: $VERSION"

sudo -v
( while true; do sudo -n true; sleep 60; kill -0 "$$" 2>/dev/null || exit; done ) &
SUDO_KEEPALIVE_PID=$!
trap 'kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true' EXIT

echo
echo "==> Cleaning previous builds"

rm -rf \
    "$ROOT/build/pyi-linux" \
    "$ROOT/build/pyi-windows" \
    "$ROOT/build/runtime" \
    "$ROOT/build/python-"*.exe \
    "$ROOT/build/get-pip.py" \
    "$ROOT/dist" \
    "$ROOT/frontend/src-tauri/resources/backend" \
    "$ROOT/frontend/src-tauri/resources/Memurai.msi" \
    "$ROOT/frontend/src-tauri/target/release/bundle" \
    "$ROOT/frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle"

mkdir -p \
    "$ROOT/build/dependencies" \
    "$ROOT/dist/linux" \
    "$ROOT/dist/windows" \
    "$ROOT/releases/linux" \
    "$ROOT/releases/windows" \
    "$ROOT/frontend/src-tauri/resources/backend"

echo
echo "==> Checking build prerequisites"

if ! command -v apt-get >/dev/null 2>&1; then
    die "This build script requires Debian/Ubuntu."
fi

sudo dpkg --add-architecture i386
sudo apt-get update

sudo apt-get install -y \
    build-essential \
    clang \
    curl \
    wget \
    file \
    git \
    pkg-config \
    libssl-dev \
    libwebkit2gtk-4.1-dev \
    libxdo-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    nsis \
    lld \
    llvm \
    python3 \
    python3-pip \
    python3-venv \
    unzip \
    ffmpeg \
    redis-server

command -v makensis >/dev/null 2>&1 \
    || die "NSIS installation failed."

WINE_MIN_VERSION="9.13"

echo
echo "==> Checking Wine (>= $WINE_MIN_VERSION required)"

wine_version() {
    "$1" --version 2>/dev/null \
        | sed -E 's/^wine-([0-9]+(\.[0-9]+)*).*/\1/'
}

wine_is_new_enough() {
    local bin="$1" ver
    command -v "$bin" >/dev/null 2>&1 || return 1
    ver="$(wine_version "$bin")"
    [ -n "$ver" ] || return 1
    version_ge "$ver" "$WINE_MIN_VERSION"
}

if ! wine_is_new_enough wine && ! wine_is_new_enough wine64; then
    echo "==> Installing Wine from the WineHQ repository"

    . /etc/os-release
    DISTRO_CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"

    [ -n "$DISTRO_CODENAME" ] \
        || die "could not determine distro codename."

    sudo mkdir -pm755 /etc/apt/keyrings

    sudo wget -qO /etc/apt/keyrings/winehq-archive.key \
        https://dl.winehq.org/wine-builds/winehq.key \
        || die "could not download WineHQ signing key."

    sudo wget -qNP /etc/apt/sources.list.d/ \
        "https://dl.winehq.org/wine-builds/ubuntu/dists/${DISTRO_CODENAME}/winehq-${DISTRO_CODENAME}.sources" \
        || die "WineHQ has no repository for '$DISTRO_CODENAME'."

    sudo apt-get update

    sudo apt-get remove -y \
        wine wine64 wine32 wine-stable \
        2>/dev/null || true

    sudo apt-get install -y --install-recommends winehq-stable \
        || die "WineHQ installation failed."
fi

if wine_is_new_enough wine; then
    WINE_BIN="wine"
elif wine_is_new_enough wine64; then
    WINE_BIN="wine64"
else
    FOUND_WINE="$(wine_version wine 2>/dev/null || true)"
    die "Wine >= $WINE_MIN_VERSION is required (found: ${FOUND_WINE:-none})."
fi

WINEBOOT_BIN="wineboot"

command -v "$WINEBOOT_BIN" >/dev/null 2>&1 \
    || die "wineboot is unavailable."

echo "==> Wine: $("$WINE_BIN" --version)"

if ! command -v rustup >/dev/null 2>&1; then
    echo "==> Installing Rust"
    curl --proto '=https' --tlsv1.2 -sSf \
        https://sh.rustup.rs | sh -s -- -y
fi

source "$HOME/.cargo/env" 2>/dev/null || true

command -v cargo >/dev/null 2>&1 \
    || die "cargo is not available."

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "==> Installing Node.js"

    curl -fsSL \
        https://deb.nodesource.com/setup_22.x \
        | sudo -E bash -

    sudo apt-get install -y nodejs
fi

echo "==> Node: $(node --version)"
echo "==> npm: $(npm --version)"

if ! command -v cargo-xwin >/dev/null 2>&1; then
    echo "==> Installing cargo-xwin"
    cargo install --locked cargo-xwin
fi

echo "==> cargo-xwin: $(cargo-xwin --version)"

if ! rustup target list --installed \
    | grep -q '^x86_64-pc-windows-msvc$'; then

    echo "==> Installing Windows Rust target"
    rustup target add x86_64-pc-windows-msvc
fi

export WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml="
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"

"$WINEBOOT_BIN" --init >/dev/null 2>&1 || true
"$WINEBOOT_BIN" --update >/dev/null 2>&1 || true
"$WINEBOOT_BIN" --wait >/dev/null 2>&1 || true

BUILD_VENV="$ROOT/build/venv"

echo
echo "==> Preparing Linux Python build environment"

python3 -m venv "$BUILD_VENV"

"$BUILD_VENV/bin/python" -m pip install --upgrade pip
"$BUILD_VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
"$BUILD_VENV/bin/python" -m pip install pyinstaller

echo "==> Linux Python: $("$BUILD_VENV/bin/python" --version)"
echo "==> Linux PyInstaller: $("$BUILD_VENV/bin/python" -m PyInstaller --version)"

echo
echo "==> Checking Windows Python"

windows_python_ok() {
    "$WINE_BIN" python --version >/dev/null 2>&1 \
        && "$WINE_BIN" python -m pip --version >/dev/null 2>&1
}

if ! windows_python_ok; then
    if "$WINE_BIN" python --version >/dev/null 2>&1; then
        echo "==> Windows Python present but pip is missing"
    else
        echo "==> Windows Python not found"
        echo "==> Downloading Windows Python"

        PYTHON_VERSION="3.13.7"
        PYTHON_INSTALLER="$ROOT/build/python-${PYTHON_VERSION}-amd64.exe"
        PYTHON_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-amd64.exe"

        wget -q --show-progress \
            "$PYTHON_URL" \
            -O "$PYTHON_INSTALLER"

        [ -s "$PYTHON_INSTALLER" ] \
            || die "downloaded Python installer is empty."

        file "$PYTHON_INSTALLER" | grep -qi 'PE32' \
            || die "downloaded Python installer is not a Windows executable."

        "$WINE_BIN" "$PYTHON_INSTALLER" \
            /quiet \
            InstallAllUsers=0 \
            PrependPath=1 \
            Include_pip=1 \
            Include_test=0

        "$WINEBOOT_BIN" --wait >/dev/null 2>&1 || true

        rm -f "$PYTHON_INSTALLER"
    fi

    "$WINE_BIN" python --version >/dev/null 2>&1 \
        || die "Windows Python installation failed."

    if ! "$WINE_BIN" python -m pip --version >/dev/null 2>&1; then
        echo "==> Bootstrapping pip via ensurepip"

        "$WINE_BIN" python -m ensurepip \
            --upgrade \
            --default-pip \
            >/dev/null 2>&1 || true
    fi

    if ! "$WINE_BIN" python -m pip --version >/dev/null 2>&1; then
        echo "==> Falling back to get-pip.py"

        GET_PIP="$ROOT/build/get-pip.py"

        wget -q --show-progress \
            https://bootstrap.pypa.io/get-pip.py \
            -O "$GET_PIP" \
            || die "could not download get-pip.py."

        [ -s "$GET_PIP" ] \
            || die "downloaded get-pip.py is empty."

        "$WINE_BIN" python "$GET_PIP" \
            || die "get-pip.py failed under Wine."

        rm -f "$GET_PIP"
    fi
fi

windows_python_ok \
    || die "Windows Python is installed but pip is unavailable."

echo "==> Windows Python: $("$WINE_BIN" python --version)"
echo "==> Windows pip: $("$WINE_BIN" python -m pip --version)"

echo
echo "==> Installing Windows Python dependencies"

"$WINE_BIN" python -m pip install --upgrade pip
"$WINE_BIN" python -m pip install -r "$ROOT/requirements.txt"
"$WINE_BIN" python -m pip install pyinstaller

echo "==> Windows PyInstaller: $("$WINE_BIN" python -m PyInstaller --version)"

echo
echo "==> Preparing runtime binaries"

RUNTIME_DIR="$ROOT/build/runtime"

rm -rf "$RUNTIME_DIR"

mkdir -p \
    "$RUNTIME_DIR/linux" \
    "$RUNTIME_DIR/windows"

FFMPEG_LINUX="$(command -v ffmpeg || true)"

[ -n "$FFMPEG_LINUX" ] \
    || die "Linux FFmpeg was not installed."

[ -x "$FFMPEG_LINUX" ] \
    || die "Linux FFmpeg is not executable."

cp "$FFMPEG_LINUX" \
    "$RUNTIME_DIR/linux/ffmpeg"

REDIS_LINUX="$(command -v redis-server || true)"

[ -n "$REDIS_LINUX" ] \
    || die "Linux Redis was not installed."

[ -x "$REDIS_LINUX" ] \
    || die "Linux Redis is not executable."

cp "$REDIS_LINUX" \
    "$RUNTIME_DIR/linux/redis-server"

echo "==> Preparing Windows FFmpeg"

FFMPEG_WINDOWS_ROOT="$ROOT/build/ffmpeg-windows"
FFMPEG_WINDOWS_ARCHIVE="$ROOT/build/ffmpeg-windows.zip"

if [ ! -s "$FFMPEG_WINDOWS_ARCHIVE" ]; then
    echo "==> Downloading Windows FFmpeg"
    wget -q --show-progress \
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" \
        -O "$FFMPEG_WINDOWS_ARCHIVE" \
        || die "could not download Windows FFmpeg."
    [ -s "$FFMPEG_WINDOWS_ARCHIVE" ] \
        || die "Windows FFmpeg download is empty."
else
    echo "==> Using cached Windows FFmpeg archive"
fi

FFMPEG_WINDOWS="$(find "$FFMPEG_WINDOWS_ROOT" -type f -name "ffmpeg.exe" -print -quit)"

if [ -z "$FFMPEG_WINDOWS" ]; then
    echo "==> Extracting Windows FFmpeg"
    mkdir -p "$FFMPEG_WINDOWS_ROOT"
    unzip -q "$FFMPEG_WINDOWS_ARCHIVE" -d "$FFMPEG_WINDOWS_ROOT"
    FFMPEG_WINDOWS="$(find "$FFMPEG_WINDOWS_ROOT" -type f -name "ffmpeg.exe" -print -quit)"
else
    echo "==> Using cached extracted Windows FFmpeg"
fi

[ -n "$FFMPEG_WINDOWS" ] || die "Windows ffmpeg.exe was not found."

cp "$FFMPEG_WINDOWS" "$RUNTIME_DIR/windows/ffmpeg.exe"

echo
echo "==> Verifying runtime binaries"

require_file "$RUNTIME_DIR/linux/ffmpeg"
require_file "$RUNTIME_DIR/linux/redis-server"
require_file "$RUNTIME_DIR/windows/ffmpeg.exe"

file "$RUNTIME_DIR/linux/ffmpeg" | grep -q 'ELF .* executable' \
    || die "Linux FFmpeg is not a valid ELF executable."

file "$RUNTIME_DIR/linux/redis-server" | grep -q 'ELF .* executable' \
    || die "Linux Redis is not a valid ELF executable."

file "$RUNTIME_DIR/windows/ffmpeg.exe" | grep -qi 'PE32' \
    || die "Windows FFmpeg is not a valid Windows executable."

echo "==> Linux FFmpeg: $RUNTIME_DIR/linux/ffmpeg"
echo "==> Linux Redis:  $RUNTIME_DIR/linux/redis-server"
echo "==> Windows FFmpeg: $RUNTIME_DIR/windows/ffmpeg.exe"

echo
echo "==> Checking Memurai installer"

require_file "$MEMURAI_MSI"

MEMURAI_TYPE="$(file -b "$MEMURAI_MSI")"

echo "$MEMURAI_TYPE" | grep -Eiq \
    'Composite Document File|Microsoft Installer|MSI' \
    || die "Memurai installer does not appear to be a valid MSI: $MEMURAI_TYPE"

mkdir -p "$ROOT/frontend/src-tauri/resources"

cp "$MEMURAI_MSI" \
    "$ROOT/frontend/src-tauri/resources/Memurai.msi"

echo "==> Memurai installer: $MEMURAI_MSI"

echo
echo "==> Checking frontend dependencies"

cd "$ROOT/frontend"

if [ ! -d "node_modules" ]; then
    echo "==> Installing frontend dependencies"
    npm install
fi

npx tauri --version >/dev/null 2>&1 \
    || die "Tauri CLI is unavailable."

echo "==> Tauri: $(npx tauri --version)"

cd "$ROOT"

echo
echo "==> [1/4] Building Linux server"

export STORELIMITLESS_FFMPEG="$RUNTIME_DIR/linux/ffmpeg"
export STORELIMITLESS_REDIS="$RUNTIME_DIR/linux/redis-server"

"$BUILD_VENV/bin/python" -m PyInstaller \
    --clean \
    --workpath "$ROOT/build/pyi-linux" \
    --distpath "$ROOT/dist/linux" \
    "$ROOT/storelimitless-backend.spec"

LINUX_BACKEND="$ROOT/dist/linux/storelimitless-backend"

[ -f "$LINUX_BACKEND" ] \
    || die "Linux backend build failed."

file "$LINUX_BACKEND" | grep -q 'ELF .* executable' \
    || die "Linux backend is not a valid ELF executable."

rm -f "$ROOT/frontend/src-tauri/resources/backend/"*

cp "$LINUX_BACKEND" \
    "$ROOT/frontend/src-tauri/resources/backend/storelimitless-backend"

chmod +x \
    "$ROOT/frontend/src-tauri/resources/backend/storelimitless-backend"

cp "$LINUX_BACKEND" \
    "$ROOT/releases/linux/StoreLimitlessServer_$VERSION"

echo
echo "==> [2/4] Building Linux application"

cd "$ROOT/frontend"

npx tauri build --bundles deb

LINUX_PACKAGE=$(newest_match \
    "$ROOT/frontend/src-tauri/target/release/bundle/deb" \
    "*.deb")

[ -n "$LINUX_PACKAGE" ] \
    || die "Linux .deb was not produced."

cp "$LINUX_PACKAGE" \
    "$ROOT/releases/linux/"

echo
echo "==> [3/4] Building Windows server"

cd "$ROOT"

unset STORELIMITLESS_REDIS
export STORELIMITLESS_FFMPEG="$RUNTIME_DIR/windows/ffmpeg.exe"

"$WINE_BIN" python -m PyInstaller \
    --clean \
    --workpath "$ROOT/build/pyi-windows" \
    --distpath "$ROOT/dist/windows" \
    "$ROOT/storelimitless-backend.spec"

WINDOWS_BACKEND="$ROOT/dist/windows/storelimitless-backend.exe"

[ -f "$WINDOWS_BACKEND" ] \
    || die "Windows backend build failed."

file "$WINDOWS_BACKEND" | grep -qi 'PE32' \
    || die "Windows backend is not a valid Windows executable."

rm -f "$ROOT/frontend/src-tauri/resources/backend/"*

cp "$WINDOWS_BACKEND" \
    "$ROOT/frontend/src-tauri/resources/backend/storelimitless-backend.exe"

cp "$WINDOWS_BACKEND" \
    "$ROOT/releases/windows/StoreLimitlessServer_$VERSION.exe"

echo
echo "==> Verifying Windows Tauri resources"

require_file "$ROOT/frontend/src-tauri/resources/backend/storelimitless-backend.exe"
require_file "$ROOT/frontend/src-tauri/resources/Memurai.msi"

file "$ROOT/frontend/src-tauri/resources/backend/storelimitless-backend.exe" | grep -qi 'PE32' \
    || die "Windows backend resource is not a valid Windows executable."

MEMURAI_RESOURCE_SIZE="$(stat -c '%s' "$ROOT/frontend/src-tauri/resources/Memurai.msi")"
[ "$MEMURAI_RESOURCE_SIZE" -gt 0 ] \
    || die "Windows Memurai resource is empty."

echo "==> Windows backend resource verified."
echo "==> Windows Memurai resource verified."

echo
echo "==> [4/4] Building Windows NSIS application"

cd "$ROOT/frontend"

npx tauri build \
    --runner cargo-xwin \
    --target x86_64-pc-windows-msvc \
    --bundles nsis

NSIS_INSTALLER=$(newest_match \
    "$ROOT/frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis" \
    "*-setup.exe")

[ -n "$NSIS_INSTALLER" ] \
    || die "Windows NSIS installer was not produced."

file "$NSIS_INSTALLER" | grep -Eqi 'PE32|MS-DOS executable' \
    || die "Windows NSIS installer is not a valid Windows executable."

cp "$NSIS_INSTALLER" \
    "$ROOT/releases/windows/"

echo
echo "========================================"
echo "==> StoreLimitless $VERSION build complete"
echo "========================================"

echo
echo "==> Linux releases:"
find "$ROOT/releases/linux" -maxdepth 1 -type f -print

echo
echo "==> Windows releases:"
find "$ROOT/releases/windows" -maxdepth 1 -type f -print

echo
echo "==> All releases:"
find "$ROOT/releases" -maxdepth 2 -type f -print