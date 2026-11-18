"""Redis-backed upload coordination helpers shared by the upload routes."""

from __future__ import annotations

from asyncio import Task, sleep
from pathlib import Path
from time import time
from typing import Any

from fastapi import HTTPException

from backend.database.models import File
from backend.database.redis_server import Redis
from core.transfer import file_upload
from core.utils.progress import Progress

BACKGROUND_UPLOAD_TASKS: set[Task[None]] = set()


def pending_upload_key(job_id: str) -> str:
    """Build the Redis key for an upload awaiting its first chunk."""

    return f"upload:pending:{job_id}"


def upload_session_key(job_id: str) -> str:
    """Build the Redis key for an active chunk upload session."""

    return f"upload:session:{job_id}"


def track_upload_task(task: Task[None]) -> None:
    """Keep a background task strongly referenced until it completes."""

    BACKGROUND_UPLOAD_TASKS.add(task)
    task.add_done_callback(BACKGROUND_UPLOAD_TASKS.discard)


async def wait_for_all_chunks(job_id: str, file: File) -> None:
    """Wait on Redis-backed contiguous-byte metadata rather than an in-memory Event."""

    while True:
        state: Any = await Redis.get_json(upload_session_key(job_id))

        if not isinstance(state, dict):
            raise TimeoutError("Upload session expired before all chunks arrived")

        contiguous: int = int(state.get("contiguous", 0))
        total: int = int(state.get("total", 0))
        file.size = contiguous

        if bool(state.get("received_chunk")) and contiguous >= total:
            return

        last_activity: float = float(state.get("last_activity", 0))
        if time() - last_activity > 120:
            raise TimeoutError("No data received from the browser")

        await sleep(0.25)


async def record_written_range(job_id: str, start: int, end: int) -> int:
    """Merge a completed chunk range atomically in Redis and return contiguous bytes."""

    session_key: str = upload_session_key(job_id)
    lock: Any = Redis.redis.lock(f"upload:lock:{job_id}", timeout=30, blocking_timeout=30)

    async with lock:
        state: Any = await Redis.get_json(session_key)

        if not isinstance(state, dict):
            raise HTTPException(status_code=404, detail="Upload session expired")

        ranges_raw: Any = state.get("ranges", [])
        ranges: list[list[int]] = [[int(item[0]), int(item[1])] for item in ranges_raw if isinstance(item, (list, tuple)) and len(item) == 2]

        if end > start:
            ranges.append([start, end])
            ranges.sort(key=lambda item: item[0])

        merged: list[list[int]] = []

        for range_start, range_end in ranges:
            if merged and range_start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], range_end)

            else:
                merged.append([range_start, range_end])

        contiguous: int = merged[0][1] if merged and merged[0][0] == 0 else 0
        state["ranges"] = merged
        state["contiguous"] = contiguous
        state["last_activity"] = time()
        state["received_chunk"] = True
        file_state: Any = state.get("file")

        if isinstance(file_state, dict):
            file_state["size"] = contiguous

        await Redis.set_json(session_key, state, ex=60 * 60)
        return contiguous


async def run_chunk_upload_job(job_id: str, file: File, file_path: Path, producer_task: Task[None]) -> None:
    """Stream received chunks to the data center and clean up Redis session state."""

    try:
        async for progress in file_upload(file, file_path, producer_task):
            await Redis.set(job_id, progress)

        if producer_task.done() and not producer_task.cancelled():
            producer_error: BaseException | None = producer_task.exception()

            if producer_error is not None:
                await Redis.set(job_id, Progress(file.name, f"Upload failed: {producer_error}", 0))

    except Exception as error:
        await Redis.set(job_id, Progress(file.name, f"Upload failed: {error}", 0))

    finally:
        await Redis.delete_key(upload_session_key(job_id))
        await Redis.delete_key(pending_upload_key(job_id))
        file_path.unlink(missing_ok=True)

        try:
            file_path.parent.rmdir()

        except OSError:
            pass
