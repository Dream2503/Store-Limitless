import platform
import subprocess
from asyncio import CancelledError, Task, create_task, gather, run
from pathlib import Path
from shutil import rmtree, which
from socket import create_connection
from subprocess import Popen, CompletedProcess, DEVNULL, TimeoutExpired
from threading import Thread
from time import sleep

from uvicorn import Config, Server

from backend.server.app import app
from core.config import REDIS_PATH, TRANSFER_PATH
from core.data_center import DataCenter
from core.utils.discord_ import Discord
from core.utils.telegram_ import Telegram


def start_redis() -> Popen | None:
    from core.config import FROZEN

    try:
        with create_connection(("127.0.0.1", 6379), timeout=0.1):
            return None

    except OSError:
        pass

    if platform.system() == "Windows":
        memurai_msi: Path = Path(__file__).resolve().parent / "Memurai.msi"

        if not memurai_msi.is_file():
            memurai_msi: Path = Path(__file__).resolve().parent / "resources" / "Memurai.msi"

        if not memurai_msi.is_file():
            raise FileNotFoundError(f"Memurai installer not found: {memurai_msi}")

        result: CompletedProcess[bytes] = subprocess.run(["msiexec", "/i", str(memurai_msi), "/quiet", "/norestart"], check=False)

        if result.returncode not in (0, 3010):
            raise RuntimeError(f"Memurai installation failed with exit code: {result.returncode}")

        subprocess.run(["sc", "start", "Memurai"], stdout=DEVNULL, stderr=DEVNULL, check=False)
        return None

    redis_path: Path = REDIS_PATH if FROZEN else Path(which("redis-server") or "")

    if not redis_path.is_file():
        raise FileNotFoundError(f"Redis executable not found: {redis_path}")

    redis_dir: Path = TRANSFER_PATH.parent / "redis"
    redis_dir.mkdir(parents=True, exist_ok=True)
    return Popen([
        str(redis_path),
        "--bind", "127.0.0.1",
        "--port", "6379",
        "--dir", str(redis_dir),
    ])


def wait_for_redis(timeout: float = 30.0) -> None:
    for _ in range(int(timeout * 10)):
        try:
            with create_connection(("127.0.0.1", 6379), timeout=0.1):
                return

        except OSError:
            sleep(0.1)

    raise RuntimeError("Redis did not become available on 127.0.0.1:6379")


async def main() -> None:
    redis_process = start_redis()

    try:
        wait_for_redis()
        await DataCenter.initialize_cache()
        await Telegram.main()

        discord_thread: Thread = Thread(target=Discord.main, daemon=True)
        server_task: Task[None] = create_task(Server(Config(app, host="0.0.0.0", port=8000, log_level="warning")).serve())
        discord_thread.start()

        try:
            await server_task

        except CancelledError:
            pass

        finally:
            server_task.cancel()
            await gather(server_task, return_exceptions=True)
            await Telegram.exit()
            discord_thread.join(timeout=5)

    finally:
        if redis_process is not None and redis_process.poll() is None:
            redis_process.terminate()

            try:
                redis_process.wait(timeout=5)

            except TimeoutExpired:
                redis_process.kill()
                redis_process.wait()

        for path in TRANSFER_PATH.iterdir():
            if path.is_dir():
                rmtree(path)

            else:
                path.unlink()


if __name__ == "__main__":
    try:
        run(main())

    except KeyboardInterrupt:
        pass
