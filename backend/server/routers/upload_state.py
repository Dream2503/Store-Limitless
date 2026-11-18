"""Redis-backed upload coordination and account-scoped upload job registry."""

from __future__ import annotations

import re
from json import dumps, loads
from asyncio import CancelledError, Task, gather, sleep
from pathlib import Path
from time import time
from collections.abc import Iterable
from contextlib import aclosing
from typing import cast
from urllib.parse import urlsplit

from fastapi import HTTPException

from backend.database.models import File
from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import sanitize_sensitive_urls, write_log
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


def upload_activity_key(job_id: str) -> str:
    """Build the Redis key tracking actual body activity for an in-flight chunk request."""

    return f"upload:activity:{job_id}"


async def touch_upload_activity(job_id: str, timestamp: float | None = None) -> None:
    """Refresh the upload idle-timeout heartbeat without rewriting concurrent range metadata."""

    await Redis.redis.set(
        upload_activity_key(job_id),
        str(time() if timestamp is None else timestamp),
        ex=60 * 60,
    )


def upload_chunk_progress_key(job_id: str) -> str:
    """Build the Redis hash for display-only bytes currently arriving in each chunk."""

    return f"upload:chunk-progress:{job_id}"


def upload_job_key(job_id: str) -> str:
    """Build the Redis key containing ownership and display metadata for a job."""

    return f"upload:job:{job_id}"


def terminal_upload_job_key(job_id: str) -> str:
    """Build the short-lived status key retained after a completed job leaves the tray."""

    return f"upload:terminal:{job_id}"


