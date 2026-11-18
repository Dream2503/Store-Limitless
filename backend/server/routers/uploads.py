"""Upload initiation, chunk reception, and link-based upload endpoints."""

from __future__ import annotations

import json
from asyncio import CancelledError, Task, create_task
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path
from time import time
from typing import Any, cast
from urllib.parse import SplitResult, parse_qs, unquote, urlsplit
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.database.models import Directory, File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import get_current_user
from backend.server.utils import perform_validation
from core.config import TRANSFER_PATH
from core.data_center import DataCenter
from core.transfer import link_upload
from core.utils import sanitize_sensitive_urls, write_log
from core.utils.download_utils import preview_torrent_files
from core.utils.progress import Progress
from .upload_state import (
    cancel_upload_task,
    cancel_upload_job_state,
    cleanup_upload_artifacts,
    get_upload_batch_metadata,
    get_upload_job_metadata,
    list_user_upload_batches,
    list_user_upload_jobs,
    pending_upload_key,
    record_written_range,
    register_upload_job,
    resolve_upload_job_status,
    run_chunk_upload_job,
    terminal_upload_job_key,
    track_upload_task,
    register_upload_batch,
    reconcile_completed_link_batch,
    update_upload_batch,
    upload_batch_key,
    user_upload_batches_key,
    upload_job_key,
    upload_session_key,
    user_upload_jobs_key,
    wait_for_all_chunks,
)

router: APIRouter = APIRouter()
public_router: APIRouter = APIRouter()


@router.post("/upload")
async def upload_file(
        data_center: str = Header(..., alias="X-Data-Center"),
        directory: str = Header(..., alias="X-Directory"),
        file_name: str = Header(..., alias="X-File-Name"),
        file_size: int = Header(0, alias="X-File-Size", ge=0),
        batch_id: str = Header("", alias="X-Upload-Batch-Id"),
        user: User = Depends(get_current_user)
) -> JSONResponse:
    """Create an upload job and store its initial metadata in Redis."""

    data_center = perform_validation(data_center, "data_center")
    file_name = perform_validation(file_name, "file_name")

    if batch_id:
        batch = await get_upload_batch_metadata(batch_id)

        if batch is None or batch.get("username") != user.username:
            raise HTTPException(status_code=404, detail="Upload task not found")

        if batch.get("cancelled") or batch.get("status") in {"cancelled", "completed", "error"}:
            raise HTTPException(status_code=409, detail="Upload task is no longer active")

    directory_obj: Directory | None = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    job_id: str = str(uuid4())
    file_path: Path = TRANSFER_PATH / user.username / job_id / file_name
    file: File = File(
        directory_id=cast(int, directory_obj.id),
        name=file_name,
        type=guess_type(file_name)[0] or "application/octet-stream",
        size=0,
        modified_at=datetime.now(timezone.utc),
        links=[],
        data_center=data_center,
        username=user.username
    )
    await Redis.set_json(
        pending_upload_key(job_id),
        {"file": file.model_dump(mode="json"), "file_path": str(file_path), "username": user.username},
        ex=60 * 60,
    )
    await Redis.set(job_id, Progress(file_name, "Waiting for upload", 0))
    await register_upload_job(
        job_id,
        username=user.username,
        name=file_name,
        kind="file",
        directory=str(directory_obj.path),
        data_center=data_center,
        total=file_size,
        batch_id=batch_id,
    )
    if batch_id:
        latest_batch = await get_upload_batch_metadata(batch_id)

        if latest_batch is None or latest_batch.get("username") != user.username or latest_batch.get("cancelled") or latest_batch.get("status") in {
            "cancelled", "completed", "error"}:
            await cleanup_upload_artifacts(job_id)
            await Redis.redis.srem(user_upload_jobs_key(user.username), job_id)
            await Redis.delete_key(upload_job_key(job_id))
            await Redis.delete(job_id, "Progress")
            raise HTTPException(status_code=409, detail="Upload task was cancelled or removed")

    return JSONResponse({"job_id": job_id})


