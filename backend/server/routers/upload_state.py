"""Redis-backed upload coordination and account-scoped upload job registry."""

from __future__ import annotations

import re
from asyncio import Task, gather, sleep
from pathlib import Path
from time import time
from typing import Any, cast, Iterable
from urllib.parse import urlsplit

from fastapi import HTTPException

from backend.database.models import File
from backend.database.redis_server import Redis
from core.transfer import file_upload
from core.utils.progress import Progress

BACKGROUND_UPLOAD_TASKS: set[Task[None]] = set()
UPLOAD_JOB_TASKS: dict[str, Task[None]] = {}


def pending_upload_key(job_id: str) -> str:
    """Build the Redis key for an upload awaiting its first chunk."""

    return f"upload:pending:{job_id}"


def upload_session_key(job_id: str) -> str:
    """Build the Redis key for an active chunk upload session."""

    return f"upload:session:{job_id}"


def upload_job_key(job_id: str) -> str:
    """Build the Redis key containing ownership and display metadata for a job."""

    return f"upload:job:{job_id}"


def terminal_upload_job_key(job_id: str) -> str:
    """Build the short-lived status key retained after a completed job leaves the tray."""

    return f"upload:terminal:{job_id}"


def resolve_upload_job_status(message: str, metadata: dict[str, Any]) -> str:
    """Classify only actual terminal messages; intermediate torrent stages aren't completion."""

    lowered: str = message.lower()

    if bool(metadata.get("cancelled")) or lowered.startswith("upload cancelled"):
        return "cancelled"

    if lowered.startswith(("upload complete", "download and upload complete", "torrent download and sequential upload complete")):
        return "completed"

    if bool(metadata.get("failed")) or lowered.startswith("upload failed"):
        return "error"

    return "uploading"


def user_upload_jobs_key(username: str) -> str:
    """Build the Redis set key indexing upload jobs by account username."""

    return f"upload:user:{username}:jobs"


async def register_upload_job(job_id: str, *, username: str, name: str, kind: str, directory: str, data_center: str, total: int = 0,
                              batch_id: str = "") -> None:
    """Persist account ownership and display metadata so any signed-in device can find a job."""

    metadata: dict[str, Any] = {
        "job_id": job_id,
        "username": username,
        "name": name,
        "kind": kind,
        "directory": directory,
        "data_center": data_center,
        "total": max(0, int(total)),
        "batch_id": batch_id,
        "created_at": time(),
        "cancelled": False,
    }
    await Redis.set_json(upload_job_key(job_id), metadata)
    await Redis.redis.sadd(user_upload_jobs_key(username), job_id)

    if batch_id:
        batch = await get_upload_batch_metadata(batch_id)

        if batch and batch.get("username") == username:
            children = [str(value) for value in batch.get("child_job_ids", [])]

            if job_id not in children:
                children.append(job_id)

            batch["child_job_ids"] = children
            await Redis.set_json(upload_batch_key(batch_id), batch)

            if batch.get("cancelled"):
                metadata["cancelled"] = True
                metadata["cancelled_at"] = time()
                await Redis.set_json(upload_job_key(job_id), metadata)
                await Redis.set(job_id, Progress(name, "Upload cancelled", 0))
                await cleanup_upload_artifacts(job_id)


async def get_upload_job_metadata(job_id: str) -> dict[str, Any] | None:
    """Return persisted metadata for a job, if it still exists."""

    value: Any = await Redis.get_json(upload_job_key(job_id))
    return value if isinstance(value, dict) else None


