"""Install and launch required external programs such as FFmpeg, Redis, and cloudflared."""

from __future__ import annotations

import os
import platform
import shutil
from os import chmod
from pathlib import Path
from queue import Empty, Full, Queue
from re import Match, search
from socket import create_connection
from subprocess import PIPE, STDOUT, CalledProcessError, Popen, TimeoutExpired, run
from threading import Thread
from time import monotonic, sleep
from urllib import request
from zipfile import ZipFile

from core.config import TRANSFER_PATH
from core.data_center import Binaries
from core.utils import write_log


def get_ffmpeg_path() -> Path:
    """Return FFmpeg's executable path, installing it automatically on supported systems."""

    ffmpeg_executable: str | None = shutil.which("ffmpeg")

    if ffmpeg_executable is not None:
        return Path(ffmpeg_executable)

    system: str = platform.system()

    if system == "Windows":
        ffmpeg_dir: Path = Path.home() / "AppData" / "Local" / "StoreLimitless" / "ffmpeg"
        ffmpeg_path: Path = ffmpeg_dir / "ffmpeg.exe"

        if not ffmpeg_path.is_file():
            write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", "FFmpeg is not installed. Downloading and installing FFmpeg for you...")
            ffmpeg_dir.mkdir(parents=True, exist_ok=True)
            archive: Path = ffmpeg_dir / "ffmpeg.zip"

            try:
                request.urlretrieve("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", archive)

                with ZipFile(archive) as zip_file:
                    ffmpeg_files: list[str] = [name for name in zip_file.namelist() if name.endswith("/bin/ffmpeg.exe")]

                    if not ffmpeg_files:
                        raise RuntimeError("FFmpeg was downloaded, but ffmpeg.exe could not be found.")

                    with zip_file.open(ffmpeg_files[0]) as source:
                        ffmpeg_path.write_bytes(source.read())
            finally:
                archive.unlink(missing_ok=True)

        if not ffmpeg_path.is_file():
            raise RuntimeError("FFmpeg was downloaded, but ffmpeg.exe could not be installed.")

        write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", f"FFmpeg installed at: {ffmpeg_path}")
        return ffmpeg_path

    if system == "Linux":
        write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", "FFmpeg is not installed. Installing FFmpeg for you...")
        is_root: bool = hasattr(os, "geteuid") and os.geteuid() == 0
        apt_prefix: list[str] = [] if is_root else ["sudo"]

        try:
            run(apt_prefix + ["apt-get", "update"], check=True)
            run(apt_prefix + ["apt-get", "install", "-y", "ffmpeg"], check=True)

        except (CalledProcessError, FileNotFoundError) as error:
            raise RuntimeError(
                "FFmpeg is required to download YouTube videos, but StoreLimitless could not install it automatically.\n\n"
                "Please install it manually by running:\n"
                "sudo apt-get update && sudo apt-get install -y ffmpeg"
            ) from error

        ffmpeg_executable = shutil.which("ffmpeg")

        if ffmpeg_executable is None:
            raise RuntimeError("FFmpeg was installed, but could not be found in PATH.")

        return Path(ffmpeg_executable)

    raise RuntimeError("FFmpeg is required to download YouTube videos. Please install FFmpeg and make sure it is available in PATH.")


def _redis_is_responding(timeout: float = 0.2) -> bool:
    """Verify Redis with a RESP PING instead of trusting an open TCP port alone."""

    try:
        with create_connection(("127.0.0.1", 6379), timeout=timeout) as connection:
            connection.sendall(b"*1\r\n$4\r\nPING\r\n")

            with connection.makefile("rb") as response:
                return response.readline().strip() == b"+PONG"

    except OSError:
        return False