def resolve_upload_job_status(message: str, metadata: dict[str, object]) -> str:
    """Classify only actual terminal messages; intermediate torrent stages aren't completion."""

    lowered: str = message.lower()

    if bool(metadata.get("cancelled")) or lowered.startswith("upload cancelled"):
        return "cancelled"

    if lowered.startswith((
        "upload complete",
        "download and upload complete",
        "torrent download and sequential upload complete",
        "google drive folder import complete",
        "storelimitless copy complete",
    )):
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

    metadata: dict[str, object] = {
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
        batch_lock = Redis.redis.lock(f"upload:batch-lock:{batch_id}", timeout=60, blocking_timeout=30)
        async with batch_lock:
            batch = await get_upload_batch_metadata(batch_id)

            if batch and batch.get("username") == username:
                children = [str(value) for value in batch.get("child_job_ids", [])]

                if job_id not in children:
                    children.append(job_id)

                if kind == "file":
                    try:
                        metadata["batch_item_index"] = max(0, int(batch.get("current_index") or 0))
                    except (TypeError, ValueError):
                        metadata["batch_item_index"] = max(0, len(children) - 1)
                    await Redis.set_json(upload_job_key(job_id), metadata)

                batch["child_job_ids"] = children
                await Redis.set_json(upload_batch_key(batch_id), batch)

                if batch.get("cancelled"):
                    metadata["cancelled"] = True
                    metadata["cancelled_at"] = time()
                    await Redis.set_json(upload_job_key(job_id), metadata)
                    await Redis.set(job_id, Progress(name, "Upload cancelled", 0))
                    await cleanup_upload_artifacts(job_id)

    write_log(
        "INFO",
        Database,
        "UPLOAD STATE",
        username,
        f"Registered job_id={job_id} kind={kind} data_center={data_center} "
        f"name={name!r} expected_bytes={max(0, int(total))} batch_id={batch_id or '-'}",
    )


async def get_upload_job_metadata(job_id: str) -> dict[str, object] | None:
    """Return persisted metadata for a job, if it still exists."""

    value: object | None = await Redis.get_json(upload_job_key(job_id))
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


async def list_user_upload_jobs(username: str) -> list[dict[str, object]]:
    """Return this user's upload jobs with current Redis-backed progress values."""

    index_key: str = user_upload_jobs_key(username)
    raw_job_ids: set[str] = await Redis.redis.smembers(index_key)
    jobs: list[dict[str, object]] = []

    for raw_job_id in raw_job_ids:
        job_id: str = str(raw_job_id)
        metadata: dict[str, object] | None = await get_upload_job_metadata(job_id)

        if metadata is None or metadata.get("username") != username:
            await Redis.redis.srem(index_key, job_id)
            continue

        try:
            progress: Progress = cast("Progress", await Redis.get(job_id, "Progress"))

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
            "status": "paused" if metadata.get("paused") else status,
            "paused": bool(metadata.get("paused")),
            "message": "Paused by you" if metadata.get("paused") else (message or "Uploading"),
            "transfer": transfer,
            "total": total,
            "download_transfer": max(0, int(getattr(progress, "download_transfer", 0) or 0)),
            "download_total": max(0, int(getattr(progress, "download_total", 0) or 0)),
            "download_active": bool(getattr(progress, "download_active", False)),
            "download_speed": max(0.0, float(getattr(progress, "download_speed", 0) or 0)),
            "upload_speed": max(0.0, float(getattr(progress, "upload_speed", 0) or 0)),
            "download_eta": getattr(progress, "download_eta", None),
            "upload_eta": getattr(progress, "upload_eta", None),
            "ingestion_type": str(getattr(progress, "ingestion_type", "") or ""),
            "file_counts": getattr(progress, "file_counts", {}) or {},
            "last_updated_at": max(0.0, float(getattr(progress, "last_updated_at", 0) or 0)),
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
    """Wait for browser chunks, allowing an explicit pause to last indefinitely."""

    session_key = upload_session_key(job_id)

    while True:
        job_metadata: dict[str, object] | None = await get_upload_job_metadata(job_id)

        if job_metadata and bool(job_metadata.get("cancelled")):
            raise RuntimeError("Upload cancelled")

        if job_metadata and bool(job_metadata.get("failed")):
            raise RuntimeError("Upload failed: browser transfer stopped")

        # An explicit user pause is not an idle upload. Wait without a duration
        # limit, keep the one-hour Redis session TTL alive, and watch for Resume
        # or Cancel. The 120-second idle watchdog below applies only while active.
        if job_metadata and bool(job_metadata.get("paused")):
            while job_metadata and bool(job_metadata.get("paused")):
                if bool(job_metadata.get("cancelled")):
                    raise RuntimeError("Upload cancelled")
                if bool(job_metadata.get("failed")):
                    raise RuntimeError("Upload failed: browser transfer stopped")

                paused_state: object | None = await Redis.get_json(session_key)
                if not isinstance(paused_state, dict):
                    raise TimeoutError("Upload session expired while paused")

                # A paused transfer may remain untouched for hours or days.
                # Refresh its session/activity TTL so Resume can continue using
                # the same staged file and already-received byte ranges.
                await Redis.set_json(session_key, paused_state, ex=60 * 60)
                await touch_upload_activity(job_id)
                await sleep(1.0)
                job_metadata = await get_upload_job_metadata(job_id)

            if job_metadata and bool(job_metadata.get("cancelled")):
                raise RuntimeError("Upload cancelled")

            if job_metadata and bool(job_metadata.get("failed")):
                raise RuntimeError("Upload failed: browser transfer stopped")

            # Exclude the entire paused interval from the active idle timer.
            # Otherwise a pause longer than 120 seconds would fail immediately
            # on Resume despite being an intentional, healthy state.
            resumed_state: object | None = await Redis.get_json(session_key)
            if not isinstance(resumed_state, dict):
                raise TimeoutError("Upload session expired while paused")

            resumed_at = time()
            resumed_state["last_activity"] = resumed_at
            await Redis.set_json(session_key, resumed_state, ex=60 * 60)
            await touch_upload_activity(job_id, resumed_at)
            continue

        state: object | None = await Redis.get_json(session_key)

        if not isinstance(state, dict):
            raise TimeoutError("Upload session expired before all chunks arrived")

        contiguous: int = int(state.get("contiguous", 0))
        total: int = int(state.get("total", 0))
        file.size = contiguous

        if bool(state.get("received_chunk")) and contiguous >= total:
            return

        last_activity: float = float(state.get("last_activity", 0))
        raw_activity = await Redis.redis.get(upload_activity_key(job_id))
        if raw_activity is not None:
            try:
                last_activity = max(last_activity, float(raw_activity))
            except (TypeError, ValueError):
                pass

        if time() - last_activity > 120:
            # Recheck the pause/cancel state at the timeout boundary. This avoids
            # racing a Pause request that arrived just after the first metadata read.
            latest_metadata = await get_upload_job_metadata(job_id)
            if latest_metadata and bool(latest_metadata.get("cancelled")):
                raise RuntimeError("Upload cancelled")
            if latest_metadata and bool(latest_metadata.get("failed")):
                raise RuntimeError("Upload failed: browser transfer stopped")
            if latest_metadata and bool(latest_metadata.get("paused")):
                continue

            latest_activity = await Redis.redis.get(upload_activity_key(job_id))
            if latest_activity is not None:
                try:
                    if time() - float(latest_activity) <= 120:
                        continue
                except (TypeError, ValueError):
                    pass

            raise TimeoutError("No browser upload data received for 120 seconds")

        await sleep(0.25)


async def record_written_range(job_id: str, start: int, end: int) -> int:
    """Merge a completed chunk range atomically in Redis and return contiguous bytes."""

    session_key: str = upload_session_key(job_id)
    lock: object = Redis.redis.lock(f"upload:lock:{job_id}", timeout=30, blocking_timeout=30)

    async with lock:
        state: object | None = await Redis.get_json(session_key)

        if not isinstance(state, dict):
            raise HTTPException(status_code=404, detail="Upload session expired")

        ranges_raw: Iterable[object] = cast("Iterable[object]", state.get("ranges", []))
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
        file_state: object = state.get("file")

        if isinstance(file_state, dict):
            file_state["size"] = contiguous

        await Redis.set_json(session_key, state, ex=60 * 60)
        # Once this range is committed, the session range list becomes its source
        # of truth. Remove the temporary in-flight marker to keep Redis state small.
        await Redis.redis.hdel(upload_chunk_progress_key(job_id), str(start))
        await touch_upload_activity(job_id, state["last_activity"] if isinstance(state.get("last_activity"), (int, float)) else None)
        return contiguous


def upload_batch_key(batch_id: str) -> str:
    """Build the Redis key containing one multi-file upload task."""

    return f"upload:batch:{batch_id}"


def user_upload_batches_key(username: str) -> str:
    """Build the Redis set key indexing a user's upload batches."""

    return f"upload:user:{username}:batches"


def upload_batch_progress_key(batch_id: str) -> str:
    """Build the Redis hash key holding sparse progress for active batch items."""

    return f"upload:batch-progress:{batch_id}"


async def register_upload_batch(batch_id: str, *, username: str, name: str, kind: str, directory: str, data_center: str,
                                items: list[dict[str, object]]) -> None:
    """Persist the whole user-selected upload as one queued tray task."""

    metadata: dict[str, object] = {
        "batch_id": batch_id,
        "username": username,
        "name": name[:255],
        "kind": kind,
        "directory": directory,
        "directories": sorted({
            str(item.get("directory") or directory)
            for item in items
            if str(item.get("directory") or directory)
        } | {directory}),
        "data_center": data_center,
        "appendable": kind == "file",
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
        "paused": False,
        "created_at": time(),
    }
    await Redis.set_json(upload_batch_key(batch_id), metadata)
    await Redis.redis.sadd(user_upload_batches_key(username), batch_id)


async def get_upload_batch_metadata(batch_id: str) -> dict[str, object] | None:
    """Return a stored batch task when present."""

    value: object | None = await Redis.get_json(upload_batch_key(batch_id))
    return value if isinstance(value, dict) else None


async def update_upload_batch_item_progress(
        batch_id: str,
        username: str,
        item_index: int,
        *,
        transfer: int,
        total: int,
        progress: float,
        message: str,
) -> dict[str, object]:
    """Persist only the active item's changing progress, without rewriting the full batch metadata."""

    lock = Redis.redis.lock(f"upload:batch-lock:{batch_id}", timeout=60, blocking_timeout=30)

    async with lock:
        metadata = await get_upload_batch_metadata(batch_id)

        if metadata is None or metadata.get("username") != username:
            raise KeyError(batch_id)

        if (
                metadata.get("cancelled")
                or metadata.get("paused")
                or metadata.get("status") in {"completed", "error", "cancelled"}
                or int(metadata.get("current_index") or 0) != item_index
        ):
            return metadata

        items = metadata.get("items")
        if not isinstance(items, list) or not 0 <= item_index < len(items):
            return metadata

        item = items[item_index]
        if not isinstance(item, dict) or item.get("status") not in {"uploading", "downloading", "waiting_upload"}:
            return metadata

        progress_state = {
            "transfer": max(0, int(transfer)),
            "total": max(0, int(total)),
            "progress": max(0.0, min(100.0, float(progress))),
            "message": str(message or "Uploading")[:300],
        }
        await Redis.redis.hset(upload_batch_progress_key(batch_id), str(item_index), dumps(progress_state))
        return metadata


async def update_upload_batch(batch_id: str, username: str, **updates: object) -> dict[str, object]:
    """Update owned batch metadata atomically, preserving files appended during progress updates."""

    lock = Redis.redis.lock(f"upload:batch-lock:{batch_id}", timeout=60, blocking_timeout=30)
    async with lock:
        metadata = await get_upload_batch_metadata(batch_id)

        if metadata is None or metadata.get("username") != username:
            raise KeyError(batch_id)

        current_status = str(metadata.get("status") or "queued")
        requested_status = updates.get("status")
        if current_status in {"completed", "error", "cancelled"} and requested_status not in {None, current_status}:
            return metadata

        if metadata.get("cancelled") and requested_status not in {None, "cancelled"}:
            return metadata

        # A progress snapshot may have been prepared before another modal appended
        # files. Never let that older, shorter snapshot remove the queue's tail.
        incoming_items = updates.get("items")
        if metadata.get("appendable") and isinstance(incoming_items, list):
            existing_items = [
                dict(item) if isinstance(item, dict) else {"name": str(item)}
                for item in metadata.get("items", [])
            ]
            normalized_items = [
                dict(item) if isinstance(item, dict) else {"name": str(item)}
                for item in incoming_items
            ]

            # A clear-tray action dismisses terminal rows without removing them from
            # the underlying queue. Preserve that UI-only flag when the active browser
            # worker later publishes a snapshot built from its original File objects.
            for item_index, incoming_item in enumerate(normalized_items):
                if (
                        item_index < len(existing_items)
                        and existing_items[item_index].get("dismissed")
                        and not incoming_item.get("dismissed")
                ):
                    incoming_item["dismissed"] = True

            if len(normalized_items) < len(existing_items):
                normalized_items.extend(existing_items[len(normalized_items):])

            updates["items"] = normalized_items
            updates["total_items"] = len(normalized_items)

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
        progress: Progress = cast("Progress", await Redis.get(child_job_id, "Progress"))
    except KeyError:
        return False

    if resolve_upload_job_status(str(progress.message or ""), child_metadata) != "completed":
        return False

    raw_items = batch.get("items") if isinstance(batch.get("items"), list) else []
    items: list[dict[str, object]] = [cast("dict[str, object]", dict(item)) if isinstance(item, dict) else {"name": str(item)} for item in raw_items]
    child_items = getattr(progress, "torrent_items", []) or []
    by_file_index = {
        int(item["file_index"]): item
        for item in child_items
        if isinstance(item, dict) and item.get("file_index") is not None
    }
    if getattr(progress, "ingestion_type", "") == "google_drive_folder" and child_items:
        items = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in child_items if isinstance(item, dict)
        ]

    # A link child only returns a terminal-success progress after the entire direct-link,
    # YouTube, or selected-torrent import has finished. Therefore every selected batch item
    # can be reconciled to completed, even if a browser polling request was interrupted.
    for item in items:
        child_item: dict[str, object] | None = None
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
        ingestion_type=str(getattr(progress, "ingestion_type", "") or batch.get("ingestion_type") or ""),
    )
    return True