async def list_user_upload_jobs(username: str) -> list[dict[str, Any]]:
    """Return this user's upload jobs with current Redis-backed progress values."""

    index_key: str = user_upload_jobs_key(username)
    raw_job_ids: set[str] = await Redis.redis.smembers(index_key)
    jobs: list[dict[str, Any]] = []

    for raw_job_id in raw_job_ids:
        job_id: str = str(raw_job_id)
        metadata: dict[str, Any] | None = await get_upload_job_metadata(job_id)

        if metadata is None or metadata.get("username") != username:
            await Redis.redis.srem(index_key, job_id)
            continue

        try:
            progress: Progress = cast(Progress, await Redis.get(job_id, "Progress"))

        except KeyError:
            await Redis.redis.srem(index_key, job_id)
            await Redis.delete_key(upload_job_key(job_id))
            continue

        message: str = str(progress.message or "")
        status: str = resolve_upload_job_status(message, metadata)
        progress_name: str = str(progress.name or "").strip()

        if progress_name in {"", "link", "update_name_from_backend"}:
            progress_name = str(metadata.get("name") or "Upload")

        total: int = max(int(progress.total or 0), int(metadata.get("total") or 0))
        transfer: int = max(0, int(progress.transfer or 0))
        jobs.append({
            "job_id": job_id,
            "name": progress_name,
            "kind": str(metadata.get("kind") or "link"),
            "directory": str(metadata.get("directory") or "/home"),
            "data_center": str(metadata.get("data_center") or ""),
            "status": status,
            "message": message or "Uploading",
            "transfer": transfer,
            "total": total,
            "created_at": int(float(metadata.get("created_at") or 0) * 1000),
        })

    jobs.sort(key=lambda item: item["created_at"], reverse=True)
    return jobs


def track_upload_task(task: Task[None], job_id: str | None = None) -> None:
    """Keep a background task referenced and optionally index it for account-scoped cancellation."""

    BACKGROUND_UPLOAD_TASKS.add(task)

    if job_id:
        UPLOAD_JOB_TASKS[job_id] = task

    def discard(done_task: Task[None]) -> None:
        BACKGROUND_UPLOAD_TASKS.discard(done_task)

        if job_id and UPLOAD_JOB_TASKS.get(job_id) is done_task:
            UPLOAD_JOB_TASKS.pop(job_id, None)

    task.add_done_callback(discard)


def cancel_upload_task(job_id: str) -> bool:
    """Cancel a tracked server-side import or chunk-processing task when it is running."""

    task: Task[None] | None = UPLOAD_JOB_TASKS.get(job_id)

    if task is None or task.done():
        return False

    task.cancel()
    return True


async def wait_for_all_chunks(job_id: str, file: File) -> None:
    """Wait on Redis-backed contiguous-byte metadata rather than an in-memory Event."""

    while True:
        job_metadata: dict[str, Any] | None = await get_upload_job_metadata(job_id)

        if job_metadata and bool(job_metadata.get("cancelled")):
            raise RuntimeError("Upload cancelled")

        if job_metadata and bool(job_metadata.get("failed")):
            raise RuntimeError("Upload failed: browser transfer stopped")

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


def upload_batch_key(batch_id: str) -> str:
    """Build the Redis key containing one multi-file upload task."""

    return f"upload:batch:{batch_id}"


def user_upload_batches_key(username: str) -> str:
    """Build the Redis set key indexing a user's upload batches."""

    return f"upload:user:{username}:batches"


async def register_upload_batch(batch_id: str, *, username: str, name: str, kind: str, directory: str, data_center: str,
                                items: list[dict[str, Any]]) -> None:
    """Persist the whole user-selected upload as one queued tray task."""

    metadata: dict[str, Any] = {
        "batch_id": batch_id,
        "username": username,
        "name": name[:255],
        "kind": kind,
        "directory": directory,
        "data_center": data_center,
        "items": [dict(item, status=str(item.get("status") or "queued"), message=str(item.get("message") or "Queued")) for item in items],
        "total_items": len(items),
        "completed_items": 0,
        "current_index": 0,
        "current_job_id": "",
        "child_job_ids": [],
        "current_name": "",
        "status": "queued",
        "message": "Queued",
        "cancelled": False,
        "created_at": time(),
    }
    await Redis.set_json(upload_batch_key(batch_id), metadata)
    await Redis.redis.sadd(user_upload_batches_key(username), batch_id)


async def get_upload_batch_metadata(batch_id: str) -> dict[str, Any] | None:
    """Return a stored batch task when present."""

    value: Any = await Redis.get_json(upload_batch_key(batch_id))
    return value if isinstance(value, dict) else None