def start_redis_server() -> Popen[bytes] | None:
    """Use an existing local Redis server or launch a bundled/system installation."""

    if _redis_is_responding(timeout=0.1):
        return None

    system: str = platform.system()

    if system == "Windows":
        redis_dir: Path = Path.home() / "AppData" / "Local" / "StoreLimitless" / "redis"
        redis_path: Path = redis_dir / "redis-server.exe"
        system_redis: str | None = shutil.which("redis-server")

        if system_redis is not None:
            redis_path = Path(system_redis)

        elif not redis_path.is_file():
            write_log("INFO", Binaries, "START_REDIS_SERVER", "server", "Redis is not installed. Downloading and installing Redis for you...")
            redis_dir.mkdir(parents=True, exist_ok=True)
            archive: Path = redis_dir / "redis.zip"

            try:
                request.urlretrieve(
                    "https://github.com/redis-windows/redis-windows/releases/download/8.2.1/"
                    "Redis-8.2.1-Windows-x64-msys2.zip",
                    archive,
                )

                with ZipFile(archive) as zip_file:
                    redis_files: list[str] = [name for name in zip_file.namelist() if name.endswith("/redis-server.exe")]

                    if not redis_files:
                        raise RuntimeError("Redis was downloaded, but redis-server.exe could not be found.")

                    prefix: str = redis_files[0].rsplit("/", 1)[0] + "/"
                    root: Path = redis_dir.resolve()

                    for name in zip_file.namelist():
                        if not name.startswith(prefix) or name.endswith("/"):
                            continue

                        target: Path = (redis_dir / name[len(prefix):]).resolve()

                        if not target.is_relative_to(root):
                            raise RuntimeError("The Redis archive contains an unsafe file path.")

                        target.parent.mkdir(parents=True, exist_ok=True)

                        with zip_file.open(name) as source:
                            target.write_bytes(source.read())

            finally:
                archive.unlink(missing_ok=True)

    elif system == "Linux":
        redis_executable: str | None = shutil.which("redis-server")
        redis_path: Path = Path(redis_executable) if redis_executable else Path("/usr/bin/redis-server")

        if redis_executable is None or not redis_path.is_file():
            write_log(
                "INFO", Binaries, "START_REDIS_SERVER", "server",
                "Redis is not installed. Updating APT repositories and installing Redis for you...",
            )
            is_root: bool = hasattr(os, "geteuid") and os.geteuid() == 0
            apt_prefix: list[str] = [] if is_root else ["sudo"]

            try:
                run(apt_prefix + ["apt-get", "update"], check=True)
                run(apt_prefix + ["apt-get", "install", "-y", "redis-server"], check=True)

            except (CalledProcessError, FileNotFoundError) as error:
                raise RuntimeError(
                    "Redis is required, but StoreLimitless could not install it automatically.\n\n"
                    "Please install it manually by running:\n"
                    "sudo apt-get update && sudo apt-get install -y redis-server"
                ) from error

            redis_executable = shutil.which("redis-server")

            if redis_executable is None:
                raise FileNotFoundError("Redis installation finished, but redis-server could not be found in PATH.")

            redis_path = Path(redis_executable)

    else:
        raise RuntimeError(f"Redis automatic installation is not supported on {system}.")

    if not redis_path.is_file():
        raise FileNotFoundError(f"Redis executable not found: {redis_path}")

    redis_data_dir: Path = TRANSFER_PATH.parent / "redis"
    redis_data_dir.mkdir(parents=True, exist_ok=True)
    redis_process: Popen[bytes] = Popen([str(redis_path), "--bind", "127.0.0.1", "--port", "6379", "--dir", str(redis_data_dir)])
    deadline: float = monotonic() + 10

    while monotonic() < deadline:
        if redis_process.poll() is not None:
            raise RuntimeError(f"Redis exited during startup with code {redis_process.returncode}.")

        if _redis_is_responding(timeout=0.2):
            return redis_process

        sleep(0.2)

    redis_process.terminate()

    try:
        redis_process.wait(timeout=3)

    except TimeoutExpired:
        redis_process.kill()
        redis_process.wait()

    raise TimeoutError("Redis did not respond to PING on 127.0.0.1:6379 within 10 seconds.")


