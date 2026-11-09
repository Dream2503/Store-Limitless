#!/usr/bin/env bash
# This file is checked out with LF endings; see the repository .gitattributes.

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

# Install native build prerequisites on common Linux distributions.
# Node is managed separately below so Vite always gets a supported version.
run_privileged() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "Installing build prerequisites needs root access. Install sudo or rerun this script as root."
    fi
}

package_installed() {
    local manager="$1"
    local package="$2"

    case "$manager" in
        apt) dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' ;;
        dnf|zypper) rpm -q "$package" >/dev/null 2>&1 ;;
        pacman) pacman -Q "$package" >/dev/null 2>&1 ;;
        apk) apk info -e "$package" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

install_missing_packages() {
    local manager="$1"
    shift
    local -a packages=("$@")
    local -a missing=()
    local package

    for package in "${packages[@]}"; do
        if ! package_installed "$manager" "$package"; then
            missing+=("$package")
        fi
    done

    if [[ "${#missing[@]}" -eq 0 ]]; then
        echo "==> Native build packages already installed"
        return
    fi

    echo "==> Installing native build packages: ${missing[*]}"
    case "$manager" in
        apt)
            run_privileged apt-get update
            run_privileged apt-get install -y "${missing[@]}"
            ;;
        dnf)
            run_privileged dnf install -y "${missing[@]}"
            ;;
        pacman)
            run_privileged pacman -Sy --needed --noconfirm "${missing[@]}"
            ;;
        zypper)
            run_privileged zypper --non-interactive refresh
            run_privileged zypper --non-interactive install --no-recommends "${missing[@]}"
            ;;
        apk)
            run_privileged apk add --no-cache "${missing[@]}"
            ;;
    esac
}

install_system_prerequisites() {
    local manager
    local -a packages=()

    if command -v apt-get >/dev/null 2>&1; then
        manager="apt"
        packages=(python3 python3-venv python3-dev build-essential file curl wget ca-certificates libffi-dev libssl-dev pkg-config xz-utils coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine wine64 winbind)
        fi
    elif command -v dnf >/dev/null 2>&1; then
        manager="dnf"
        packages=(python3 python3-devel gcc gcc-c++ make file curl wget ca-certificates libffi-devel openssl-devel pkgconf-pkg-config xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine winbind)
        fi
    elif command -v pacman >/dev/null 2>&1; then
        manager="pacman"
        packages=(python python-pip base-devel file curl wget ca-certificates openssl libffi pkgconf xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine winetricks)
        fi
    elif command -v zypper >/dev/null 2>&1; then
        manager="zypper"
        packages=(python3 python3-devel gcc gcc-c++ make file curl wget ca-certificates libffi-devel libopenssl-devel pkg-config xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine wine-32bit winbind)
        fi
    elif command -v apk >/dev/null 2>&1; then
        manager="apk"
        packages=(python3 py3-pip py3-virtualenv python3-dev build-base file curl wget ca-certificates libffi-dev openssl-dev pkgconf xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine)
        fi
    else
        # On uncommon distributions, allow users who already have every tool
        # to build without forcing an unsupported package-manager operation.
        for tool in python3 file curl wget tar xz sha256sum; do
            command -v "$tool" >/dev/null 2>&1 || die "No supported package manager was found and '$tool' is missing."
        done
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            for tool in wine wineboot winepath; do
                command -v "$tool" >/dev/null 2>&1 || die "Windows builds require '$tool'. Install Wine manually."
            done
        fi
        echo "==> No supported package manager detected; existing build tools will be used"
        return
    fi

    install_missing_packages "$manager" "${packages[@]}"

    for tool in python3 file curl wget tar xz sha256sum; do
        command -v "$tool" >/dev/null 2>&1 || die "'$tool' is still unavailable after prerequisite installation."
    done

    python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required to build StoreLimitless.")
PY

    if [[ "$BUILD_WINDOWS" == "1" ]]; then
        for tool in wine wineboot winepath; do
            command -v "$tool" >/dev/null 2>&1 || die "Windows builds require '$tool'. It was not found after installing Wine packages."
        done
    fi
}

