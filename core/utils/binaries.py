import os
import platform
import shutil
from os import chmod
from pathlib import Path
from queue import Empty, Queue
from re import search
from socket import create_connection
from subprocess import PIPE, STDOUT, Popen, TimeoutExpired, CalledProcessError, run
from threading import Thread
from time import monotonic, sleep
from urllib import request
from zipfile import ZipFile

from core.config import TRANSFER_PATH
from core.data_center import Binaries
from core.utils import write_log


def get_ffmpeg_path():
    ffmpeg_path: str | None = shutil.which("ffmpeg")

    if ffmpeg_path is not None:
        return Path(ffmpeg_path)

    if platform.system() == "Windows":
        ffmpeg_dir: Path = Path.home() / "AppData" / "Local" / "StoreLimitless" / "ffmpeg"
        ffmpeg_path: Path = ffmpeg_dir / "ffmpeg.exe"

        if not ffmpeg_path.is_file():
            write_log('INFO', Binaries, 'GET_FFMPEG_PATH', "server", "FFmpeg is not installed. Downloading and installing FFmpeg for you...")
            ffmpeg_dir.mkdir(parents=True, exist_ok=True)
            archive: Path = ffmpeg_dir / "ffmpeg.zip"
            request.urlretrieve("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", archive)

            with ZipFile(archive) as zf:
                ffmpeg_files: list[str] = [name for name in zf.namelist() if name.endswith("/bin/ffmpeg.exe")]

                if not ffmpeg_files:
                    raise RuntimeError("FFmpeg was downloaded, but ffmpeg.exe could not be found.")

                with zf.open(ffmpeg_files[0]) as source:
                    ffmpeg_path.write_bytes(source.read())

            archive.unlink(missing_ok=True)

        if not ffmpeg_path.is_file():
            raise RuntimeError("FFmpeg was downloaded, but ffmpeg.exe could not be installed.")

        write_log('INFO', Binaries, 'GET_FFMPEG_PATH', "server", f"FFmpeg installed at: {ffmpeg_path}")
        return ffmpeg_path

    if platform.system() == "Linux":
        write_log('INFO', Binaries, 'GET_FFMPEG_PATH', "server", "FFmpeg is not installed. Installing FFmpeg for you...")

        try:
            run(["sudo", "apt", "update"], check=True)
            run(["sudo", "apt", "install", "-y", "ffmpeg"], check=True)

        except CalledProcessError as e:
            raise RuntimeError(
                "FFmpeg is required to download YouTube videos, but StoreLimitless could not install it automatically.\n\n"
                "Please install it manually by running:\n"
                "sudo apt update && sudo apt install ffmpeg"
            ) from e

        ffmpeg_path = shutil.which("ffmpeg")

        if ffmpeg_path is None:
            raise RuntimeError("FFmpeg was installed, but could not be found in PATH.")

        return Path(ffmpeg_path)

    raise RuntimeError("FFmpeg is required to download YouTube videos. Please install FFmpeg and make sure it is available in PATH.")


def start_redis_server():
    try:
        with create_connection(("127.0.0.1", 6379), timeout=0.1):
            return None

    except OSError:
        pass

    system: str = platform.system()

    if system == "Windows":
        redis_dir: Path = Path.home() / "AppData" / "Local" / "StoreLimitless" / "redis"
        redis_path: Path = redis_dir / "redis-server.exe"
        system_redis: str | None = shutil.which("redis-server")

        if system_redis is not None:
            redis_path = Path(system_redis)

        elif not redis_path.is_file():
            write_log('INFO', Binaries, 'START_REDIS_SERVER', "server", "Redis is not installed. Downloading and installing Redis for you...")
            redis_dir.mkdir(parents=True, exist_ok=True)
            archive: Path = redis_dir / "redis.zip"
            request.urlretrieve("https://github.com/redis-windows/redis-windows/releases/download/8.2.1/Redis-8.2.1-Windows-x64-msys2.zip", archive)

            with ZipFile(archive) as zf:
                redis_files: list[str] = [name for name in zf.namelist() if name.endswith("/redis-server.exe")]

                if not redis_files:
                    raise RuntimeError("Redis was downloaded, but redis-server.exe could not be found.")

                prefix: str = redis_files[0].rsplit("/", 1)[0] + "/"

                for name in zf.namelist():
                    if name.startswith(prefix) and not name.endswith("/"):
                        target: Path = redis_dir / name[len(prefix):]
                        target.parent.mkdir(parents=True, exist_ok=True)

                        with zf.open(name) as source:
                            target.write_bytes(source.read())

            archive.unlink(missing_ok=True)

    elif system == "Linux":
        redis_executable: str | None = shutil.which("redis-server")
        redis_path: Path = Path(redis_executable) if redis_executable else Path("/usr/bin/redis-server")

        if redis_executable is None or not redis_path.is_file():
            write_log('INFO', Binaries, 'START_REDIS_SERVER', "server",
                      "Redis is not installed. Updating APT repositories and installing Redis for you...")

            try:
                is_root: bool = hasattr(os, "geteuid") and os.geteuid() == 0
                apt_prefix: list[str] = [] if is_root else ["sudo"]

                run(apt_prefix + ["apt-get", "update"], check=True)
                run(apt_prefix + ["apt-get", "install", "-y", "redis-server"], check=True)

            except (CalledProcessError, FileNotFoundError) as e:
                raise RuntimeError(
                    "Redis is required, but StoreLimitless could not install it automatically.\n\n"
                    "Please install it manually by running:\n"
                    "sudo apt-get update && sudo apt-get install -y redis-server"
                ) from e

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
    redis_process: Popen = Popen([str(redis_path), "--bind", "127.0.0.1", "--port", "6379", "--dir", str(redis_data_dir)])

    deadline: float = monotonic() + 10

    while monotonic() < deadline:
        if redis_process.poll() is not None:
            raise RuntimeError(f"Redis exited during startup with code {redis_process.returncode}.")

        try:
            with create_connection(("127.0.0.1", 6379), timeout=0.2):
                return redis_process

        except OSError:
            sleep(0.2)

    redis_process.terminate()
    raise TimeoutError("Redis did not start listening on 127.0.0.1:6379 within 10 seconds.")