async def update_upload_batch(batch_id: str, username: str, **updates: Any) -> dict[str, Any]:
    """Update owned batch metadata without ever reviving a cancelled task."""

    metadata = await get_upload_batch_metadata(batch_id)

    if metadata is None or metadata.get("username") != username:
        raise KeyError(batch_id)

    if metadata.get("cancelled") and updates.get("status") not in {None, "cancelled"}:
        return metadata

    metadata.update(updates)
    await Redis.set_json(upload_batch_key(batch_id), metadata)
    return metadata


async def reconcile_completed_link_batch(batch_id: str, username: str, child_job_id: str) -> bool:
    """Repair a link batch from the authoritative child-job result, even if the browser missed its final PATCH."""

    if not child_job_id:
        return False

    batch = await get_upload_batch_metadata(batch_id)
    if (
            batch is None
            or batch.get("username") != username
            or batch.get("kind") != "link"
            or batch.get("cancelled")
            or batch.get("status") in {"completed", "error", "cancelled"}
    ):
        return False

    child_metadata = await get_upload_job_metadata(child_job_id)
    if (
            child_metadata is None
            or child_metadata.get("username") != username
            or str(child_metadata.get("batch_id") or "") != batch_id
    ):
        return False

    try:
        progress: Progress = cast(Progress, await Redis.get(child_job_id, "Progress"))
    except KeyError:
        return False

    if resolve_upload_job_status(str(progress.message or ""), child_metadata) != "completed":
        return False

    raw_items = batch.get("items") if isinstance(batch.get("items"), list) else []
    items = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in raw_items]
    child_items = getattr(progress, "torrent_items", []) or []
    by_file_index = {
        int(item["file_index"]): item
        for item in child_items
        if isinstance(item, dict) and item.get("file_index") is not None
    }

    # A link child only returns a terminal-success progress after the entire direct-link,
    # YouTube, or selected-torrent import has finished. Therefore every selected batch item
    # can be reconciled to completed, even if a browser polling request was interrupted.
    for item in items:
        child_item = None
        if item.get("file_index") is not None:
            try:
                child_item = by_file_index.get(int(item["file_index"]))
            except (TypeError, ValueError):
                child_item = None

        size = max(
            int(item.get("size") or 0),
            int(child_item.get("total") or 0) if child_item else 0,
            int(child_item.get("transfer") or 0) if child_item else 0,
        )
        if size == 0:
            size = max(0, int(item.get("total") or 0), int(item.get("transfer") or 0))

        item["status"] = "completed"
        item["message"] = "Completed"
        if size > 0 or "transfer" in item or "total" in item:
            item["size"] = size
            item["transfer"] = size
            item["total"] = size

    total_items = len(items)
    await update_upload_batch(
        batch_id,
        username,
        status="completed",
        message="Upload completed successfully",
        items=items,
        total_items=total_items,
        completed_items=total_items,
    )
    return True


async def cleanup_upload_artifacts(job_id: str) -> None:
    """Remove temporary chunk files and Redis session state for an uncommitted upload."""

    paths: set[Path] = set()

    for key in (pending_upload_key(job_id), upload_session_key(job_id)):
        state: Any = await Redis.get_json(key)

        if isinstance(state, dict):
            raw_path = state.get("file_path")

            if isinstance(raw_path, str) and raw_path:
                paths.add(Path(raw_path))

        await Redis.delete_key(key)

    for path in paths:
        path.unlink(missing_ok=True)

        try:
            path.parent.rmdir()
            path.parent.parent.rmdir()

        except OSError:
            pass


