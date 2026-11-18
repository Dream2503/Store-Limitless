from __future__ import annotations

from asyncio import Task, create_task, wait, sleep
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncGenerator, TYPE_CHECKING

from core.config import LOG_HANDLER, TRANSFER_PATH, LOCK

if TYPE_CHECKING:
    from core.data_center import DataCenter
    from backend.database import File


def getenv(key: str) -> str:
    import os
    value: str | None = os.getenv(key)

    if value is None or not value.strip():
        raise RuntimeError(f"Environment variable '{key}' is missing or empty. Check your .env file or system environment.")

    return value


GOOGLE_API_KEY: str = getenv("GOOGLE_API_KEY")


def write_log(level: str, data_center: type[DataCenter] | DataCenter, func: str, user: str, message: str) -> None:
    with LOCK:
        LOG_HANDLER.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{data_center.NAME}] [{level}] [{func}] [{user}] {message}\n")
        LOG_HANDLER.flush()


def get_transfer_path(username: str, directory: str, filename: str) -> Path:
    path: Path = TRANSFER_PATH / username

    if directory:
        path = path / directory

    return path / filename


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

    progress.message = "Upload Complete"
    progress.total = total_size or size
    yield progress
    file.size = size
    file.links = [results[i] for i in sorted(results)]