def start_cloudflare_tunnel(service_url: str, label: str) -> tuple[Popen, str]:
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
                raise RuntimeError(
                    f"Automatic Cloudflare installation is not supported for {system} {machine}."
                )

            download_url: str = (
                "https://github.com/cloudflare/cloudflared/releases/latest/download/"
                "cloudflared-windows-amd64.exe"
            )

        elif system == "Linux":
            architectures: dict[str, str] = {
                "x86_64": "amd64",
                "amd64": "amd64",
                "aarch64": "arm64",
                "arm64": "arm64",
                "armv7l": "arm",
            }

            architecture: str | None = architectures.get(machine)

            if architecture is None:
                raise RuntimeError(
                    f"Automatic Cloudflare installation is not supported for {system} {machine}."
                )

            download_url = (
                "https://github.com/cloudflare/cloudflared/releases/latest/download/"
                f"cloudflared-linux-{architecture}"
            )

        elif system == "Darwin":
            architectures = {
                "x86_64": "amd64",
                "amd64": "amd64",
                "arm64": "arm64",
                "aarch64": "arm64",
            }

            architecture = architectures.get(machine)

            if architecture is None:
                raise RuntimeError(
                    f"Automatic Cloudflare installation is not supported for {system} {machine}."
                )

            download_url = (
                "https://github.com/cloudflare/cloudflared/releases/latest/download/"
                f"cloudflared-darwin-{architecture}"
            )

        else:
            raise RuntimeError(
                f"Automatic Cloudflare installation is not supported on {system}."
            )

        if not cloudflared_path.is_file():
            write_log('INFO', Binaries, 'START_CLOUDFLARE_TUNNEL', "server", "Cloudflare Tunnel is not installed. Downloading it for you...")
            cloudflared_dir.mkdir(parents=True, exist_ok=True)

            try:
                request.urlretrieve(download_url, cloudflared_path)

            except Exception as e:
                cloudflared_path.unlink(missing_ok=True)

                raise RuntimeError(
                    "Cloudflare Tunnel could not be downloaded automatically.\n\n"
                    "Please install it using:\n"
                    "https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/"
                ) from e

        if not cloudflared_path.is_file() or cloudflared_path.stat().st_size == 0:
            raise RuntimeError(
                "Cloudflare Tunnel was downloaded, but the executable is missing or empty."
            )

        if system != "Windows":
            chmod(cloudflared_path, 0o755)

        cloudflared = str(cloudflared_path)
        write_log('INFO', Binaries, 'START_CLOUDFLARE_TUNNEL', "server", f"Cloudflare Tunnel installed at: {cloudflared_path}")

    output: Queue[str] = Queue(maxsize=1)

    process: Popen = Popen(
        [
            cloudflared,
            "tunnel",
            "--protocol",
            "http2",
            "--no-autoupdate",
            "--url",
            service_url,
        ],
        stdout=PIPE,
        stderr=STDOUT,
        text=True,
        bufsize=1,
    )

    def read_output() -> None:
        if process.stdout is None:
            return

        for line in process.stdout:
            write_log('INFO', Binaries, 'START_CLOUDFLARE_TUNNEL', "server", f"[Cloudflare:{label}] {line.rstrip()}")

            match = search(
                r"https://[a-zA-Z0-9-]+\.trycloudflare\.com",
                line,
            )

            if match:
                try:
                    output.put_nowait(match.group(0))
                except Exception:
                    pass

    Thread(target=read_output, daemon=True).start()

    try:
        public_url: str = output.get(timeout=60)

    except Empty as e:
        if process.poll() is None:
            process.terminate()

            try:
                process.wait(timeout=5)
            except TimeoutExpired:
                process.kill()
                process.wait()

        raise RuntimeError(
            f"Could not obtain the public Cloudflare URL for {label}. "
            "Check your internet connection and the Cloudflare Tunnel logs."
        ) from e

    return process, public_url