@router.post("/upload-batches")
async def create_upload_batch(payload: dict[str, Any] = Body(...), user: User = Depends(get_current_user)) -> JSONResponse:
    """Register one tray task for a multi-file upload or a single link import."""

    name = str(payload.get("name") or "Upload task").strip()[:255]
    kind = str(payload.get("kind") or "file")
    directory = perform_validation(str(payload.get("directory") or "/home"), "directory")
    data_center = perform_validation(str(payload.get("data_center") or "Discord"), "data_center")
    raw_items = payload.get("items")

    if kind not in {"file", "link"} or not isinstance(raw_items, list) or not raw_items or len(raw_items) > 2000:
        raise HTTPException(status_code=422, detail="Upload batch must contain between 1 and 2000 items")

    if kind == "link" and len(raw_items) != 1:
        raise HTTPException(status_code=422, detail="Only one link can be imported per upload task")

    directory_obj = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Destination directory not found")

    items: list[dict[str, Any]] = []

    for item in raw_items:
        if isinstance(item, str):
            item_name = item.strip()
            item_size = 0

        elif isinstance(item, dict):
            item_name = str(item.get("name") or "Upload item").strip()[:500]

            try:
                item_size = max(0, int(item.get("size") or 0))

            except (TypeError, ValueError):
                item_size = 0
        else:
            raise HTTPException(status_code=422, detail="Invalid upload batch item")

        if not item_name:
            item_name = "Upload item"

        items.append({"name": item_name, "size": item_size})

    batch_id = str(uuid4())
    await register_upload_batch(
        batch_id,
        username=user.username,
        name=name,
        kind=kind,
        directory=str(directory_obj.path),
        data_center=data_center,
        items=items,
    )
    return JSONResponse({"batch_id": batch_id, "status": "queued"})


@router.get("/upload-batches")
async def get_upload_batches(user: User = Depends(get_current_user)) -> JSONResponse:
    """List whole upload tasks, including queued items and completed task history."""

    return JSONResponse({"batches": await list_user_upload_batches(user.username)})


