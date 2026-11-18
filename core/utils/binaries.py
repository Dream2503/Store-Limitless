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

from core.config import LOG_PATH, TRANSFER_PATH
from core.data_center import Binaries
from core.utils import write_log


def get_ffmpeg_path() -> Path:
    """Return FFmpeg's path, installing FFmpeg and FFprobe when either is missing."""

    ffmpeg_executable: str | None = shutil.which("ffmpeg")
    ffprobe_executable: str | None = shutil.which("ffprobe")

    if ffmpeg_executable is not None and ffprobe_executable is not None:
        return Path(ffmpeg_executable)

    system: str = platform.system()

    if system == "Windows":
        ffmpeg_dir: Path = Path.home() / "AppData" / "Local" / "StoreLimitless" / "ffmpeg"
        ffmpeg_path: Path = ffmpeg_dir / "ffmpeg.exe"
        ffprobe_path: Path = ffmpeg_dir / "ffprobe.exe"

        # Prefer a system installation only when both required tools are available.
        if ffmpeg_path.is_file() and ffprobe_path.is_file():
            write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", f"Using bundled FFmpeg and FFprobe from: {ffmpeg_dir}")
            return ffmpeg_path

        write_log(
            "INFO", Binaries, "GET_FFMPEG_PATH", "server",
            "FFmpeg or FFprobe is missing. Downloading and installing both tools...",
        )
        ffmpeg_dir.mkdir(parents=True, exist_ok=True)
        archive: Path = ffmpeg_dir / "ffmpeg.zip"

        try:
            request.urlretrieve("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", archive)

            with ZipFile(archive) as zip_file:
                ffmpeg_files: list[str] = [
                    name for name in zip_file.namelist() if name.endswith("/bin/ffmpeg.exe")
                ]
                ffprobe_files: list[str] = [
                    name for name in zip_file.namelist() if name.endswith("/bin/ffprobe.exe")
                ]

                if not ffmpeg_files or not ffprobe_files:
                    raise RuntimeError("The FFmpeg archive must contain both ffmpeg.exe and ffprobe.exe.")

                with zip_file.open(ffmpeg_files[0]) as source:
                    ffmpeg_path.write_bytes(source.read())

                with zip_file.open(ffprobe_files[0]) as source:
                    ffprobe_path.write_bytes(source.read())
        finally:
            archive.unlink(missing_ok=True)

        if not ffmpeg_path.is_file() or not ffprobe_path.is_file():
            raise RuntimeError(
                "FFmpeg installation did not provide both executables. "
                "Please install FFmpeg and FFprobe manually."
            )

        write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", f"FFmpeg installed at: {ffmpeg_path}")
        write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", f"FFprobe installed at: {ffprobe_path}")
        return ffmpeg_path

    if system == "Linux":
        write_log(
            "INFO", Binaries, "GET_FFMPEG_PATH", "server",
            "FFmpeg or FFprobe is missing. Installing the FFmpeg package, which provides both tools...",
        )
        is_root: bool = hasattr(os, "geteuid") and os.geteuid() == 0
        # Avoid hanging a server request on an interactive sudo password prompt.
        apt_prefix: list[str] = [] if is_root else ["sudo", "-n"]

        try:
            run(apt_prefix + ["apt-get", "update"], check=True, timeout=180)
            run(apt_prefix + ["apt-get", "install", "-y", "ffmpeg"], check=True, timeout=180)

        except (CalledProcessError, FileNotFoundError, TimeoutExpired) as error:
            raise RuntimeError(
                "FFmpeg and FFprobe are required for track-aware streaming, but StoreLimitless "
                "could not install them automatically. Please install them manually by running:\n"
                "sudo apt-get update && sudo apt-get install -y ffmpeg"
            ) from error

        ffmpeg_executable = shutil.which("ffmpeg")
        ffprobe_executable = shutil.which("ffprobe")

        if ffmpeg_executable is None or ffprobe_executable is None:
            raise RuntimeError(
                "The FFmpeg package was installed, but both ffmpeg and ffprobe must be available in PATH."
            )

        write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", f"FFmpeg available at: {ffmpeg_executable}")
        write_log("INFO", Binaries, "GET_FFMPEG_PATH", "server", f"FFprobe available at: {ffprobe_executable}")
        return Path(ffmpeg_executable)

    raise RuntimeError(
        f"Automatic FFmpeg and FFprobe installation is not supported on {system}. "
        "Please install both tools and make sure they are available in PATH."
    )

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
    # Redis writes persistence and server status reports directly to stdout/stderr.
    # Send both streams to the application's log file instead of the interactive console.
    with LOG_PATH.open("ab") as redis_log:
        redis_process: Popen[bytes] = Popen(
            [str(redis_path), "--bind", "127.0.0.1", "--port", "6379", "--dir", str(redis_data_dir)],
            stdout=redis_log,
            stderr=STDOUT,
        )

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

    protocol: str = os.environ.get("STORELIMITLESS_CLOUDFLARE_PROTOCOL", "http2").strip().lower()

    if protocol not in {"auto", "http2", "quic"}:
        raise ValueError("STORELIMITLESS_CLOUDFLARE_PROTOCOL must be one of: auto, http2, quic")

    write_log(
        "INFO",
        Binaries,
        "START_CLOUDFLARE_TUNNEL",
        "server",
        f"Using Cloudflare edge protocol: {protocol}",
    )
    output: Queue[str] = Queue(maxsize=1)
    process: Popen[str] = Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--protocol", protocol, "--url", service_url],
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
            normalized_line: str = line.lower()

            # Browser seeking, media-player retries, and closed client connections can
            # make cloudflared report these expected request cancellations as ERR logs.
            # Suppress only this known transport noise; keep forwarding unrelated errors.
            if (
                "http2: stream closed" in normalized_line
                or (
                    "context canceled" in normalized_line
                    and ("failed to serve incoming request" in normalized_line or "failed to proxy http" in normalized_line)
                )
            ):
                continue

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