node_supports_vite() {
    command -v node >/dev/null 2>&1 || return 1
    command -v npm >/dev/null 2>&1 || return 1

    node --version | python3 -c '
import sys
parts = sys.stdin.read().strip().lstrip("v").split(".")
try:
    major, minor = int(parts[0]), int(parts[1])
except (ValueError, IndexError):
    raise SystemExit(1)
supported = (major == 20 and minor >= 19) or (major == 22 and minor >= 12) or major > 22
raise SystemExit(0 if supported else 1)
'
}

ensure_node_runtime() {
    if node_supports_vite; then
        echo "==> Node $(node --version) and npm $(npm --version) are ready"
        return
    fi

    echo "==> Installing a supported Node.js LTS runtime locally for this build"
    local node_version="${NODE_RUNTIME_VERSION:-}"
    local node_arch
    local archive_name
    local archive_path
    local expected_sha
    local node_dir="$ROOT/build/node-runtime"

    if [[ -z "$node_version" ]]; then
        node_version="$(curl -fsSL --retry 3 https://nodejs.org/dist/index.json | python3 -c '
import json, sys
for release in json.load(sys.stdin):
    version = release.get("version", "")
    major = int(version.lstrip("v").split(".")[0]) if version else 0
    if release.get("lts") and major >= 22:
        print(version)
        break
')"
    fi
    [[ -n "$node_version" ]] || die "Could not determine a supported Node.js LTS release. Set NODE_RUNTIME_VERSION explicitly."

    [[ "$node_version" == v* ]] || node_version="v$node_version"
    [[ "$node_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Invalid NODE_RUNTIME_VERSION '$node_version'."

    case "$(uname -m)" in
        x86_64|amd64) node_arch="x64" ;;
        aarch64|arm64) node_arch="arm64" ;;
        *) die "Automatic Node.js installation does not support architecture $(uname -m)." ;;
    esac

    archive_name="node-${node_version}-linux-${node_arch}.tar.xz"
    archive_path="$ROOT/build/$archive_name"
    curl -fsSL --retry 3 "https://nodejs.org/dist/${node_version}/${archive_name}" -o "$archive_path" \
        || die "Could not download Node.js ${node_version}."
    expected_sha="$(curl -fsSL --retry 3 "https://nodejs.org/dist/${node_version}/SHASUMS256.txt" | awk -v filename="$archive_name" '$2 == filename {print $1; exit}')"
    [[ "$expected_sha" =~ ^[a-fA-F0-9]{64}$ ]] || die "Could not find the official SHA-256 checksum for ${archive_name}."
    printf '%s  %s\n' "$expected_sha" "$archive_path" | sha256sum -c - \
        || die "Node.js archive checksum validation failed."

    rm -rf -- "$node_dir"
    mkdir -p "$node_dir"
    tar -xJf "$archive_path" -C "$node_dir" --strip-components=1
    export PATH="$node_dir/bin:$PATH"

    node_supports_vite || die "Downloaded Node.js is not compatible with the installed frontend toolchain."
    echo "==> Node $(node --version) and npm $(npm --version) are ready"
}

ensure_build_prerequisites() {
    mkdir -p "$ROOT/build"
    install_system_prerequisites
    ensure_node_runtime

    if [[ "$BUILD_LINUX" == "1" ]]; then
        local probe_dir
        probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/storelimitless-venv-check.XXXXXX")"
        if ! python3 -m venv "$probe_dir/venv"; then
            rm -rf -- "$probe_dir"
            die "Python venv support is missing. Install the Python venv package for your interpreter, then retry."
        fi
        rm -rf -- "$probe_dir"
    fi
}

ensure_build_prerequisites

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
    env -u VITE_API_URL npm run build
)

[[ -f "$FRONTEND_DIST/index.html" ]] \
    || die "Frontend build failed: frontend/dist/index.html not found."
[[ -f "$FRONTEND_DIST/config.js" ]] \
    || die "Frontend build failed: frontend/dist/config.js is missing; check frontend/public/config.js."
grep -q 'src="/config.js"' "$FRONTEND_DIST/index.html" \
    || die "Frontend build failed: index.html does not load the runtime API config."
echo "==> Frontend build ready: $FRONTEND_DIST (runtime API configuration enabled)"

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
\r' "$0"; then
    die "build.sh contains CRLF line endings. Run: sed -i 's/\r$//' build.sh"
