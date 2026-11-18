"""Upload progress tracking and bounded parallel transfer of growing files."""

from __future__ import annotations

from asyncio import Task, create_task, gather, sleep, to_thread, wait
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator

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
        # Some imports receive bytes while storing completed chunks at the same time.
        # These fields track that incoming/download side independently of upload progress.
        self.download_transfer: int = 0
        self.download_total: int = 0
        self.download_active: bool = False
        self.download_speed: float = 0.0
        self.upload_speed: float = 0.0
        self.download_eta: float | None = None
        self.upload_eta: float | None = None
        self.ingestion_type: str = ""
        self.file_counts: dict[str, int] = {}
        self.last_updated_at: float = 0.0
        self.torrent_items: list[dict[str, object]] = []

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
        producer: Task[None] | None = None,
        total_size: int = 0,
        start_part: int = 1,
        track_download: bool = False,
        download_total: int = 0,
        download_from_path: bool = False,
) -> AsyncGenerator[Progress, None]:
    """Upload completed chunks as a file grows, bounded by the selected provider's own parallelism."""

    chunk_size: int = int(data_center.MAX_SIZE)

    if chunk_size <= 0:
        raise ValueError(f"{data_center.__name__}.MAX_SIZE must be greater than zero")

    upload_tasks: set[Task[tuple[int, str]]] = set()
    results: dict[int, str] = {}
    part: int = start_part

    if track_download:
        progress.download_active = True
        progress.download_total = max(0, int(download_total))

    def update_download_progress() -> None:
        """Publish bytes received from the source independently of storage upload bytes."""

        if not track_download:
            return

        if download_from_path:
            try:
                received = path.stat().st_size
            except FileNotFoundError:
                received = 0
        else:
            # For browser uploads, wait_for_all_chunks updates file.size to the
            # contiguous byte count received from the browser.
            received = max(0, int(file.size or 0))

        progress.download_transfer = min(received, progress.download_total) if progress.download_total > 0 else received

    async def upload_part(index: int, size: int) -> tuple[int, str]:
        """Read exactly one chunk off the event loop and retry temporary provider I/O errors."""

        while True:
            try:
                offset: int = (index - start_part) * chunk_size
                chunk: bytes = await to_thread(read_chunk, path, offset, size)
                message_id: str = await data_center.upload(chunk, f"{file.name}.part{index:03d}", progress)

                if total_size:
                    total_parts: int = (total_size + chunk_size - 1) // chunk_size
                    write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {index}/{total_parts}")

                else:
                    write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {index}")

                return index, message_id

            except OSError as error:
                write_log("ERROR", data_center, "UPLOAD", file.username, f"Network error part {index}, retrying: {error}")
                await sleep(2)

    async def collect_done() -> None:
        """Record completed uploads without changing the order of the final chunk links."""

        nonlocal upload_tasks
        if not upload_tasks:
            return

        done, pending = await wait(upload_tasks, timeout=0)
        upload_tasks = set(pending)

        for task in done:
            index, message_id = task.result()
            results[index] = message_id

    def update_progress(current_size: int) -> None:
        """Keep the shared progress record aligned with known or discovered file size."""

        progress.total = total_size or current_size

    try:
        while producer is not None and not producer.done():
            while not path.exists() and not producer.done():
                await sleep(0.1)

            if producer.done():
                break

            size: int = path.stat().st_size

            while size >= (part - start_part + 1) * chunk_size and len(upload_tasks) < max(1, int(data_center.MAX_PARALLEL_UPLOADS)):
                upload_tasks.add(create_task(upload_part(part, chunk_size)))
                part += 1

            await collect_done()
            update_progress(size)
            update_download_progress()
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
        offset: int = (part - start_part) * chunk_size

        while size >= offset + chunk_size:
            while len(upload_tasks) >= max(1, int(data_center.MAX_PARALLEL_UPLOADS)):
                await collect_done()
                update_progress(size)
                update_download_progress()
                yield progress
                await sleep(0.1)

            upload_tasks.add(create_task(upload_part(part, chunk_size)))
            part += 1
            offset += chunk_size

        if size > offset:
            while len(upload_tasks) >= max(1, int(data_center.MAX_PARALLEL_UPLOADS)):
                await collect_done()
                update_progress(size)
                update_download_progress()
                yield progress
                await sleep(0.1)

            upload_tasks.add(create_task(upload_part(part, size - offset)))
            part += 1

        while upload_tasks:
            await collect_done()
            update_progress(size)
            update_download_progress()
            yield progress
            await sleep(0.1)

        update_progress(size)
        update_download_progress()
        if track_download and download_from_path and progress.download_total <= 0:
            progress.download_total = size
            progress.download_transfer = size
        yield progress
        file.size = size
        file.links = [results[index] for index in sorted(results)]

    finally:
        pending_tasks: list[Task[tuple[int, str]]] = list(upload_tasks)

        for task in pending_tasks:
            task.cancel()

        if pending_tasks:
            await gather(*pending_tasks, return_exceptions=True)