def start_cloudflare_tunnel(service_url: str, label: str) -> tuple[Popen[str], str]:
    """Start a Cloudflare Quick Tunnel and return its process and assigned public URL."""

    cloudflared: str | None = shutil.which("cloudflared")

    if cloudflared is None:
        system: str = platform.system()
        machine: str = platform.machine().lower()
        cloudflared_dir: Path = (
            Path.home() / "AppData" / "Local" / "StoreLimitless" / "cloudflared"
            if system == "Windows"
            else Path.home() / ".local" / "share" / "StoreLimitless" / "cloudflared"
        )
        executable: str = "cloudflared.exe" if system == "Windows" else "cloudflared"
        cloudflared_path: Path = cloudflared_dir / executable

        if system == "Windows":
            if machine not in ("amd64", "x86_64"):
                raise RuntimeError(f"Automatic Cloudflare installation is not supported for {system} {machine}.")

            download_url: str = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

        elif system == "Linux":
            architectures: dict[str, str] = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm"}
            architecture: str | None = architectures.get(machine)

            if architecture is None:
                raise RuntimeError(f"Automatic Cloudflare installation is not supported for {system} {machine}.")

            download_url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{architecture}"

        elif system == "Darwin":
            architectures = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}
            architecture = architectures.get(machine)

            if architecture is None:
                raise RuntimeError(f"Automatic Cloudflare installation is not supported for {system} {machine}.")

            download_url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-{architecture}"

        else:
            raise RuntimeError(f"Automatic Cloudflare installation is not supported on {system}.")

        if not cloudflared_path.is_file():
            write_log("INFO", Binaries, "START_CLOUDFLARE_TUNNEL", "server", "Cloudflare Tunnel is not installed. Downloading it for you...")
            cloudflared_dir.mkdir(parents=True, exist_ok=True)

            try:
                request.urlretrieve(download_url, cloudflared_path)

            except Exception as error:
                cloudflared_path.unlink(missing_ok=True)

                raise RuntimeError(
                    "Cloudflare Tunnel could not be downloaded automatically.\n\n"
                    "Please install it using:\n"
                    "https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/"
                ) from error

        if not cloudflared_path.is_file() or cloudflared_path.stat().st_size == 0:
            raise RuntimeError("Cloudflare Tunnel was downloaded, but the executable is missing or empty.")

        if system != "Windows":
            chmod(cloudflared_path, 0o755)

        cloudflared = str(cloudflared_path)
        write_log("INFO", Binaries, "START_CLOUDFLARE_TUNNEL", "server", f"Cloudflare Tunnel installed at: {cloudflared_path}")

    output: Queue[str] = Queue(maxsize=1)
    process: Popen[str] = Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--url", service_url],
        stdout=PIPE,
        stderr=STDOUT,
        text=True,
        bufsize=1,
    )

    def read_output() -> None:
        """Forward tunnel logs and capture the first generated public URL."""

        if process.stdout is None:
            return

        for line in process.stdout:
            write_log("INFO", Binaries, "START_CLOUDFLARE_TUNNEL", "server", f"[Cloudflare:{label}] {line.rstrip()}")
            match: Match[str] | None = search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)

            if match is not None:
                try:
                    output.put_nowait(match.group(0))

                except Full:
                    pass

    reader_thread: Thread = Thread(target=read_output, daemon=True)
    reader_thread.start()
    deadline = monotonic() + 60

    while True:
        try:
            public_url: str = output.get(timeout=min(0.5, max(0.01, deadline - monotonic())))
            return process, public_url

        except Empty:
            if process.poll() is not None:
                raise RuntimeError(
                    f"Cloudflare Tunnel for {label} exited before publishing a URL "
                    f"(exit code {process.returncode})."
                )

            if monotonic() >= deadline:
                if process.poll() is None:
                    process.terminate()

                    try:
                        process.wait(timeout=5)

                    except TimeoutExpired:
                        process.kill()
                        process.wait()

                raise TimeoutError(
                    f"Could not obtain the public Cloudflare URL for {label} within 60 seconds. "
                    "Check your internet connection and the Cloudflare Tunnel logs."
                )
