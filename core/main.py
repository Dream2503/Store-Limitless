import platform
import shutil
import socket
import subprocess
from asyncio import CancelledError, Task, create_task, gather, run
from pathlib import Path
from shutil import rmtree
from threading import Thread

from uvicorn import Config, Server

from backend.server.app import app
from core.config import REDIS_PATH, TRANSFER_PATH
from core.data_center import DataCenter
from core.utils import check_dependencies
from core.utils.discord_ import Discord
from core.utils.telegram_ import Telegram


def start_redis() -> subprocess.Popen | None:
    if platform.system() == "Windows":
        return None

    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=0.1):
            return None
    except OSError:
        pass

    from core.config import FROZEN

    redis_path = REDIS_PATH if FROZEN else Path(shutil.which("redis-server") or "")

    if not redis_path.is_file():
        raise FileNotFoundError(f"Redis executable not found: {redis_path}")

    redis_dir = TRANSFER_PATH.parent / "redis"
    redis_dir.mkdir(parents=True, exist_ok=True)

    return subprocess.Popen([
        str(redis_path),
        "--bind", "127.0.0.1",
        "--port", "6379",
        "--dir", str(redis_dir),
    ])


def wait_for_redis(timeout: float = 30.0) -> None:
    for _ in range(int(timeout * 10)):
        try:
            with socket.create_connection(("127.0.0.1", 6379), timeout=0.1):
                return
        except OSError:
            import time
            time.sleep(0.1)

    raise RuntimeError("Redis did not become available on 127.0.0.1:6379")


async def run_server() -> None:
    config: Config = Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server: Server = Server(config)
    await server.serve()


async def main() -> None:
    redis_process = start_redis()

    try:
        wait_for_redis()
        check_dependencies()
        await DataCenter.initialize_cache()
        await Telegram.main()

        discord_thread: Thread = Thread(target=Discord.main, daemon=True)
        server_task: Task[None] = create_task(run_server())
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
            except subprocess.TimeoutExpired:
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
