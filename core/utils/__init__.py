from __future__ import annotations

import platform
import shutil
from asyncio import Task, create_task, wait, sleep
from datetime import datetime
from io import BytesIO
from pathlib import Path
from socket import create_connection
from typing import Any, AsyncGenerator, TYPE_CHECKING

from redis.asyncio import Redis

if TYPE_CHECKING:
    from core.data_center import DataCenter
    from backend.database.models.file import File


def check_dependencies() -> None:
    missing: list[str] = []

    if shutil.which("ffmpeg") is None:
        missing.append("FFmpeg")

    if shutil.which("redis-server") is None:
        missing.append("Redis")

    if missing:
        system = platform.system()
        instructions: list[str] = []

        if system == "Windows":
            if "FFmpeg" in missing:
                instructions.append(
                    "FFmpeg:\n"
                    "  1. Open PowerShell or Command Prompt.\n"
                    "  2. Run: winget install Gyan.FFmpeg\n"
                    "  3. Close and reopen your terminal so PATH is refreshed."
                )

            if "Redis" in missing:
                instructions.append(
                    "Redis:\n"
                    "  1. Open PowerShell or Command Prompt as Administrator.\n"
                    "  2. Run: winget install Memurai.Memurai\n"
                    "  3. Memurai is installed as a Windows service and should start automatically.\n"
                    "  4. If it does not start automatically, open the Memurai service from Windows Services and start it."
                )

        elif system == "Linux":
            if "FFmpeg" in missing:
                instructions.append(
                    "FFmpeg:\n"
                    "  1. Open a terminal.\n"
                    "  2. Run: sudo apt update\n"
                    "  3. Run: sudo apt install ffmpeg"
                )

            if "Redis" in missing:
                instructions.append(
                    "Redis:\n"
                    "  1. Open a terminal.\n"
                    "  2. Run: sudo apt update\n"
                    "  3. Run: sudo apt install redis-server\n"
                    "  4. Start Redis with: sudo systemctl enable --now redis-server"
                )

        else:
            instructions.append(
                "Your operating system is not supported automatically.\n"
                "Install FFmpeg and Redis manually, then make sure both "
                "commands are available in PATH."
            )

        raise RuntimeError(
            "\n"
            "╔══════════════════════════════════════════════════════════╗\n"
            "║             Missing Required Dependencies                ║\n"
            "╚══════════════════════════════════════════════════════════╝\n\n"
            f"The application cannot start because the following dependencies are missing:\n\n"
            f"  • {', '.join(missing)}\n\n"
            "Follow the instructions below, then run the application again.\n\n"
            + "\n\n".join(instructions)
            + "\n\n"
              "After installation, simply restart the application.\n"
        )

    try:
        with create_connection(("127.0.0.1", 6379), timeout=1):
            pass

    except OSError as error:
        system = platform.system()

        if system == "Windows":
            instructions = (
                "Redis is installed, but the Redis server is not running.\n\n"
                "Try the following:\n"
                "  1. Open Windows Services.\n"
                "  2. Find the Memurai service.\n"
                "  3. Right-click it and select Start.\n\n"
                "Then run the application again."
            )
        elif system == "Linux":
            instructions = (
                "Redis is installed, but the Redis server is not running.\n\n"
                "Run:\n"
                "  sudo systemctl enable --now redis-server\n\n"
                "Then run the application again."
            )
        else:
            instructions = (
                "Redis is installed, but the Redis server is not running on localhost:6379.\n\n"
                "Start the Redis server and then run the application again."
            )

        raise RuntimeError(instructions) from error


def getenv(key: str) -> str:
    import os
    value: str | None = os.getenv(key)

    if value is None or not value.strip():
        raise RuntimeError(f"Environment variable '{key}' is missing or empty. Check your .env file or system environment.")

    return value


def write_log(level: str, data_center: type[DataCenter] | DataCenter, func: str, user: str, message: str) -> None:
    from core.config import LOG_HANDLER, LOCK

    with LOCK:
        LOG_HANDLER.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{data_center.NAME}] [{level}] [{func}] [{user}] {message}\n")
        LOG_HANDLER.flush()


