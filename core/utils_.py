"""Private helpers for the shared on-disk data-center chunk cache."""

from __future__ import annotations

from asyncio import Lock, to_thread
from collections import OrderedDict
from pathlib import Path
from shutil import rmtree, which
from socket import AF_INET, SOCK_DGRAM, socket
from subprocess import Popen, TimeoutExpired, run as subprocess_run
from time import monotonic
from typing import Any
from typing import cast

CACHE: OrderedDict[str, tuple[int, float]] = OrderedDict()
CACHE_SIZE: int = 0
CACHE_LOCK: Lock | None = None


def lock() -> Lock:
    """Return the lazily initialized async lock protecting cache metadata."""

    global CACHE_LOCK

    if CACHE_LOCK is None:
        CACHE_LOCK = Lock()

    return cast(Lock, CACHE_LOCK)


def part_path(cache_dir: Path, fid: str, part: int) -> Path:
    """Build the deterministic on-disk path for one cached file part."""

    if part < 0:
        raise ValueError("Cache part index cannot be negative")

    if not fid or Path(fid).name != fid or fid in {".", ".."}:
        raise ValueError("Invalid file cache identifier")

    return cache_dir / fid / f"part_{part:08d}"


async def update_cache_entry(path: str, size: int | None, cache_limit: int) -> None:
    """Insert, refresh, or remove a cache-index entry and enforce the cache size limit."""

    global CACHE_SIZE

    async with lock():
        old: tuple[int, float] | None = CACHE.pop(path, None)

        if old is not None:
            CACHE_SIZE -= old[0]

        if size is None:
            return

        CACHE[path] = (size, monotonic())
        CACHE_SIZE += size
        await evict(cache_limit)


async def evict(cache_limit: int) -> None:
    """Remove least-recently-used cached parts until the cache fits within its limit."""

    global CACHE_SIZE

    while CACHE_SIZE > cache_limit and CACHE:
        key: str
        size: int
        key, (size, _) = CACHE.popitem(last=False)
        path: Path = Path(key)

        try:
            path.unlink(missing_ok=True)

            try:
                path.parent.rmdir()

            except OSError:
                pass

        finally:
            CACHE_SIZE -= size


async def reset_cache(cache_dir: Path) -> None:
    """Recreate the disposable chunk cache and reset its in-memory accounting."""

    global CACHE_SIZE

    async with lock():
        if cache_dir.exists():
            await to_thread(rmtree, cache_dir)

        cache_dir.mkdir(parents=True, exist_ok=True)
        CACHE.clear()
        CACHE_SIZE = 0


def get_local_ip() -> str:
    """Discover the preferred outbound interface address, falling back offline safely."""

    try:
        with socket(AF_INET, SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])

    except OSError:
        return "127.0.0.1"


def stop_process(process: Popen[Any], timeout: int | float = 5) -> None:
    """Terminate a child process, escalating to kill if graceful shutdown times out."""

    if process.poll() is not None:
        return

    process.terminate()

    try:
        process.wait(timeout=timeout)

    except TimeoutExpired:
        process.kill()
        process.wait()


def ensure_frontend_dependencies(frontend_dir: Path) -> None:
    """Install frontend dependencies when the generated node_modules are absent."""

    package_json: Path = frontend_dir / "package.json"
    package_lock: Path = frontend_dir / "package-lock.json"
    vite_package: Path = frontend_dir / "node_modules" / "vite" / "package.json"

    if not package_json.is_file():
        raise FileNotFoundError(f"Frontend package.json was not found: {package_json}")

    if vite_package.is_file():
        return

    npm: str | None = which("npm")

    if npm is None:
        raise RuntimeError("npm is required to install frontend dependencies. Install Node.js and npm, then restart.")

    command: list[str] = [npm, "ci"] if package_lock.is_file() else [npm, "install"]
    print("Frontend dependencies are missing. Installing them before startup...")
    print(f"Running: {' '.join(command)}")
    subprocess_run(command, cwd=frontend_dir, check=True)

    if not vite_package.is_file():
        raise RuntimeError("Frontend dependency installation finished, but Vite was not found in node_modules.")

    print("Frontend dependencies are ready.")