async def cleanup_upload_artifacts(job_id: str) -> None:
    """Remove temporary chunk files and Redis session state for an uncommitted upload."""

    paths: set[Path] = set()

    for key in (pending_upload_key(job_id), upload_session_key(job_id)):
        state: object | None = await Redis.get_json(key)

        if isinstance(state, dict):
            raw_path = state.get("file_path")

            if isinstance(raw_path, str) and raw_path:
                paths.add(Path(raw_path))

        await Redis.delete_key(key)

    await Redis.delete_key(upload_activity_key(job_id))
    await Redis.delete_key(upload_chunk_progress_key(job_id))

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
            current: Progress = cast("Progress", await Redis.get(job_id, "Progress"))

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


async def list_user_upload_batches(username: str) -> list[dict[str, object]]:
    """List batch tasks with aggregate progress and the current child stage."""

    index_key = user_upload_batches_key(username)
    raw_batch_ids: set[str] = await Redis.redis.smembers(index_key)
    batches: list[dict[str, object]] = []

    for raw_batch_id in raw_batch_ids:
        batch_id = str(raw_batch_id)
        metadata = await get_upload_batch_metadata(batch_id)

        if metadata is None or metadata.get("username") != username:
            await Redis.redis.srem(index_key, batch_id)
            continue

        # The browser normally publishes current_job_id, but a frontend hot reload
        # can interrupt that PATCH after the backend has already registered the child.
        # Recover it from the batch's persisted child list instead of relying on UI memory.
        if metadata.get("kind") in {"link", "copy"} and metadata.get("status") in {"queued", "uploading", "paused"}:
            current_child_id = str(metadata.get("current_job_id") or "")
            child_ids = [str(value) for value in metadata.get("child_job_ids", []) if str(value)]
            candidates = ([current_child_id] if current_child_id else []) + [
                child_id for child_id in reversed(child_ids) if child_id != current_child_id
            ]

            for candidate_id in candidates:
                child_metadata = await get_upload_job_metadata(candidate_id)
                if (
                        child_metadata is not None
                        and child_metadata.get("username") == username
                        and str(child_metadata.get("batch_id") or "") == batch_id
                        and child_metadata.get("kind") == str(metadata.get("kind") or "link")
                ):
                    if current_child_id != candidate_id:
                        await update_upload_batch(batch_id, username, current_job_id=candidate_id)
                        metadata = await get_upload_batch_metadata(batch_id) or metadata
                    current_child_id = candidate_id
                    break

            if current_child_id:
                if metadata.get("kind") == "link":
                    await reconcile_completed_link_batch(batch_id, username, current_child_id)
                elif metadata.get("kind") == "copy":
                    child_metadata = await get_upload_job_metadata(current_child_id)
                    try:
                        child_progress = cast("Progress", await Redis.get(current_child_id, "Progress"))
                    except KeyError:
                        child_progress = None
                    if child_metadata and child_metadata.get("username") == username and child_progress:
                        child_state = resolve_upload_job_status(str(child_progress.message or ""), child_metadata)
                        if child_state in {"completed", "error", "cancelled"} and metadata.get("status") not in {"completed", "error", "cancelled"}:
                            item_list = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in metadata.get("items", [])]
                            if item_list:
                                size_value = max(0, int(child_progress.total or child_metadata.get("total") or 0))
                                item_list[0].update({
                                    "status": child_state,
                                    "message": str(child_progress.message or child_state.title()),
                                    "size": size_value,
                                    "total": size_value,
                                    "transfer": max(0, int(child_progress.transfer or 0)),
                                    "upload_transfer": max(0, int(child_progress.transfer or 0)),
                                    "upload_total": size_value,
                                    "download_transfer": max(0, int(child_progress.download_transfer or 0)),
                                    "download_total": max(0, int(child_progress.download_total or 0)),
                                    "progress": 100.0 if child_state == "completed" else 0.0,
                                })
                            metadata = await update_upload_batch(
                                batch_id, username, status=child_state,
                                message=str(child_progress.message or child_state.title()),
                                items=item_list, completed_items=1 if child_state == "completed" else 0,
                                progress=100 if child_state == "completed" else 0,
                                transfer=max(0, int(child_progress.transfer or 0)),
                                total=max(0, int(child_progress.total or 0)),
                                upload_transfer=max(0, int(child_progress.transfer or 0)),
                                upload_total=max(0, int(child_progress.total or 0)),
                                download_transfer=max(0, int(child_progress.download_transfer or 0)),
                                download_total=max(0, int(child_progress.download_total or 0)),
                                download_active=bool(child_progress.download_active),
                            )
                metadata = await get_upload_batch_metadata(batch_id) or metadata

        total_items = max(1, int(metadata.get("total_items") or 0))
        completed_items = max(0, min(int(metadata.get("completed_items") or 0), total_items))
        current_transfer = 0
        current_total = 0
        current_upload_transfer = 0
        current_upload_total = 0
        current_download_transfer = 0
        current_download_total = 0
        current_download_active = False
        current_download_speed = 0.0
        current_upload_speed = 0.0
        current_download_eta: float | None = None
        current_upload_eta: float | None = None
        ingestion_type = str(metadata.get("ingestion_type") or "")
        current_file_counts: dict[str, int] = {}
        last_updated_at = 0.0
        current_name = str(metadata.get("current_name") or "").strip()
        current_message = str(metadata.get("message") or "")
        current_job_id = str(metadata.get("current_job_id") or "")
        items_raw = metadata.get("items") if isinstance(metadata.get("items"), list) else []
        items: list[dict[str, object]] = [cast("dict[str, object]", dict(item)) if isinstance(item, dict) else {"name": str(item)} for item in cast("Iterable[object]", items_raw)]
        current_index = int(metadata.get("current_index") or 0)

        # Only the current browser-upload item has live sparse progress. Keeping it in a
        # dedicated Redis hash avoids serializing the entire appendable batch on every byte update.
        if str(metadata.get("kind") or "") == "file" and 0 <= current_index < len(items):
            active_item = items[current_index]
            if active_item.get("status") not in {"completed", "error", "failed", "cancelled", "skipped"}:
                raw_progress = await Redis.redis.hget(upload_batch_progress_key(batch_id), str(current_index))
                if raw_progress:
                    try:
                        progress_state: object = loads(raw_progress)
                        if isinstance(progress_state, dict):
                            for field in ("transfer", "total", "progress", "message"):
                                if field in progress_state:
                                    active_item[field] = progress_state[field]
                    except (TypeError, ValueError):
                        pass

        source_name = str(items[current_index].get("name") or "") if 0 <= current_index < len(items) else ""
        current_name_is_url = current_name.lower().startswith(("http://", "https://"))

        if current_name_is_url:
            current_name = ""

        parsed_source = urlsplit(source_name) if source_name.lower().startswith(("http://", "https://")) else None
        source_host = (parsed_source.hostname or "").lower().removeprefix("www.") if parsed_source else ""
        is_youtube_source = source_host in {"youtube.com", "youtu.be"} or source_host.endswith(".youtube.com")
        source_path_name = (parsed_source.path.rstrip("/").rsplit("/", 1)[-1] if parsed_source else "")
        placeholder_names = {"", "link", "upload", "torrent", "watch", "shorts", "embed", "live", "video", "update_name_from_backend"}

        items_changed = False

        # Browser files run sequentially. The active child is authoritative until the
        # browser starts the next item, at which point the previous completion is saved
        # in the batch itself. Reconcile only the active/latest child on each snapshot.
        if str(metadata.get("kind") or "") == "file" and not metadata.get("cancelled"):
            child_ids = [str(value) for value in metadata.get("child_job_ids", []) if str(value)]
            current_child_id = str(metadata.get("current_job_id") or "")
            child_ids = [current_child_id] if current_child_id else child_ids[-1:]
            for child_position, child_id in enumerate(child_ids):
                child_metadata = await get_upload_job_metadata(child_id)
                if (
                        child_metadata is None
                        or child_metadata.get("username") != username
                        or str(child_metadata.get("batch_id") or "") != batch_id
                        or child_metadata.get("kind") != "file"
                ):
                    continue

                try:
                    child_progress: Progress = cast("Progress", await Redis.get(child_id, "Progress"))
                except KeyError:
                    continue

                child_status = resolve_upload_job_status(
                    str(child_progress.message or ""),
                    child_metadata,
                )
                try:
                    item_index = int(child_metadata.get("batch_item_index", child_position))
                except (TypeError, ValueError):
                    item_index = child_position

                if not 0 <= item_index < len(items):
                    continue

                item = items[item_index]
                if not isinstance(item, dict):
                    continue

                if child_status == "completed":
                    item_size = max(
                        0,
                        int(item.get("size") or item.get("total") or child_progress.total or child_metadata.get("total") or 0),
                    )
                    completed_values: dict[str, object] = {
                        "status": "completed",
                        "message": "Uploaded successfully",
                        "transfer": item_size,
                        "total": item_size,
                        "size": item_size,
                        "progress": 100.0,
                        "download_transfer": item_size,
                        "download_total": item_size,
                        "upload_transfer": item_size,
                        "upload_total": item_size,
                        "download_speed": 0.0,
                        "upload_speed": 0.0,
                        "download_eta": None,
                        "upload_eta": None,
                    }
                elif child_status in {"error", "cancelled"}:
                    completed_values = {
                        "status": child_status,
                        "message": str(child_progress.message or ("Upload cancelled" if child_status == "cancelled" else "Upload failed"))[:300],
                    }
                else:
                    continue

                if any(item.get(key) != value for key, value in completed_values.items()):
                    item.update(completed_values)
                    items_changed = True

        if current_job_id:
            try:
                child_progress: Progress = cast("Progress", await Redis.get(current_job_id, "Progress"))
                current_transfer = max(0, int(child_progress.transfer or 0))
                current_total = max(0, int(child_progress.total or 0))
                current_upload_transfer = current_transfer
                current_upload_total = current_total
                current_download_transfer = max(0, int(getattr(child_progress, "download_transfer", 0) or 0))
                current_download_total = max(0, int(getattr(child_progress, "download_total", 0) or 0))

                # Reflect bytes currently arriving at FastAPI within each active chunk.
                # Merge offsets with already-confirmed ranges instead of adding them to
                # child_progress.download_transfer, which may already include completed
                # chunks. The committed-range worker remains unchanged.
                if str(metadata.get("kind") or "") == "file":
                    session_state = await Redis.get_json(upload_session_key(current_job_id))
                    raw_chunks = await Redis.redis.hgetall(upload_chunk_progress_key(current_job_id))
                    if isinstance(session_state, dict):
                        total_received = 0
                        raw_ranges = session_state.get("ranges", [])
                        confirmed_ranges = []
                        if isinstance(raw_ranges, list):
                            for raw_range in raw_ranges:
                                if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
                                    try:
                                        confirmed_ranges.append((int(raw_range[0]), int(raw_range[1])))
                                    except (TypeError, ValueError):
                                        continue

                        # Count each active byte range only once, including its overlap
                        # with committed ranges or another retried request.
                        active_ranges = []
                        for raw_start, raw_end in raw_chunks.items():
                            try:
                                start, end = int(raw_start), int(raw_end)
                                if end > start:
                                    active_ranges.append((start, end))
                            except (TypeError, ValueError):
                                continue

                        all_ranges = sorted(confirmed_ranges + active_ranges)
                        merged_end = 0
                        for range_start, range_end in all_ranges:
                            if range_start > merged_end:
                                # Holes are not transferred yet; only count actual bytes.
                                total_received += range_end - range_start
                                merged_end = range_end
                            elif range_end > merged_end:
                                total_received += range_end - merged_end
                                merged_end = range_end

                        current_download_transfer = max(
                            current_download_transfer,
                            min(current_total, total_received),
                        )

                current_download_active = bool(getattr(child_progress, "download_active", False))
                current_download_speed = max(0.0, float(getattr(child_progress, "download_speed", 0) or 0))
                current_upload_speed = max(0.0, float(getattr(child_progress, "upload_speed", 0) or 0))
                current_download_eta = getattr(child_progress, "download_eta", None)
                current_upload_eta = getattr(child_progress, "upload_eta", None)
                ingestion_type = str(getattr(child_progress, "ingestion_type", "") or ingestion_type)
                current_file_counts = getattr(child_progress, "file_counts", {}) or {}
                last_updated_at = max(0.0, float(getattr(child_progress, "last_updated_at", 0) or 0))
                if ingestion_type and metadata.get("ingestion_type") != ingestion_type:
                    metadata["ingestion_type"] = ingestion_type
                    items_changed = True
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

                # Terminal child outcomes must update the parent batch even when
                # the browser-side polling loop vanished during HMR or navigation.
                child_metadata = await get_upload_job_metadata(current_job_id)
                child_status = resolve_upload_job_status(
                    progress_message,
                    child_metadata if child_metadata and child_metadata.get("username") == username else {},
                )
                if (
                        child_metadata
                        and child_metadata.get("username") == username
                        and str(child_metadata.get("batch_id") or "") == batch_id
                        and child_status in {"error", "cancelled"}
                        and metadata.get("status") in {"queued", "uploading", "paused"}
                ):
                    if str(metadata.get("kind") or "") == "file":
                        # Browser file queues own their sequencing. A cancelled or failed
                        # child is a terminal outcome for just its current item; the browser
                        # worker catches it and advances to the next selected file.
                        try:
                            child_item_index = int(child_metadata.get("batch_item_index", current_index))
                        except (TypeError, ValueError):
                            child_item_index = current_index
                        if 0 <= child_item_index < len(items):
                            child_item = items[child_item_index]
                            target_status = "cancelled" if child_status == "cancelled" else "error"
                            child_message = progress_message or (
                                "Cancelled" if child_status == "cancelled" else "Upload failed"
                            )
                            if child_item.get("status") not in {"completed", "cancelled", "error", "failed", "skipped"}:
                                child_item["status"] = target_status
                                child_item["message"] = child_message[:300]
                                items_changed = True
                        metadata["completed_items"] = sum(1 for item in items if item.get("status") == "completed")
                        items_changed = True
                    else:
                        metadata["status"] = child_status
                        metadata["message"] = progress_message or (
                            "Upload cancelled" if child_status == "cancelled" else "Upload failed"
                        )
                        # Ingestion jobs publish authoritative file-level outcomes from the
                        # server. Preserve those successes/failures instead of overwriting them
                        # with a generic parent-level cancellation/error snapshot.
                        if ingestion_type == "google_drive_folder":
                            for item in items:
                                if child_status == "cancelled" and item.get("status") not in {"completed", "error", "failed"}:
                                    item["status"] = "cancelled"
                                    item["message"] = "Cancelled"
                                    items_changed = True
                        else:
                            active_seen = False
                            for item in items:
                                if item.get("status") in {"downloading", "uploading"}:
                                    if child_status == "cancelled":
                                        item["status"] = "cancelled"
                                        item["message"] = "Cancelled"
                                    elif not active_seen:
                                        item["status"] = "error"
                                        item["message"] = metadata["message"]
                                        active_seen = True
                                    else:
                                        item["status"] = "skipped"
                                        item["message"] = "Not started after failure"
                                    items_changed = True
                                elif child_status == "cancelled" and item.get("status") == "queued":
                                    item["status"] = "cancelled"
                                    item["message"] = "Cancelled"
                                    items_changed = True
                                elif child_status == "error" and item.get("status") == "queued":
                                    item["status"] = "skipped"
                                    item["message"] = "Not started after failure"
                                    items_changed = True
                        metadata["completed_items"] = sum(1 for item in items if item.get("status") == "completed")
                        items_changed = True

                # The child process is the source of truth for torrent file-level progress.
                # Rebuild the tray items from Redis on every batch poll so a frontend reload
                # or hot-module replacement cannot strand the tray at its last browser PATCH.
                child_items = getattr(child_progress, "torrent_items", []) or []
                if ingestion_type == "google_drive_folder":
                    folder_items = [
                        {key: value for key, value in child_item.items() if not key.startswith("_")}
                        for child_item in child_items if isinstance(child_item, dict)
                    ]
                    if str(metadata.get("status") or "") == "cancelled" or metadata.get("cancelled"):
                        for folder_item in folder_items:
                            if folder_item.get("status") not in {"completed", "error", "failed"}:
                                folder_item["status"] = "cancelled"
                                folder_item["message"] = "Cancelled"
                    if folder_items and folder_items != items:
                        items = folder_items
                        metadata["items"] = items
                        metadata["total_items"] = len(items)
                        metadata["completed_items"] = sum(item.get("status") == "completed" for item in items)
                        items_changed = True
                    # This import creates real files under the folder tree, not a file for the URL itself.
                    if current_name and 0 <= current_index < len(items):
                        current_name = str(child_progress.name or current_name)
                if child_items and str(metadata.get("kind") or "") == "link" and ingestion_type != "google_drive_folder":
                    child_by_index = {
                        int(child_item["file_index"]): child_item
                        for child_item in child_items
                        if isinstance(child_item, dict) and child_item.get("file_index") is not None
                    }
                    for item in items:
                        if item.get("file_index") is None:
                            continue
                        child_item: dict[str, object] | None
                        try:
                            child_item = child_by_index.get(int(item["file_index"]))
                        except (TypeError, ValueError):
                            child_item = None
                        if child_item is None:
                            continue

                        child_status = str(child_item.get("status") or item.get("status") or "queued")
                        child_transfer = max(0, int(child_item.get("transfer") or 0))
                        child_total = max(0, int(child_item.get("total") or item.get("size") or 0))
                        child_name = str(child_item.get("name") or item.get("name") or "")
                        child_message = str(child_item.get("message") or child_status.title())
                        if (
                                item.get("status") != child_status
                                or int(item.get("transfer") or 0) != child_transfer
                                or int(item.get("total") or item.get("size") or 0) != child_total
                                or item.get("name") != child_name
                                or item.get("message") != child_message
                                or int(item.get("download_transfer") or 0) != int(child_item.get("download_transfer") or 0)
                                or int(item.get("upload_transfer") or 0) != int(child_item.get("upload_transfer") or 0)
                                or float(item.get("download_speed") or 0) != float(child_item.get("download_speed") or 0)
                                or float(item.get("upload_speed") or 0) != float(child_item.get("upload_speed") or 0)
                        ):
                            item.update({
                                "status": child_status,
                                "transfer": child_transfer,
                                "total": child_total,
                                "size": child_total,
                                "name": child_name,
                                "message": child_message,
                                "download_transfer": max(0, int(child_item.get("download_transfer") or 0)),
                                "download_total": max(0, int(child_item.get("download_total") or child_total)),
                                "download_speed": max(0.0, float(child_item.get("download_speed") or 0)),
                                "download_eta": child_item.get("download_eta"),
                                "upload_transfer": max(0, int(child_item.get("upload_transfer") or 0)),
                                "upload_total": max(0, int(child_item.get("upload_total") or child_total)),
                                "upload_speed": max(0.0, float(child_item.get("upload_speed") or 0)),
                                "upload_eta": child_item.get("upload_eta"),
                            })
                            items_changed = True

                    completed_from_child = sum(1 for item in items if item.get("status") == "completed")
                    if int(metadata.get("completed_items") or 0) != completed_from_child:
                        metadata["completed_items"] = completed_from_child
                        items_changed = True

                    active_item = next(
                        (item for item in items if item.get("status") in {"downloading", "waiting_upload", "uploading"}),
                        None,
                    )
                    if active_item is not None:
                        current_name = str(active_item.get("name") or current_name)
                        current_message = str(active_item.get("message") or current_message)

            except KeyError:
                pass


        if (
                str(metadata.get("kind") or "") == "link"
                and ingestion_type != "google_drive_folder"
                and current_name
                and 0 <= current_index < len(items)
        ):
            submitted_name = str(items[current_index].get("name") or "")

            if submitted_name != current_name and not current_name.lower().startswith(("http://", "https://")):
                items[current_index]["name"] = current_name
                items_changed = True

        # Cancellation is terminal for the parent batch. Do not let a stale child
        # progress snapshot resurrect rows that were still downloading or waiting in
        # the upload FIFO when the user cancelled the torrent/folder import.
        if metadata.get("cancelled") or str(metadata.get("status") or "") == "cancelled":
            for item in items:
                item_status = str(item.get("status") or "queued")
                if item_status not in {"completed", "error", "failed", "cancelled", "skipped"}:
                    item["status"] = "cancelled"
                    item["message"] = "Cancelled"
                    items_changed = True

            completed_count = sum(str(item.get("status") or "") == "completed" for item in items)
            if int(metadata.get("completed_items") or 0) != completed_count:
                metadata["completed_items"] = completed_count
                items_changed = True

        if items_changed:
            updates: dict[str, object] = {
                "items": items,
                "total_items": len(items),
                "completed_items": sum(1 for item in items if item.get("status") == "completed"),
                "status": str(metadata.get("status") or "queued"),
            }
            for key in ("message", "current_job_id", "current_name", "current_index", "paused", "cancelled"):
                if key in metadata:
                    updates[key] = metadata[key]
            if ingestion_type:
                updates["ingestion_type"] = ingestion_type

            metadata = await update_upload_batch(batch_id, username, **updates)
            items = [
                dict(item) if isinstance(item, dict) else {"name": str(item)}
                for item in metadata.get("items", [])
            ]

        # The child progress above may have completed another file during this poll.
        # Re-read totals after rebuilding folder/torrent items rather than using stale batch metadata.
        total_items = max(1, int(metadata.get("total_items") or len(items) or 0))
        completed_items = max(0, min(int(metadata.get("completed_items") or 0), total_items))
        current_fraction = min(1.0, current_transfer / current_total) if current_total else 0.0
        status = str(metadata.get("status") or "queued")

        if ingestion_type in {"google_drive_folder", "torrent"} and items:
            sizes_known = all(max(0, int(item.get("size") or item.get("total") or 0)) > 0 for item in items)
            item_count = len(items)
            if sizes_known:
                total_bytes = sum(max(0, int(item.get("size") or item.get("total") or 0)) for item in items)
                downloaded_bytes = 0
                uploaded_bytes = 0
                for item in items:
                    item_size = max(0, int(item.get("size") or item.get("total") or 0))
                    item_status = str(item.get("status") or "queued")
                    if item_status in {"waiting_upload", "uploading", "completed", "downloaded"}:
                        downloaded_bytes += item_size
                    elif item_status == "downloading":
                        downloaded_bytes += min(item_size, max(0, int(item.get("download_transfer", item.get("transfer", 0)) or 0)))
                    if item_status == "completed":
                        uploaded_bytes += item_size
                    elif item_status == "uploading":
                        uploaded_bytes += min(item_size, max(0, int(item.get("upload_transfer", item.get("transfer", 0)) or 0)))
                download_fraction = min(1.0, downloaded_bytes / total_bytes) if total_bytes else 0.0
                upload_fraction = min(1.0, uploaded_bytes / total_bytes) if total_bytes else 0.0
            else:
                # When Drive does not disclose file sizes, use item counts rather than
                # pretending that a byte-accurate percentage is known.
                discovered = max(1, len(items))
                statuses = [str(item.get("status") or "queued") for item in items]
                downloaded_items = sum(value in {"downloaded", "waiting_upload", "uploading", "completed"} for value in statuses)
                downloading_items = sum(value == "downloading" for value in statuses)
                uploading_items = sum(value == "uploading" for value in statuses)
                uploaded_items = sum(value == "completed" for value in statuses)
                download_fraction = min(1.0, (downloaded_items + 0.5 * downloading_items) / discovered)
                upload_fraction = min(1.0, (uploaded_items + 0.5 * uploading_items) / discovered)
            progress = 100.0 if status == "completed" else min(99.0, ((download_fraction + upload_fraction) / 2) * 100.0)
        else:
            progress = 100.0 if status == "completed" else min(99.0, ((completed_items + current_fraction) / total_items) * 100.0)

        if metadata.get("cancelled") or status == "cancelled":
            status = "cancelled"
            current_download_active = False
            current_download_speed = 0.0
            current_upload_speed = 0.0
            current_download_eta = None
            current_upload_eta = None

        batches.append({
            "batch_id": batch_id,
            "name": str(metadata.get("name") or "Upload task"),
            "kind": str(metadata.get("kind") or "file"),
            "directory": str(metadata.get("directory") or "/home"),
            "directories": [str(value) for value in metadata.get("directories", []) if str(value)],
            "data_center": str(metadata.get("data_center") or ""),
            "items": items,
            "torrent_files": metadata.get("torrent_files", []) if isinstance(metadata.get("torrent_files", []), list) else [],
            "ingestion_type": ingestion_type,
            "file_counts": current_file_counts,
            "total_items": int(metadata.get("total_items") or len(items) or 0),
            "completed_items": completed_items,
            "current_index": current_index,
            "current_job_id": current_job_id,
            "current_name": current_name,
            "status": status,
            "paused": bool(metadata.get("paused")),
            "progress": progress,
            "transfer": current_transfer,
            "total": current_total,
            "upload_transfer": current_upload_transfer,
            "upload_total": current_upload_total,
            "download_transfer": current_download_transfer,
            "download_total": current_download_total,
            "download_active": current_download_active,
            "download_speed": current_download_speed,
            "upload_speed": current_upload_speed,
            "download_eta": current_download_eta,
            "upload_eta": current_upload_eta,
            "last_updated_at": last_updated_at,
            "message": current_message or status.title(),
            "created_at": int(float(metadata.get("created_at") or 0) * 1000),
        })

    batches.sort(key=lambda item: item["created_at"], reverse=True)
    return batches