class Jobs:
    def __init__(self, redis: Redis):
        self.redis: Redis = redis

    async def exists(self, job_id: str) -> bool:
        return bool(await self.redis.exists(job_id))

    async def get(self, job_id: str) -> Progress:
        data: dict[str, str] = await self.redis.hgetall(job_id)

        if not data:
            raise KeyError(job_id)

        progress = Progress(
            data["message"],
            int(data["total"]),
        )
        progress.transfer = int(data["transfer"])
        return progress

    async def set(self, job_id: str, progress: Progress) -> None:
        await self.redis.hset(job_id, mapping={
            "message": progress.message,
            "transfer": progress.transfer,
            "total": progress.total,
        })


class Progress:
    def __init__(self, message: str, total: int):
        self.message: str = message
        self.transfer: int = 0
        self.total: int = total

    @property
    def value(self) -> tuple[str, int, int]:
        return self.message, self.transfer, self.total


class ProgressStream(BytesIO):
    def __init__(self, data: bytes, progress: Progress):
        super().__init__(data)
        self.progress = progress

    def read(self, size: int | None = -1) -> bytes:
        chunk: bytes = super().read(size)
        self.progress.transfer += len(chunk)
        return chunk


async def upload_growing_file(file: File,
                              path: Path,
                              data_center: DataCenter,
                              progress: Progress,
                              producer: Task[Any] | None = None,
                              total_size: int = 0,
                              start_part: int = 1) -> AsyncGenerator[Progress, None]:
    upload_tasks: set[Task[tuple[int, str]]] = set()
    results: dict[int, str] = {}
    part: int = start_part
    uploaded_parts: int = 0
    max_tasks: int = 8

    async def upload_part(i: int, size: int) -> tuple[int, str]:
        while True:
            try:
                with path.open("rb") as buffer:
                    buffer.seek((i - start_part) * data_center.MAX_SIZE)
                    chunk: bytes = buffer.read(size)

                msg_id: str = await data_center.upload(chunk, f"{file.name}.part{i:03d}", progress)

                if total_size:
                    total_parts: int = (total_size + data_center.MAX_SIZE - 1) // data_center.MAX_SIZE
                    write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {i}/{total_parts}")

                else:
                    write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {i}")

                return i, msg_id

            except OSError as e:
                write_log("ERROR", data_center, "UPLOAD", file.username, f"Network error part {i}, retrying: {e}")
                await sleep(2)

    async def collect_done() -> None:
        nonlocal upload_tasks, uploaded_parts

        if not upload_tasks:
            return

        done, upload_tasks = await wait(upload_tasks, timeout=0)

        for task in done:
            i, msg_id = task.result()
            results[i] = msg_id
            uploaded_parts += 1

    while producer and not producer.done():
        while not path.exists():
            await sleep(0.1)

        size: int = path.stat().st_size

        while size >= (part - start_part + 1) * data_center.MAX_SIZE and len(upload_tasks) < max_tasks:
            upload_tasks.add(create_task(upload_part(part, data_center.MAX_SIZE)))
            part += 1

        await collect_done()

        progress.total = total_size or size
        yield progress
        await sleep(0.1)

    if producer:
        await producer

    try:
        size: int = path.stat().st_size

    except FileNotFoundError:
        path = next(p for p in path.parent.iterdir() if p.is_file())
        size: int = path.stat().st_size

    total_size = size
    offset: int = (part - start_part) * data_center.MAX_SIZE

    while size >= offset + data_center.MAX_SIZE:
        while len(upload_tasks) >= max_tasks:
            await collect_done()

            progress.total = total_size or size
            yield progress
            await sleep(0.1)

        upload_tasks.add(create_task(upload_part(part, data_center.MAX_SIZE)))
        part += 1
        offset += data_center.MAX_SIZE

        progress.total = total_size or size
        yield progress
        await sleep(0.1)

    if size > offset:
        while len(upload_tasks) >= max_tasks:
            await collect_done()

            progress.total = total_size or size
            yield progress
            await sleep(0.1)

        upload_tasks.add(create_task(upload_part(part, size - offset)))
        part += 1

    while upload_tasks:
        await collect_done()

        progress.total = total_size or size
        yield progress
        await sleep(0.1)

    progress.total = total_size or size
    yield progress
    file.size = size
    file.links = [results[i] for i in sorted(results)]