fi

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

# Install native build prerequisites on common Linux distributions.
# Node is managed separately below so Vite always gets a supported version.
run_privileged() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "Installing build prerequisites needs root access. Install sudo or rerun this script as root."
    fi
}

package_installed() {
    local manager="$1"
    local package="$2"

    case "$manager" in
        apt) dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' ;;
        dnf|zypper) rpm -q "$package" >/dev/null 2>&1 ;;
        pacman) pacman -Q "$package" >/dev/null 2>&1 ;;
        apk) apk info -e "$package" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

install_missing_packages() {
    local manager="$1"
    shift
    local -a packages=("$@")
    local -a missing=()
    local package

    for package in "${packages[@]}"; do
        if ! package_installed "$manager" "$package"; then
            missing+=("$package")
        fi
    done

    if [[ "${#missing[@]}" -eq 0 ]]; then
        echo "==> Native build packages already installed"
        return
    fi

    echo "==> Installing native build packages: ${missing[*]}"
    case "$manager" in
        apt)
            run_privileged apt-get update
            run_privileged apt-get install -y "${missing[@]}"
            ;;
        dnf)
            run_privileged dnf install -y "${missing[@]}"
            ;;
        pacman)
            run_privileged pacman -Sy --needed --noconfirm "${missing[@]}"
            ;;
        zypper)
            run_privileged zypper --non-interactive refresh
            run_privileged zypper --non-interactive install --no-recommends "${missing[@]}"
            ;;
        apk)
            run_privileged apk add --no-cache "${missing[@]}"
            ;;
    esac
}

install_system_prerequisites() {
    local manager
    local -a packages=()

    if command -v apt-get >/dev/null 2>&1; then
        manager="apt"
        packages=(python3 python3-venv python3-dev build-essential file curl wget ca-certificates libffi-dev libssl-dev pkg-config xz-utils coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine wine64 winbind)
        fi
    elif command -v dnf >/dev/null 2>&1; then
        manager="dnf"
        packages=(python3 python3-devel gcc gcc-c++ make file curl wget ca-certificates libffi-devel openssl-devel pkgconf-pkg-config xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine winbind)
        fi
    elif command -v pacman >/dev/null 2>&1; then
        manager="pacman"
        packages=(python python-pip base-devel file curl wget ca-certificates openssl libffi pkgconf xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine winetricks)
        fi
    elif command -v zypper >/dev/null 2>&1; then
        manager="zypper"
        packages=(python3 python3-devel gcc gcc-c++ make file curl wget ca-certificates libffi-devel libopenssl-devel pkg-config xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine wine-32bit winbind)
        fi
    elif command -v apk >/dev/null 2>&1; then
        manager="apk"
        packages=(python3 py3-pip py3-virtualenv python3-dev build-base file curl wget ca-certificates libffi-dev openssl-dev pkgconf xz coreutils)
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            packages+=(wine)
        fi
    else
        # On uncommon distributions, allow users who already have every tool
        # to build without forcing an unsupported package-manager operation.
        for tool in python3 file curl wget tar xz sha256sum; do
            command -v "$tool" >/dev/null 2>&1 || die "No supported package manager was found and '$tool' is missing."
        done
        if [[ "$BUILD_WINDOWS" == "1" ]]; then
            for tool in wine wineboot winepath; do
                command -v "$tool" >/dev/null 2>&1 || die "Windows builds require '$tool'. Install Wine manually."
            done
        fi
        echo "==> No supported package manager detected; existing build tools will be used"
        return
    fi

    install_missing_packages "$manager" "${packages[@]}"

    for tool in python3 file curl wget tar xz sha256sum; do
        command -v "$tool" >/dev/null 2>&1 || die "'$tool' is still unavailable after prerequisite installation."
    done

    python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required to build StoreLimitless.")
PY

    if [[ "$BUILD_WINDOWS" == "1" ]]; then
        for tool in wine wineboot winepath; do
            command -v "$tool" >/dev/null 2>&1 || die "Windows builds require '$tool'. It was not found after installing Wine packages."
        done
    fi
}