async def run_chunk_upload_job(job_id: str, file: File, file_path: Path, producer_task: Task[None]) -> None:
    """Transfer received chunks and commit the database row only after a cancellation-safe final check."""

    write_log(
        "INFO",
        Database,
        "UPLOAD CHUNKS",
        file.username,
        f"Chunk upload worker started job_id={job_id} file={file.name!r} staged_path={file_path.name!r}",
    )

    try:
        session_state: object | None = await Redis.get_json(upload_session_key(job_id))
        download_total = int(session_state.get("total") or 0) if isinstance(session_state, dict) else 0

        async def pause_requested() -> bool:
            """Read the child job's authoritative pause flag for the provider scheduler."""

            latest_metadata = await get_upload_job_metadata(job_id)
            if latest_metadata and latest_metadata.get("cancelled"):
                raise CancelledError
            return bool(latest_metadata and latest_metadata.get("paused"))

        async with aclosing(file_upload(
                file,
                file_path,
                producer_task,
                intermediate=True,
                track_download=True,
                download_total=download_total,
                pause_check=pause_requested,
        )) as upload_stream:
            async for progress in upload_stream:
                latest_metadata = await get_upload_job_metadata(job_id)

                if latest_metadata and latest_metadata.get("cancelled"):
                    raise RuntimeError("Upload cancelled")

                if latest_metadata and latest_metadata.get("paused"):
                    progress.message = "Paused by you"
                    progress.upload_speed = 0
                    progress.download_speed = 0

                await Redis.set(job_id, progress)

        if producer_task.done() and not producer_task.cancelled():
            producer_error: BaseException | None = producer_task.exception()

            if producer_error is not None:
                latest_metadata = await get_upload_job_metadata(job_id)
                write_log(
                    "ERROR",
                    Database,
                    "UPLOAD CHUNKS",
                    file.username,
                    f"Chunk producer failed job_id={job_id} error={type(producer_error).__name__}: "
                    f"{sanitize_sensitive_urls(str(producer_error))[:250]}",
                )

                if not (latest_metadata and (latest_metadata.get("failed") or latest_metadata.get("cancelled"))):
                    await Redis.set(job_id, Progress(file.name, f"Upload failed: {sanitize_sensitive_urls(str(producer_error))[:250]}", 0))

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
            write_log(
                "INFO",
                Database,
                "UPLOAD CHUNKS",
                file.username,
                f"File metadata committed job_id={job_id} file_id={file.id} name={file.name!r} bytes={file.size}",
            )
            complete = Progress(file.name, f"Upload Complete of id={file.id}", max(file.size, 0))
            complete.transfer = max(file.size, 0)
            complete.download_transfer = max(file.size, 0)
            complete.download_total = download_total or max(file.size, 0)
            complete.download_active = True
            await Redis.set(job_id, complete)

    except CancelledError:
        write_log("INFO", Database, "UPLOAD CHUNKS", file.username, f"Chunk upload worker cancelled job_id={job_id}")
        raise

    except Exception as error:
        metadata = await get_upload_job_metadata(job_id)

        if metadata and metadata.get("cancelled"):
            # Cancellation closes the async upload stream and its provider-part
            # tasks. Do not report expected shutdown as a failed upload.
            write_log("INFO", Database, "UPLOAD CHUNKS", file.username, f"Upload worker stopped after cancellation job_id={job_id}")
        else:
            write_log(
                "ERROR",
                Database,
                "UPLOAD CHUNKS",
                file.username,
                f"Chunk upload worker failed job_id={job_id} error={type(error).__name__}: "
                f"{sanitize_sensitive_urls(str(error))[:300]}",
            )

            if not (metadata and metadata.get("failed")):
                await Redis.set(job_id, Progress(file.name, f"Upload failed: {sanitize_sensitive_urls(str(error))[:250]}", 0))

    finally:
        if not producer_task.done():
            producer_task.cancel()

        await gather(producer_task, return_exceptions=True)
        await cleanup_upload_artifacts(job_id)
        write_log("INFO", Database, "UPLOAD CHUNKS", file.username, f"Chunk upload cleanup completed job_id={job_id}")