@router.patch("/upload-batches/{batch_id}")
async def patch_upload_batch(batch_id: str, payload: dict[str, Any] = Body(...), user: User = Depends(get_current_user)) -> JSONResponse:
    """Publish the queue's current item and task-level status."""

    metadata = await get_upload_batch_metadata(batch_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload task not found")

    if metadata.get("status") == "completed":
        # Completion is terminal. A late progress snapshot from the browser must not
        # replace completed per-file states with stale "downloading" values.
        return JSONResponse({"batch_id": batch_id, "status": "completed"})

    status = str(payload.get("status") or metadata.get("status") or "queued")

    if status not in {"queued", "uploading", "completed", "error", "cancelled"}:
        raise HTTPException(status_code=422, detail="Invalid upload task status")

    updates: dict[str, Any] = {"status": status}

    if "message" in payload:
        updates["message"] = str(payload.get("message") or "")[:500]

    if "torrent_files" in payload:
        torrent_files = payload.get("torrent_files")
        if not isinstance(torrent_files, list):
            raise HTTPException(status_code=422, detail="Invalid torrent file metadata")
        if metadata.get("kind") == "link" and len(torrent_files) > 1:
            raise HTTPException(status_code=422, detail="Only one torrent link can be attached to an upload task")
        updates["torrent_files"] = torrent_files

    if "items" in payload:
        items_payload = payload.get("items")
        if not isinstance(items_payload, list):
            raise HTTPException(status_code=422, detail="Invalid upload task items")
        updates["items"] = [
            {
                "name": str(item.get("name") or "Torrent file")[:500],
                "size": max(0, int(item.get("size") or 0)),
                "status": str(item.get("status") or "queued"),
                "message": str(item.get("message") or "Queued")[:300],
                **({"index": int(item["index"])} if "index" in item else {}),
                **({"path": str(item["path"])[:1000]} if "path" in item else {}),
                **({"torrent_link": str(item["torrent_link"])} if "torrent_link" in item else {}),
                **({"torrent_index": int(item["torrent_index"])} if "torrent_index" in item else {}),
                **({"file_index": int(item["file_index"])} if "file_index" in item else {}),
                **({"transfer": max(0, int(item.get("transfer") or 0))} if "transfer" in item else {}),
                **({"total": max(0, int(item.get("total") or 0))} if "total" in item else {}),
            }
            for item in items_payload if isinstance(item, dict)
        ]
        updates["total_items"] = len(updates["items"])
        updates["completed_items"] = sum(item["status"] == "completed" for item in updates["items"])

    if "current_name" in payload:
        updates["current_name"] = str(payload.get("current_name") or "")[:500]

    if "current_index" in payload:
        updates["current_index"] = max(0, min(int(payload.get("current_index") or 0), int(metadata.get("total_items") or 0)))

    if "completed_items" in payload:
        updates["completed_items"] = max(0, min(int(payload.get("completed_items") or 0), int(metadata.get("total_items") or 0)))

    if "current_job_id" in payload:
        current_job_id = str(payload.get("current_job_id") or "")
        updates["current_job_id"] = current_job_id

        if current_job_id:
            child_ids = [str(value) for value in metadata.get("child_job_ids", [])]

            if current_job_id not in child_ids:
                child_ids.append(current_job_id)

            updates["child_job_ids"] = child_ids

    if "item_index" in payload and "item_status" in payload:
        try:
            item_index = int(cast(Any, payload.get("item_index")))

        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid upload item index")

        item_status = str(payload.get("item_status") or "queued")

        if item_status not in {"queued", "uploading", "completed", "error", "cancelled", "skipped"}:
            raise HTTPException(status_code=422, detail="Invalid upload item status")

        items = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in metadata.get("items", [])]

        if 0 <= item_index < len(items):
            items[item_index]["status"] = item_status
            items[item_index]["message"] = str(payload.get("item_message") or item_status.title())[:300]
            updates["items"] = items

    if status == "error":
        items = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in metadata.get("items", [])]

        for item in items:
            if item.get("status") == "queued":
                item["status"] = "skipped"
                item["message"] = "Not started"

            elif item.get("status") == "uploading":
                item["status"] = "error"
                item["message"] = updates.get("message", "Upload failed")

        updates["items"] = items

    updated = await update_upload_batch(batch_id, user.username, **updates)
    return JSONResponse({"batch_id": batch_id, "status": str(updated.get("status") or "queued")})