async def cancel_upload_job_state(job_id: str, username: str) -> str:
    """Atomically cancel an owned job unless its database commit has already begun."""

    lock = Redis.redis.lock(f"upload:finalize-lock:{job_id}", timeout=60, blocking_timeout=60)

    async with lock:
        metadata = await get_upload_job_metadata(job_id)

        if metadata is None or metadata.get("username") != username:
            raise HTTPException(status_code=404, detail="Upload job not found")

        try:
            current: Progress = cast(Progress, await Redis.get(job_id, "Progress"))

        except KeyError:
            raise HTTPException(status_code=404, detail="Upload job not found")

        current_message = str(current.message or "")

        if metadata.get("finalizing") or "complete" in current_message.lower() or current_message.lower().startswith("upload failed"):
            raise HTTPException(status_code=409, detail="Upload is already finishing or has finished")

        if metadata.get("cancelled"):
            return "cancelled"

        metadata["cancelled"] = True
        metadata["cancelled_at"] = time()
        await Redis.set_json(upload_job_key(job_id), metadata)
        await Redis.set(job_id, Progress(str(metadata.get("name") or current.name), "Upload cancelled", 0))

    tracked_task = UPLOAD_JOB_TASKS.get(job_id)
    task_was_running = cancel_upload_task(job_id)

    if task_was_running and tracked_task is not None:
        await gather(tracked_task, return_exceptions=True)

    elif metadata.get("kind") == "file":
        await cleanup_upload_artifacts(job_id)

    return "cancelled"