node_supports_vite() {
    command -v node >/dev/null 2>&1 || return 1
    command -v npm >/dev/null 2>&1 || return 1

    node --version | python3 -c '
import sys
parts = sys.stdin.read().strip().lstrip("v").split(".")
try:
    major, minor = int(parts[0]), int(parts[1])
except (ValueError, IndexError):
    raise SystemExit(1)
supported = (major == 20 and minor >= 19) or (major == 22 and minor >= 12) or major > 22
raise SystemExit(0 if supported else 1)
'
}

ensure_node_runtime() {
    if node_supports_vite; then
        echo "==> Node $(node --version) and npm $(npm --version) are ready"
        return
    fi

    echo "==> Installing a supported Node.js LTS runtime locally for this build"
    local node_version="${NODE_RUNTIME_VERSION:-}"
    local node_arch
    local archive_name
    local archive_path
    local expected_sha
    local node_dir="$ROOT/build/node-runtime"

    if [[ -z "$node_version" ]]; then
        node_version="$(curl -fsSL --retry 3 https://nodejs.org/dist/index.json | python3 -c '
import json, sys
for release in json.load(sys.stdin):
    version = release.get("version", "")
    major = int(version.lstrip("v").split(".")[0]) if version else 0
    if release.get("lts") and major >= 22:
        print(version)
        break
')"
    fi
    [[ -n "$node_version" ]] || die "Could not determine a supported Node.js LTS release. Set NODE_RUNTIME_VERSION explicitly."

    [[ "$node_version" == v* ]] || node_version="v$node_version"
    [[ "$node_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Invalid NODE_RUNTIME_VERSION '$node_version'."

    case "$(uname -m)" in
        x86_64|amd64) node_arch="x64" ;;
        aarch64|arm64) node_arch="arm64" ;;
        *) die "Automatic Node.js installation does not support architecture $(uname -m)." ;;
    esac

    archive_name="node-${node_version}-linux-${node_arch}.tar.xz"
    archive_path="$ROOT/build/$archive_name"
    curl -fsSL --retry 3 "https://nodejs.org/dist/${node_version}/${archive_name}" -o "$archive_path" \
        || die "Could not download Node.js ${node_version}."
    expected_sha="$(curl -fsSL --retry 3 "https://nodejs.org/dist/${node_version}/SHASUMS256.txt" | awk -v filename="$archive_name" '$2 == filename {print $1; exit}')"
    [[ "$expected_sha" =~ ^[a-fA-F0-9]{64}$ ]] || die "Could not find the official SHA-256 checksum for ${archive_name}."
    printf '%s  %s\n' "$expected_sha" "$archive_path" | sha256sum -c - \
        || die "Node.js archive checksum validation failed."

    rm -rf -- "$node_dir"
    mkdir -p "$node_dir"
    tar -xJf "$archive_path" -C "$node_dir" --strip-components=1
    export PATH="$node_dir/bin:$PATH"

    node_supports_vite || die "Downloaded Node.js is not compatible with the installed frontend toolchain."
    echo "==> Node $(node --version) and npm $(npm --version) are ready"
}

ensure_build_prerequisites() {
    mkdir -p "$ROOT/build"
    install_system_prerequisites
    ensure_node_runtime

    if [[ "$BUILD_LINUX" == "1" ]]; then
        local probe_dir
        probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/storelimitless-venv-check.XXXXXX")"
        if ! python3 -m venv "$probe_dir/venv"; then
            rm -rf -- "$probe_dir"
            die "Python venv support is missing. Install the Python venv package for your interpreter, then retry."
        fi
        rm -rf -- "$probe_dir"
    fi
}

ensure_build_prerequisites

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
    env -u VITE_API_URL npm run build
)

[[ -f "$FRONTEND_DIST/index.html" ]] \
    || die "Frontend build failed: frontend/dist/index.html not found."
[[ -f "$FRONTEND_DIST/config.js" ]] \
    || die "Frontend build failed: frontend/dist/config.js is missing; check frontend/public/config.js."
grep -q 'src="/config.js"' "$FRONTEND_DIST/index.html" \
    || die "Frontend build failed: index.html does not load the runtime API config."
echo "==> Frontend build ready: $FRONTEND_DIST (runtime API configuration enabled)"

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