@router.post("/upload-batches/{batch_id}/cancel")
async def cancel_upload_batch(batch_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Cancel the active item and prevent any remaining queued items from starting."""

    metadata = await get_upload_batch_metadata(batch_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload task not found")

    if metadata.get("status") in {"completed", "error", "cancelled"}:
        return JSONResponse({"batch_id": batch_id, "status": str(metadata.get("status"))})

    child_ids = [str(value) for value in metadata.get("child_job_ids", [])]
    current_child = str(metadata.get("current_job_id") or "")

    if current_child and current_child not in child_ids:
        child_ids.append(current_child)

    completed_during_cancel = False

    for child_id in reversed(child_ids):
        child_meta = await get_upload_job_metadata(child_id)

        if not child_meta or child_meta.get("username") != user.username:
            continue

        try:
            child_progress: Progress = cast(Progress, await Redis.get(child_id, "Progress"))
            child_status = resolve_upload_job_status(str(child_progress.message or ""), child_meta)

        except KeyError:
            continue

        if child_status != "uploading":
            continue

        try:
            await cancel_upload_job_state(child_id, user.username)

        except HTTPException as error:
            if error.status_code != 409:
                raise

            try:
                finished_progress: Progress = cast(Progress, await Redis.get(child_id, "Progress"))
                finished_meta = await get_upload_job_metadata(child_id)
                completed_during_cancel = resolve_upload_job_status(str(finished_progress.message or ""), finished_meta or {}) == "completed"

            except KeyError:
                completed_during_cancel = False

        break

    metadata["cancelled"] = True
    metadata["cancelled_at"] = time()
    metadata["status"] = "cancelled"
    metadata["message"] = "Upload task cancelled"
    item_states = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in metadata.get("items", [])]
    current_index = int(metadata.get("current_index") or 0)

    if completed_during_cancel and 0 <= current_index < len(item_states):
        current_item = item_states[current_index]
        current_item["status"] = "completed"
        current_item["message"] = "Completed while cancellation was requested"
        metadata["completed_items"] = min(int(metadata.get("total_items") or len(item_states)),
                                          max(int(metadata.get("completed_items") or 0), current_index + 1))
        metadata["message"] = "Cancelled remaining items; the active item completed as cancellation arrived"

    for index, item in enumerate(item_states):
        if item.get("status") in {"queued", "uploading"}:
            item["status"] = "cancelled"
            item["message"] = "Cancelled or skipped"

    metadata["items"] = item_states
    await Redis.set_json(upload_batch_key(batch_id), metadata)
    return JSONResponse({"batch_id": batch_id, "status": "cancelled"})


async def _delete_upload_batch(batch_id: str, username: str, *, cancel_active: bool = False) -> None:
    """Delete a terminal batch and the child job records belonging to it."""

    metadata = await get_upload_batch_metadata(batch_id)

    if metadata is None:
        await Redis.redis.srem(user_upload_batches_key(username), batch_id)
        return

    if metadata.get("username") != username:
        raise HTTPException(status_code=404, detail="Upload task not found")

    child_ids = [str(value) for value in metadata.get("child_job_ids", [])]

    if metadata.get("status") in {"queued", "uploading"}:
        if not cancel_active:
            raise HTTPException(status_code=409, detail="Cancel the active upload task before removing it")

        current_child = str(metadata.get("current_job_id") or "")

        if current_child and current_child not in child_ids:
            child_ids.append(current_child)

        for child_id in reversed(child_ids):
            child_meta = await get_upload_job_metadata(child_id)

            if not child_meta or child_meta.get("username") != username:
                continue

            try:
                child_progress: Progress = cast(Progress, await Redis.get(child_id, "Progress"))
                child_status = resolve_upload_job_status(str(child_progress.message or ""), child_meta)

            except KeyError:
                continue

            if child_status != "uploading":
                continue

            try:
                await cancel_upload_job_state(child_id, username)

            except HTTPException as error:
                if error.status_code != 409:
                    raise

        metadata["cancelled"] = True
        metadata["status"] = "cancelled"
        metadata["message"] = "Upload task cancelled and removed"
        await Redis.set_json(upload_batch_key(batch_id), metadata)

    for child_id in child_ids:

        child_meta = await get_upload_job_metadata(child_id)
        if child_meta and child_meta.get("username") == username:
            await cleanup_upload_artifacts(child_id)
            await Redis.redis.srem(user_upload_jobs_key(username), child_id)
            await Redis.delete_key(upload_job_key(child_id))
            await Redis.delete(child_id, "Progress")

    await Redis.redis.srem(user_upload_batches_key(username), batch_id)
    await Redis.delete_key(upload_batch_key(batch_id))


@router.delete("/upload-batches/{batch_id}")
async def dismiss_upload_batch(batch_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Remove one terminal task from the tray."""

    await _delete_upload_batch(batch_id, user.username)
    return JSONResponse({"message": "Upload task removed"})


@router.delete("/upload-batches")
async def clear_upload_batches(user: User = Depends(get_current_user)) -> JSONResponse:
    """Cancel active tasks and clear the whole upload tray when the user chooses Clear all."""

    batch_ids = await Redis.redis.smembers(user_upload_batches_key(user.username))

    for raw_batch_id in list(batch_ids):
        await _delete_upload_batch(str(raw_batch_id), user.username, cancel_active=True)

    return JSONResponse({"message": "Upload tray cleared"})


@router.get("/upload-jobs")
async def get_upload_jobs(user: User = Depends(get_current_user)) -> JSONResponse:
    """List upload jobs belonging to the authenticated username across all devices."""

    return JSONResponse({"jobs": await list_user_upload_jobs(user.username)})


@router.get("/upload-jobs/{job_id}/status")
async def get_upload_job_status(job_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Return one job's status, including a short-lived result after tray cleanup."""

    metadata: dict[str, Any] | None = await get_upload_job_metadata(job_id)

    if metadata is not None:
        if metadata.get("username") != user.username:
            raise HTTPException(status_code=404, detail="Upload job not found")

        try:
            progress: Progress = cast(Progress, await Redis.get(job_id, "Progress"))

        except KeyError:
            progress = Progress(str(metadata.get("name") or "Upload"), "Upload status unavailable", 0)

        message: str = str(progress.message or "")
        return JSONResponse({
            "job_id": job_id,
            "name": str(metadata.get("name") or progress.name or "Upload"),
            "status": resolve_upload_job_status(message, metadata),
            "message": message,
        })

    terminal: Any = await Redis.get_json(terminal_upload_job_key(job_id))

    if not isinstance(terminal, dict) or terminal.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    return JSONResponse({
        "job_id": job_id,
        "name": str(terminal.get("name") or "Upload"),
        "status": str(terminal.get("status") or "error"),
        "message": str(terminal.get("message") or "Upload finished"),
    })


@router.delete("/upload-jobs/{job_id}")
async def dismiss_upload_job(job_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Dismiss a terminal upload job from the shared account tray."""

    metadata: dict[str, Any] | None = await get_upload_job_metadata(job_id)

    if metadata is None:
        await Redis.redis.srem(user_upload_jobs_key(user.username), job_id)
        return JSONResponse({"message": "Upload job dismissed"})

    if metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    try:
        current: Progress = cast(Progress, await Redis.get(job_id, "Progress"))

    except KeyError:
        current = Progress(str(metadata.get("name") or "Upload"), "Upload failed", 0)

    current_message: str = str(current.message or "")
    terminal_status: str = resolve_upload_job_status(current_message, metadata)

    if terminal_status == "uploading":
        raise HTTPException(status_code=409, detail="Cancel the upload before dismissing it")

    await Redis.set_json(
        terminal_upload_job_key(job_id),
        {
            "job_id": job_id,
            "username": user.username,
            "name": str(metadata.get("name") or current.name or "Upload"),
            "status": terminal_status,
            "message": current_message,
        },
        ex=24 * 60 * 60,
    )
    await Redis.redis.srem(user_upload_jobs_key(user.username), job_id)
    await Redis.delete_key(upload_job_key(job_id))
    await Redis.delete(job_id, "Progress")
    return JSONResponse({"message": "Upload job dismissed"})


@router.post("/upload-jobs/{job_id}/fail")
async def fail_upload_job(job_id: str, message: str = Body(..., embed=True, max_length=1000), user: User = Depends(get_current_user)) -> JSONResponse:
    """Publish a browser-side file-transfer failure to every device on the account."""

    metadata: dict[str, Any] | None = await get_upload_job_metadata(job_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    if metadata.get("kind") != "file":
        raise HTTPException(status_code=400, detail="Only browser file uploads can report transfer failures")

    try:
        current: Progress = cast(Progress, await Redis.get(job_id, "Progress"))

    except KeyError:
        raise HTTPException(status_code=404, detail="Upload job not found")

    lowered: str = current.message.lower()

    if metadata.get("cancelled") or lowered.startswith("upload cancelled"):
        return JSONResponse({"job_id": job_id, "status": "cancelled"})

    if "complete" in lowered:
        return JSONResponse({"job_id": job_id, "status": "completed"})

    detail = " ".join(sanitize_sensitive_urls(message).split())[:300] or "Browser file transfer failed"
    metadata["failed"] = True
    metadata["failed_at"] = time()
    await Redis.set_json(upload_job_key(job_id), metadata)
    failure = Progress(str(metadata.get("name") or current.name), f"Upload failed: {detail}", current.total)
    failure.transfer = current.transfer
    await Redis.set(job_id, failure)
    cancel_upload_task(job_id)
    await Redis.delete_key(pending_upload_key(job_id))
    return JSONResponse({"job_id": job_id, "status": "error"})


@router.post("/upload-jobs/{job_id}/cancel")
async def cancel_upload_job(job_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Cancel an upload from any signed-in device and publish the shared terminal status."""

    status = await cancel_upload_job_state(job_id, user.username)
    return JSONResponse({"job_id": job_id, "status": status})


@public_router.put("/upload/{job_id}/chunk")
async def upload_chunk(
        job_id: str,
        request: Request,
        offset: int = Query(..., ge=0),
        total: int = Query(..., ge=0),
        user: User = Depends(get_current_user),
) -> JSONResponse:
    """Write an uploaded byte range and update the Redis range metadata."""

    metadata: dict[str, Any] | None = await get_upload_job_metadata(job_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    if metadata.get("cancelled"):
        raise HTTPException(status_code=409, detail="Upload was cancelled")

    if metadata.get("failed"):
        raise HTTPException(status_code=409, detail="Upload has already failed")

    session_key: str = upload_session_key(job_id)
    state: Any = await Redis.get_json(session_key)

    if not isinstance(state, dict):
        pending: Any = await Redis.get_json(pending_upload_key(job_id))

        if not isinstance(pending, dict):
            raise HTTPException(status_code=404, detail="Upload job not found")

        pending_file_data: Any = pending.get("file")

        if not isinstance(pending_file_data, dict):
            raise HTTPException(status_code=404, detail="Upload job metadata is invalid")

        pending_file: File = File.model_validate(pending_file_data)

        if pending_file.username != user.username:
            raise HTTPException(status_code=403, detail="Access denied")

        raw_file_path: Any = pending.get("file_path")

        if not isinstance(raw_file_path, str) or not raw_file_path:
            raise HTTPException(status_code=404, detail="Upload file path is missing")

        file_path: Path = Path(raw_file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=True)
        metadata["total"] = total
        await Redis.set_json(upload_job_key(job_id), metadata)
        new_state: dict[str, Any] = {
            "file": pending_file.model_dump(mode="json"),
            "file_path": str(file_path),
            "username": user.username,
            "total": total,
            "ranges": [],
            "contiguous": 0,
            "received_chunk": False,
            "last_activity": time(),
        }
        created: bool = await Redis.set_json(session_key, new_state, ex=60 * 60, nx=True)

        if created:
            state = new_state
            await Redis.delete_key(pending_upload_key(job_id))
            await Redis.set(job_id, Progress(pending_file.name, "Receiving and uploading", 0))
            producer_task: Task[None] = create_task(wait_for_all_chunks(job_id, pending_file))
            upload_task: Task[None] = create_task(run_chunk_upload_job(job_id, pending_file, file_path, producer_task))
            track_upload_task(upload_task, job_id)

        else:
            state = await Redis.get_json(session_key)

        if not isinstance(state, dict):
            raise HTTPException(status_code=404, detail="Upload session could not be created")

    if str(state.get("username", "")) != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    if int(state.get("total", -1)) != total:
        raise HTTPException(status_code=409, detail="File size changed during upload")

    file_path: Path = Path(str(state.get("file_path", "")))

    if not file_path.is_file():
        file_state: Any = state.get("file")
        fallback_name: str = (str(file_state.get("name", "upload")) if isinstance(file_state, dict) else "upload")
        await Redis.delete_key(session_key)
        await Redis.set(job_id, Progress(fallback_name, "Upload failed: temporary file is missing; please restart the upload", 0))
        raise HTTPException(status_code=409, detail="Upload was interrupted and its temporary file is missing. Please restart the upload.")

    if offset > total:
        raise HTTPException(status_code=416, detail="Chunk offset exceeds declared file size")

    position: int = offset

    async with aiofiles.open(file_path, "r+b") as buffer:
        await buffer.seek(offset)

        async for chunk in request.stream():
            if not chunk:
                continue

            if position + len(chunk) > total:
                raise HTTPException(status_code=413, detail="Chunk exceeds declared file size")

            await buffer.write(chunk)
            await buffer.flush()
            position += len(chunk)

    latest_metadata = await get_upload_job_metadata(job_id)

    if latest_metadata is None or latest_metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    if latest_metadata.get("cancelled"):
        raise HTTPException(status_code=409, detail="Upload was cancelled")

    if latest_metadata.get("failed"):
        raise HTTPException(status_code=409, detail="Upload has already failed")

    contiguous: int = await record_written_range(job_id, offset, position)
    return JSONResponse({"job_id": job_id, "contiguous": contiguous})


@router.get("/torrent-preview")
async def torrent_preview(link: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Return torrent file names and sizes without downloading their payload."""
    raw_link: str = link.strip()
    parsed_link: SplitResult = urlsplit(raw_link)
    query: dict[str, list[str]] = {cast(str, key.lower()): cast(list[str], values) for key, values in parse_qs(parsed_link.query).items()}
    has_info_hash: bool = any(topic.lower().startswith(("urn:btih:", "urn:btmh:")) for topic in query.get("xt", []))

    if parsed_link.scheme.lower() != "magnet" or not raw_link.lower().startswith("magnet:?") or not has_info_hash:
        raise HTTPException(status_code=400, detail="Invalid torrent magnet URI")

    try:
        preview: dict[str, Any] = await preview_torrent_files(raw_link)

    except TimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from error

    except (ValueError, OSError, RuntimeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return JSONResponse(preview)


@router.post("/upload-link")
async def upload_link(
        link: str,
        data_center: str,
        directory: str,
        batch_id: str = "",
        selected_files: str = "",
        user: User = Depends(get_current_user),
) -> JSONResponse:
    """Start a background job that imports an HTTP(S), YouTube, or torrent magnet link."""

    raw_link: str = link.strip()
    parsed_link: SplitResult = urlsplit(raw_link)
    is_magnet: bool = parsed_link.scheme.lower() == "magnet"

    if is_magnet:
        query: dict[str, list[str]] = {cast(str, key.lower()): cast(list[str], values) for key, values in parse_qs(parsed_link.query).items()}
        exact_topics: list[str] = query.get("xt", [])
        has_info_hash: bool = any(topic.lower().startswith(("urn:btih:", "urn:btmh:")) for topic in exact_topics)

        if not raw_link.lower().startswith("magnet:?") or not has_info_hash:
            raise HTTPException(status_code=400, detail="Invalid torrent magnet URI: missing a valid xt topic")

        link = raw_link
        raw_name: str = (query.get("dn") or [""])[0] or "Torrent"
        try:
            selected_torrent_files: set[int] | None = {int(index) for index in json.loads(selected_files)} if selected_files else None

        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=400, detail="Invalid selected torrent file indexes") from error

        if selected_torrent_files is not None and (not selected_torrent_files or min(selected_torrent_files) < 0):
            raise HTTPException(status_code=400, detail="Select at least one valid torrent file")

    else:
        selected_torrent_files = None
        link = perform_validation(raw_link, "link")
        parsed_link = urlsplit(link)
        raw_name = unquote(Path(parsed_link.path).name).strip() or (parsed_link.hostname or "Link import")

    link_name: str = raw_name.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255] or "Link import"
    source_host: str = (parsed_link.hostname or "").lower().removeprefix("www.")
    is_youtube: bool = not is_magnet and (source_host == "youtu.be" or source_host == "youtube.com" or source_host.endswith(".youtube.com"))
    data_center = perform_validation(data_center, "data_center")

    if batch_id:
        batch = await get_upload_batch_metadata(batch_id)

        if batch is None or batch.get("username") != user.username:
            raise HTTPException(status_code=404, detail="Upload task not found")

        if batch.get("cancelled") or batch.get("status") in {"cancelled", "completed", "error"}:
            raise HTTPException(status_code=409, detail="Upload task is no longer active")

    directory_obj: Directory | None = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    file: File = File(
        directory_id=cast(int, directory_obj.id),
        name=link_name,
        type="application/octet-stream",
        size=0,
        modified_at=datetime.now(timezone.utc),
        links=[],
        data_center=data_center,
        username=user.username
    )
    job_id: str = str(uuid4())
    await Redis.set(job_id, Progress(file.name, "Starting", 0))
    await register_upload_job(
        job_id,
        username=user.username,
        name=link_name,
        kind="link",
        directory=str(directory_obj.path),
        data_center=data_center,
        batch_id=batch_id,
    )

    if batch_id:
        latest_batch = await get_upload_batch_metadata(batch_id)

        if (
                latest_batch is None
                or latest_batch.get("username") != user.username
                or latest_batch.get("cancelled") or latest_batch.get("status") in {"cancelled", "completed", "error"}
        ):
            await Redis.redis.srem(user_upload_jobs_key(user.username), job_id)
            await Redis.delete_key(upload_job_key(job_id))
            await Redis.delete(job_id, "Progress")
            raise HTTPException(status_code=409, detail="Upload task was cancelled or removed")

    async def run_upload_job() -> None:
        """Persist progress while the source URL is imported into storage."""

        progress: Progress

        try:
            async for progress in link_upload(file, link, selected_torrent_files):
                latest_metadata = await get_upload_job_metadata(job_id)

                if latest_metadata is None or latest_metadata.get("cancelled"):
                    await Redis.set(job_id, Progress(file.name, "Upload cancelled", 0))
                    return

                if is_youtube and batch_id:
                    candidate_name = str(progress.name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
                    original_name = link_name
                    normalized_candidate = candidate_name.casefold()
                    placeholder_names = {"", "link", "upload", "torrent", "watch", "shorts", "embed", "live", "video", "update_name_from_backend"}
                    is_video_id = len(candidate_name) == 11 and all(character.isalnum() or character in "_-" for character in candidate_name)
                    is_resolved_name = (
                            candidate_name
                            and normalized_candidate not in placeholder_names
                            and candidate_name != original_name
                            and not candidate_name.lower().startswith(("http://", "https://"))
                            and not is_video_id
                    )
                    latest_batch = await get_upload_batch_metadata(batch_id)

                    if latest_batch and latest_batch.get("username") == user.username:
                        batch_updates: dict[str, Any] = {}

                        if is_resolved_name:
                            if str(latest_batch.get("current_name") or "") != candidate_name:
                                batch_updates["current_name"] = candidate_name

                            batch_items: Any = latest_batch.get("items")

                            if isinstance(batch_items, list):
                                normalized_items: list[dict[str, Any]] = [
                                    dict(item) if isinstance(item, dict) else {"name": str(item)}
                                    for item in batch_items
                                ]
                                item_index: int = int(latest_batch.get("current_index") or 0)

                                if 0 <= item_index < len(normalized_items) and str(normalized_items[item_index].get("name") or "") != candidate_name:
                                    normalized_items[item_index]["name"] = candidate_name
                                    batch_updates["items"] = normalized_items

                        stage_message = str(progress.message or "").strip()

                        if (
                                stage_message.casefold() in {"downloading video", "downloading audio", "merging audio and video", "uploading file"}
                                and str(latest_batch.get("message") or "") != stage_message
                        ):
                            batch_updates["message"] = stage_message

                        if batch_updates:
                            await update_upload_batch(batch_id, user.username, **batch_updates)

                await Redis.set(job_id, progress)

            # The storage worker, not the browser's polling loop, owns successful completion.
            # This avoids leaving a finished link/torrent batch stuck at 95% when a proxy
            # request drops the child's final status or the browser's final PATCH.
            if batch_id:
                await reconcile_completed_link_batch(batch_id, user.username, job_id)

        except CancelledError:
            return

        except Exception as error:
            latest_metadata = await get_upload_job_metadata(job_id)

            if latest_metadata and latest_metadata.get("cancelled"):
                await Redis.set(job_id, Progress(file.name, "Upload cancelled", 0))
                return

            failure_detail: str = " ".join(sanitize_sensitive_urls(str(error)).split())[:400]
            failure_message: str = f"Upload failed: {failure_detail or type(error).__name__}"

            try:
                await Redis.set(job_id, Progress(file.name, failure_message, 0))

            except Exception as status_error:
                write_log(
                    "ERROR",
                    cast(type[DataCenter], DataCenter(file.data_center)),
                    "UPLOAD",
                    file.username,
                    f"Could not persist failed upload job {job_id} ({type(status_error).__name__})"
                )

    upload_task: Task[None] = create_task(run_upload_job())
    track_upload_task(upload_task, job_id)
    return JSONResponse({"job_id": job_id})
