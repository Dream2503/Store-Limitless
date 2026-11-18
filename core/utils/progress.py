"""Upload progress tracking and bounded parallel transfer of growing files."""

from __future__ import annotations

from asyncio import Task, create_task, gather, sleep, to_thread, wait
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator

from core.utils import write_log, read_chunk

if TYPE_CHECKING:
    from core.data_center import DataCenter
    from backend.database.models.file import File


class Progress:
    """Mutable progress record shared between transfer workers and API status polling."""

    def __init__(self, name: str, message: str, total: int) -> None:
        """Initialize progress state for a file or an individual transfer phase."""

        self.name: str = name
        self.message: str = message
        self.transfer: int = 0
        self.total: int = total

    @property
    def value(self) -> tuple[str, str, int, int]:
        """Return the progress fields as an immutable tuple."""

        return self.name, self.message, self.transfer, self.total


class ProgressStream(BytesIO):
    """In-memory byte stream that counts bytes read by a storage provider."""

    def __init__(self, data: bytes, progress: Progress) -> None:
        """Wrap bytes and attach the progress record updated by subsequent reads."""

        super().__init__(data)
        self.progress: Progress = progress

    def read(self, size: int | None = -1) -> bytes:
        """Read bytes and add the returned byte count to the shared progress record."""

        chunk: bytes = super().read(size)
        self.progress.transfer += len(chunk)
        return chunk


async def upload_growing_file(
        file: File,
        path: Path,
        data_center: type[DataCenter],
        progress: Progress,
        producer: Task[Any] | None = None,
        total_size: int = 0,
        start_part: int = 1,
) -> AsyncGenerator[Progress, None]:
    """Upload full chunks as they appear, then upload the final partial chunk.

    A maximum of eight provider uploads are active at once. ``producer`` may be used by downloaders that are still writing to ``path``; when it
    completes, this function waits for it before uploading the remaining bytes.
    """

    upload_tasks: set[Task[tuple[int, str]]] = set()
    results: dict[int, str] = {}
    part: int = start_part
    max_tasks: int = 2

    async def upload_part(index: int, size: int) -> tuple[int, str]:
        """Read one part off the event loop and retry provider I/O errors."""

        while True:
            try:
                offset: int = (index - start_part) * data_center.MAX_SIZE
                chunk: bytes = await to_thread(read_chunk, path, offset, size)
                message_id: str = await data_center.upload(chunk, f"{file.name}.part{index:03d}", progress)

                if total_size:
                    total_parts: int = (total_size + data_center.MAX_SIZE - 1) // data_center.MAX_SIZE
                    write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {index}/{total_parts}")

                else:
                    write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {index}")

                return index, message_id

            except OSError as error:
                write_log("ERROR", data_center, "UPLOAD", file.username, f"Network error part {index}, retrying: {error}")
                await sleep(2)

    async def collect_done() -> None:
        """Move completed upload-task results into the ordered message map."""

        nonlocal upload_tasks

        if not upload_tasks:
            return

        done, pending = await wait(upload_tasks, timeout=0)
        upload_tasks = set(pending)

        for task in done:
            index, message_id = task.result()
            results[index] = message_id

    try:
        while producer is not None and not producer.done():
            while not path.exists() and not producer.done():
                await sleep(0.1)

            if producer.done():
                break

            size: int = path.stat().st_size

            while size >= (part - start_part + 1) * data_center.MAX_SIZE and len(upload_tasks) < max_tasks:
                upload_tasks.add(create_task(upload_part(part, data_center.MAX_SIZE)))
                part += 1

            await collect_done()
            progress.total = total_size or size
            yield progress
            await sleep(0.1)

        if producer is not None:
            await producer

        try:
            size = path.stat().st_size

        except FileNotFoundError:
            candidates: list[Path] = [candidate for candidate in path.parent.iterdir() if candidate.is_file()]

            if not candidates:
                raise FileNotFoundError(f"Upload source file does not exist: {path}")

            path = candidates[0]
            size = path.stat().st_size

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
        file.links = [results[index] for index in sorted(results)]

    finally:
        pending_tasks: list[Task[tuple[int, str]]] = list(upload_tasks)

        for task in pending_tasks:
            task.cancel()

        if pending_tasks:
            await gather(*pending_tasks, return_exceptions=True)