async def list_user_upload_batches(username: str) -> list[dict[str, Any]]:
    """List batch tasks with aggregate progress and the current child stage."""

    index_key = user_upload_batches_key(username)
    raw_batch_ids: set[str] = await Redis.redis.smembers(index_key)
    batches: list[dict[str, Any]] = []

    for raw_batch_id in raw_batch_ids:
        batch_id = str(raw_batch_id)
        metadata = await get_upload_batch_metadata(batch_id)

        if metadata is None or metadata.get("username") != username:
            await Redis.redis.srem(index_key, batch_id)
            continue

        # Self-heal batches that stayed "uploading" because the browser never received the
        # child's terminal status or could not send its final batch PATCH.
        if metadata.get("kind") == "link" and metadata.get("status") in {"queued", "uploading"}:
            child_job_id = str(metadata.get("current_job_id") or "")
            if child_job_id:
                await reconcile_completed_link_batch(batch_id, username, child_job_id)
                metadata = await get_upload_batch_metadata(batch_id) or metadata

        total_items = max(1, int(metadata.get("total_items") or 0))
        completed_items = max(0, min(int(metadata.get("completed_items") or 0), total_items))
        current_transfer = 0
        current_total = 0
        current_name = str(metadata.get("current_name") or "").strip()
        current_message = str(metadata.get("message") or "")
        current_job_id = str(metadata.get("current_job_id") or "")
        items_raw = metadata.get("items") if isinstance(metadata.get("items"), list) else []
        items = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in cast(Iterable, items_raw)]
        current_index = int(metadata.get("current_index") or 0)
        source_name = str(items[current_index].get("name") or "") if 0 <= current_index < len(items) else ""
        current_name_is_url = current_name.lower().startswith(("http://", "https://"))

        if current_name_is_url:
            current_name = ""

        parsed_source = urlsplit(source_name) if source_name.lower().startswith(("http://", "https://")) else None
        source_host = (parsed_source.hostname or "").lower().removeprefix("www.") if parsed_source else ""
        is_youtube_source = source_host in {"youtube.com", "youtu.be"} or source_host.endswith(".youtube.com")
        source_path_name = (parsed_source.path.rstrip("/").rsplit("/", 1)[-1] if parsed_source else "")
        placeholder_names = {"", "link", "upload", "torrent", "watch", "shorts", "embed", "live", "video", "update_name_from_backend"}

        if current_job_id:
            try:
                child_progress: Progress = cast(Progress, await Redis.get(current_job_id, "Progress"))
                current_transfer = max(0, int(child_progress.transfer or 0))
                current_total = max(0, int(child_progress.total or 0))
                progress_name = str(child_progress.name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
                progress_name_lower = progress_name.casefold()
                progress_message = str(child_progress.message or "").strip()

                name_is_video_id = bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", progress_name))
                name_is_path_placeholder = is_youtube_source and (
                        progress_name == source_path_name or name_is_video_id
                )
                if (
                        progress_name and
                        progress_name_lower not in placeholder_names and
                        not progress_name.lower().startswith(("http://", "https://")) and
                        not name_is_path_placeholder
                ):
                    current_name = progress_name

                if progress_message:
                    if progress_message.casefold().startswith("upload complete of id="):
                        current_message = "Finalizing upload"

                    else:
                        current_message = progress_message

            except KeyError:
                pass

        items_changed = False

        if str(metadata.get("kind") or "") == "link" and current_name and 0 <= current_index < len(items):
            submitted_name = str(items[current_index].get("name") or "")

            if submitted_name != current_name and not current_name.lower().startswith(("http://", "https://")):
                items[current_index]["name"] = current_name
                items_changed = True

        if items_changed:
            metadata["items"] = items
            await Redis.set_json(upload_batch_key(batch_id), metadata)

        current_fraction = min(1.0, current_transfer / current_total) if current_total else 0.0
        status = str(metadata.get("status") or "queued")
        progress = 100.0 if status == "completed" else min(99.0, ((completed_items + current_fraction) / total_items) * 100.0)

        if metadata.get("cancelled"):
            status = "cancelled"

        batches.append({
            "batch_id": batch_id,
            "name": str(metadata.get("name") or "Upload task"),
            "kind": str(metadata.get("kind") or "file"),
            "directory": str(metadata.get("directory") or "/home"),
            "data_center": str(metadata.get("data_center") or ""),
            "items": items,
            "torrent_files": metadata.get("torrent_files", []) if isinstance(metadata.get("torrent_files", []), list) else [],
            "total_items": int(metadata.get("total_items") or 0),
            "completed_items": completed_items,
            "current_index": current_index,
            "current_job_id": current_job_id,
            "current_name": current_name,
            "status": status,
            "progress": progress,
            "transfer": current_transfer,
            "total": current_total,
            "message": current_message or status.title(),
            "created_at": int(float(metadata.get("created_at") or 0) * 1000),
        })

    batches.sort(key=lambda item: item["created_at"], reverse=True)
    return batches


async def run_chunk_upload_job(job_id: str, file: File, file_path: Path, producer_task: Task[None]) -> None:
    """Transfer received chunks and commit the database row only after a cancellation-safe final check."""

    try:
        async for progress in file_upload(file, file_path, producer_task, intermediate=True):
            latest_metadata = await get_upload_job_metadata(job_id)

            if latest_metadata and latest_metadata.get("cancelled"):
                raise RuntimeError("Upload cancelled")

            await Redis.set(job_id, progress)

        if producer_task.done() and not producer_task.cancelled():
            producer_error: BaseException | None = producer_task.exception()

            if producer_error is not None:
                latest_metadata = await get_upload_job_metadata(job_id)

                if not (latest_metadata and (latest_metadata.get("failed") or latest_metadata.get("cancelled"))):
                    await Redis.set(job_id, Progress(file.name, f"Upload failed: {producer_error}", 0))

                return

        lock = Redis.redis.lock(f"upload:finalize-lock:{job_id}", timeout=60, blocking_timeout=60)

        async with lock:
            latest_metadata = await get_upload_job_metadata(job_id)

            if latest_metadata is None or latest_metadata.get("cancelled"):
                await Redis.set(job_id, Progress(file.name, "Upload cancelled", 0))
                return

            if latest_metadata.get("failed"):
                return

            latest_metadata["finalizing"] = True
            await Redis.set_json(upload_job_key(job_id), latest_metadata)
            await file.save()
            complete = Progress(file.name, f"Upload Complete of id={file.id}", max(file.size, 0))
            complete.transfer = max(file.size, 0)
            await Redis.set(job_id, complete)

    except Exception as error:
        metadata = await get_upload_job_metadata(job_id)

        if not (metadata and (metadata.get("failed") or metadata.get("cancelled"))):
            await Redis.set(job_id, Progress(file.name, f"Upload failed: {error}", 0))

    finally:
        if not producer_task.done():
            producer_task.cancel()

        await gather(producer_task, return_exceptions=True)
        await cleanup_upload_artifacts(job_id)
