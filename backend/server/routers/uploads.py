"""Upload initiation, chunk reception, and link-based upload endpoints."""

from __future__ import annotations

import json
from asyncio import CancelledError, Task, create_task
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from time import time
from typing import cast
from urllib.parse import SplitResult, parse_qs, unquote, urlsplit
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from backend.database.models import Directory, File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import get_current_user
from backend.server.security import resolve_public_share_id, verify_public_stream_token
from backend.server.utils import perform_validation
from core.config import TRANSFER_PATH
from core.data_center import DataCenter, Database
from core.transfer import link_upload
from core.utils.telegram_link import resolve_telegram_message_link
from core.utils import get_file_mime_type, sanitize_sensitive_urls, write_log
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
    update_upload_batch_item_progress,
    upload_batch_key,
    upload_batch_progress_key,
    touch_upload_activity,
    upload_chunk_progress_key,
    user_upload_batches_key,
    upload_job_key,
    upload_session_key,
    user_upload_jobs_key,
    wait_for_all_chunks,
)

router: APIRouter = APIRouter()
public_router: APIRouter = APIRouter()


def _storelimitless_share_token(raw_link: str) -> str | None:
    """Extract a token from a StoreLimitless short share, legacy view, or public stream URL."""

    try:
        parsed = urlsplit(raw_link)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) == 2 and segments[0] in {"s", "view"}:
        return unquote(segments[1])

    if len(segments) == 3 and segments[0] == "public" and segments[1] in {"stream", "download"}:
        return unquote(segments[2])

    return None


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
        try:
            active_item_index = int(batch.get("current_index") or 0)
        except (TypeError, ValueError):
            active_item_index = 0
        batch_items = batch.get("items")
        if isinstance(batch_items, list) and 0 <= active_item_index < len(batch_items):
            active_item = batch_items[active_item_index]
            if isinstance(active_item, dict) and active_item.get("status") in {"cancelled", "completed", "error", "skipped"}:
                raise HTTPException(status_code=409, detail="This upload queue item is already terminal")

    directory_obj: Directory | None = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    job_id: str = str(uuid4())
    file_path: Path = TRANSFER_PATH / user.username / job_id / file_name
    file: File = File(
        directory_id=cast("int", directory_obj.id),
        name=file_name,
        type=get_file_mime_type(file_name),
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
        if latest_batch is None or latest_batch.get("username") != user.username:
            await cancel_upload_job_state(job_id, user.username)
            raise HTTPException(status_code=409, detail="Upload queue is no longer available")
        try:
            registered_item_index = int(latest_batch.get("current_index") or 0)
        except (TypeError, ValueError):
            registered_item_index = 0
        latest_items = latest_batch.get("items")
        if isinstance(latest_items, list) and 0 <= registered_item_index < len(latest_items):
            registered_item = latest_items[registered_item_index]
            if isinstance(registered_item, dict) and registered_item.get("status") in {"cancelled", "completed", "error", "skipped"}:
                await cancel_upload_job_state(job_id, user.username)
                raise HTTPException(status_code=409, detail="This upload queue item was cancelled before it started")

    write_log(
        "INFO",
        Database,
        "UPLOAD JOB",
        user.username,
        f"Registered browser upload job_id={job_id} name={file_name!r} data_center={data_center} "
        f"directory={directory_obj.path} expected_bytes={file_size} batch_id={batch_id or '-'}",
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
async def create_upload_batch(payload: dict[str, object] = Body(...), user: User = Depends(get_current_user)) -> JSONResponse:
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

    items: list[dict[str, object]] = []
    known_directories: dict[str, Directory] = {str(directory_obj.path): directory_obj}

    for item in raw_items:
        if isinstance(item, str):
            item_name = item.strip()
            item_size = 0
            item_directory = str(directory_obj.path)
            item_data_center = data_center

        elif isinstance(item, dict):
            item_name = str(item.get("name") or "Upload item").strip()[:500]

            try:
                item_size = max(0, int(item.get("size") or 0))
            except (TypeError, ValueError):
                item_size = 0

            item_directory = perform_validation(
                str(item.get("directory") or directory_obj.path),
                "directory",
            )
            item_data_center = perform_validation(
                str(item.get("data_center") or data_center),
                "data_center",
            )
        else:
            raise HTTPException(status_code=422, detail="Invalid upload batch item")

        if not item_name:
            item_name = "Upload item"

        destination = known_directories.get(item_directory)
        if destination is None:
            destination = await Directory.get(user.username, path=Path(item_directory))
            if destination is None:
                raise HTTPException(status_code=404, detail=f"Destination directory not found: {item_directory}")
            known_directories[item_directory] = destination

        items.append({
            "name": item_name,
            "size": item_size,
            "directory": str(destination.path),
            "data_center": item_data_center,
        })

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


@router.post("/upload-batches/{batch_id}/append")
async def append_upload_batch(
    batch_id: str,
    payload: dict[str, object] = Body(...),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Append newly selected browser files to the user's active sequential queue."""

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 2000:
        raise HTTPException(status_code=422, detail="Append between 1 and 2000 upload items")

    lock = Redis.redis.lock(f"upload:batch-lock:{batch_id}", timeout=60, blocking_timeout=30)
    async with lock:
        metadata = await get_upload_batch_metadata(batch_id)
        if metadata is None or metadata.get("username") != user.username:
            raise HTTPException(status_code=404, detail="Upload queue not found")

        if metadata.get("kind") != "file" or not metadata.get("appendable"):
            raise HTTPException(status_code=409, detail="This upload queue does not accept additional files")

        if metadata.get("cancelled") or metadata.get("status") in {"completed", "error", "cancelled"}:
            raise HTTPException(status_code=409, detail="This upload queue has already finished")

        existing_items = [
            dict(item) if isinstance(item, dict) else {"name": str(item)}
            for item in metadata.get("items", [])
        ]
        if len(existing_items) + len(raw_items) > 2000:
            raise HTTPException(status_code=422, detail="An upload queue cannot contain more than 2000 files")

        appended_items: list[dict[str, object]] = []
        directories = {str(value) for value in metadata.get("directories", []) if str(value)}
        if metadata.get("directory"):
            directories.add(str(metadata["directory"]))
        known_directories: dict[str, Directory] = {}

        for raw_item in raw_items:
            if isinstance(raw_item, str):
                item_name = raw_item.strip()[:500]
                item_size = 0
                item_directory = str(metadata.get("directory") or "/home")
                item_data_center = str(metadata.get("data_center") or "Discord")
            elif isinstance(raw_item, dict):
                item_name = str(raw_item.get("name") or "Upload item").strip()[:500]
                try:
                    item_size = max(0, int(raw_item.get("size") or 0))
                except (TypeError, ValueError):
                    item_size = 0
                item_directory = perform_validation(
                    str(raw_item.get("directory") or metadata.get("directory") or "/home"),
                    "directory",
                )
                item_data_center = perform_validation(
                    str(raw_item.get("data_center") or metadata.get("data_center") or "Discord"),
                    "data_center",
                )
            else:
                raise HTTPException(status_code=422, detail="Invalid upload queue item")

            if not item_name:
                item_name = "Upload item"

            destination = known_directories.get(item_directory)
            if destination is None:
                destination = await Directory.get(user.username, path=Path(item_directory))
                if destination is None:
                    raise HTTPException(status_code=404, detail=f"Destination directory not found: {item_directory}")
                known_directories[item_directory] = destination

            directories.add(str(destination.path))
            appended_items.append({
                "name": item_name,
                "size": item_size,
                "directory": str(destination.path),
                "data_center": item_data_center,
                "status": "queued",
                "message": "Queued",
            })

        metadata["items"] = existing_items + appended_items
        metadata["total_items"] = len(metadata["items"])
        metadata["directories"] = sorted(directories)
        metadata["message"] = f"{metadata['total_items']} files in upload queue"
        await Redis.set_json(upload_batch_key(batch_id), metadata)

    return JSONResponse({
        "batch_id": batch_id,
        "status": str(metadata.get("status") or "queued"),
        "total_items": int(metadata.get("total_items") or 0),
        "appended_items": len(appended_items),
    })


@router.get("/upload-batches")
async def get_upload_batches(user: User = Depends(get_current_user)) -> JSONResponse:
    """List whole upload tasks, including queued items and completed task history."""

    return JSONResponse({"batches": await list_user_upload_batches(user.username)})


@router.patch("/upload-batches/{batch_id}")
async def patch_upload_batch(batch_id: str, payload: dict[str, object] = Body(...), user: User = Depends(get_current_user)) -> JSONResponse:
    """Publish the queue's current item and task-level status."""

    metadata = await get_upload_batch_metadata(batch_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload task not found")

    if metadata.get("status") == "completed":
        # Completion is terminal. A late progress snapshot from the browser must not
        # replace completed per-file states with stale "downloading" values.
        return JSONResponse({"batch_id": batch_id, "status": "completed"})

    # Browser byte-progress events update one item at a time. Keep these writes outside
    # the batch JSON document so a large appendable queue is not re-serialized per event.
    sparse_progress_fields = {
        "item_index", "item_status", "item_transfer", "item_total", "item_progress", "item_message",
    }
    if (
            "item_index" in payload
            and "item_status" in payload
            and "item_transfer" in payload
            and set(payload).issubset(sparse_progress_fields)
    ):
        if metadata.get("kind") != "file":
            raise HTTPException(status_code=422, detail="Sparse progress updates are only supported for browser file queues")

        if str(payload.get("item_status") or "") != "uploading":
            raise HTTPException(status_code=422, detail="Sparse progress updates must target an uploading item")

        try:
            item_index = int(payload["item_index"])
            item_transfer = max(0, int(payload.get("item_transfer") or 0))
            item_total = max(0, int(payload.get("item_total") or 0))
            item_progress = max(0.0, min(100.0, float(payload.get("item_progress") or 0)))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid sparse upload progress")

        items = metadata.get("items")
        if not isinstance(items, list) or not 0 <= item_index < len(items):
            raise HTTPException(status_code=422, detail="Invalid upload item index")

        try:
            updated = await update_upload_batch_item_progress(
                batch_id,
                user.username,
                item_index,
                transfer=item_transfer,
                total=item_total,
                progress=item_progress,
                message=str(payload.get("item_message") or "Uploading"),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Upload task not found")

        return JSONResponse({"batch_id": batch_id, "status": str(updated.get("status") or "queued")})

    status = str(payload.get("status") or metadata.get("status") or "queued")

    if status not in {"queued", "uploading", "paused", "completed", "error", "cancelled"}:
        raise HTTPException(status_code=422, detail="Invalid upload task status")

    # Browser progress events can arrive after the user pauses. Preserve the
    # authoritative pause state while still accepting byte and item progress.
    if metadata.get("paused") and status == "uploading":
        status = "paused"

    updates: dict[str, object] = {"status": status}

    if "message" in payload and not (metadata.get("paused") and status == "paused"):
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
                **({"directory": perform_validation(str(item["directory"]), "directory")} if item.get("directory") else {}),
                **({"data_center": perform_validation(str(item["data_center"]), "data_center")} if item.get("data_center") else {}),
                **({"index": int(item["index"])} if "index" in item else {}),
                **({"path": str(item["path"])[:1000]} if "path" in item else {}),
                **({"torrent_link": str(item["torrent_link"])} if "torrent_link" in item else {}),
                **({"torrent_index": int(item["torrent_index"])} if "torrent_index" in item else {}),
                **({"file_index": int(item["file_index"])} if "file_index" in item else {}),
                **({"transfer": max(0, int(item.get("transfer") or 0))} if "transfer" in item else {}),
                **({"total": max(0, int(item.get("total") or 0))} if "total" in item else {}),
                **({"progress": max(0.0, min(100.0, float(item.get("progress") or 0)))} if "progress" in item else {}),
                **({"download_transfer": max(0, int(item.get("download_transfer") or 0))} if "download_transfer" in item else {}),
                **({"download_total": max(0, int(item.get("download_total") or 0))} if "download_total" in item else {}),
                **({"download_speed": max(0.0, float(item.get("download_speed") or 0))} if "download_speed" in item else {}),
                **({"download_eta": None if item.get("download_eta") is None else max(0.0, float(item.get("download_eta") or 0))} if "download_eta" in item else {}),
                **({"upload_transfer": max(0, int(item.get("upload_transfer") or 0))} if "upload_transfer" in item else {}),
                **({"upload_total": max(0, int(item.get("upload_total") or 0))} if "upload_total" in item else {}),
                **({"upload_speed": max(0.0, float(item.get("upload_speed") or 0))} if "upload_speed" in item else {}),
                **({"upload_eta": None if item.get("upload_eta") is None else max(0.0, float(item.get("upload_eta") or 0))} if "upload_eta" in item else {}),
            }
            for item in items_payload if isinstance(item, dict)
        ]
        if metadata.get("appendable"):
            persisted_items = [
                dict(item) if isinstance(item, dict) else {"name": str(item)}
                for item in metadata.get("items", [])
            ]
            if len(updates["items"]) < len(persisted_items):
                updates["items"].extend(persisted_items[len(updates["items"]):])

        updates["total_items"] = len(updates["items"])
        updates["completed_items"] = sum(item["status"] == "completed" for item in updates["items"])

    if "current_name" in payload:
        updates["current_name"] = str(payload.get("current_name") or "")[:500]

    latest_item_count = len(updates.get("items", metadata.get("items", []))) if isinstance(
        updates.get("items", metadata.get("items", [])), list
    ) else max(0, int(metadata.get("total_items") or 0))

    if "current_index" in payload:
        updates["current_index"] = max(0, min(int(payload.get("current_index") or 0), latest_item_count))

    if "completed_items" in payload:
        updates["completed_items"] = max(0, min(int(payload.get("completed_items") or 0), latest_item_count))

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
            item_index = int(cast("str | int | float | bool", payload.get("item_index")))

        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid upload item index")

        item_status = str(payload.get("item_status") or "queued")

        if item_status not in {"queued", "uploading", "completed", "error", "cancelled", "skipped"}:
            raise HTTPException(status_code=422, detail="Invalid upload item status")

        items = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in metadata.get("items", [])]

        if 0 <= item_index < len(items):
            items[item_index]["status"] = item_status
            items[item_index]["message"] = str(payload.get("item_message") or item_status.title())[:300]

            if "item_transfer" in payload:
                try:
                    items[item_index]["transfer"] = max(0, int(payload.get("item_transfer") or 0))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail="Invalid upload item transfer")

            if "item_total" in payload:
                try:
                    items[item_index]["total"] = max(0, int(payload.get("item_total") or 0))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail="Invalid upload item total")

            if "item_progress" in payload:
                try:
                    item_progress = float(payload.get("item_progress") or 0)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail="Invalid upload item progress")
                updates_item_progress = max(0.0, min(100.0, item_progress))
                items[item_index]["progress"] = updates_item_progress

            if item_status == "completed":
                completed_size = max(
                    0,
                    int(items[item_index].get("size") or 0),
                    int(items[item_index].get("total") or 0),
                )
                items[item_index]["total"] = completed_size
                items[item_index]["transfer"] = completed_size
                items[item_index]["progress"] = 100.0

            updates["items"] = items

    if status == "error":
        # Prefer the latest item snapshot supplied by the tray's child-status
        # recovery. Falling back to persisted items also supports older clients.
        raw_items = updates.get("items", metadata.get("items", []))
        items = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in raw_items]
        active_error_assigned = False

        for item in items:
            item_status = str(item.get("status") or "queued")

            if item_status == "queued":
                item["status"] = "skipped"
                item["message"] = "Not started"

            elif item_status in {"downloading", "uploading"}:
                if not active_error_assigned:
                    item["status"] = "error"
                    item["message"] = updates.get("message", "Upload failed")
                    active_error_assigned = True

                else:
                    item["status"] = "skipped"
                    item["message"] = "Not started after failure"

        updates["items"] = items
        updates["total_items"] = len(items)
        updates["completed_items"] = sum(item.get("status") == "completed" for item in items)

    updated = await update_upload_batch(batch_id, user.username, **updates)
    return JSONResponse({"batch_id": batch_id, "status": str(updated.get("status") or "queued")})


@router.post("/upload-batches/{batch_id}/pause")
async def pause_upload_batch(
    batch_id: str,
    payload: dict[str, object] = Body(...),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Pause/resume a browser-managed upload batch."""

    metadata = await get_upload_batch_metadata(batch_id)
    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload task not found")

    if metadata.get("status") in {"completed", "error", "cancelled"}:
        return JSONResponse({"batch_id": batch_id, "status": str(metadata.get("status"))})

    paused = bool(payload.get("paused"))
    # Link/torrent jobs run entirely in backend generators. They cannot be
    # safely suspended at an arbitrary byte boundary, so don't pretend otherwise.
    if paused and metadata.get("kind") == "link":
        raise HTTPException(
            status_code=409,
            detail="Pause is currently supported for browser file uploads only. Link and torrent imports cannot be paused safely yet.",
        )

    current_job_id = str(metadata.get("current_job_id") or "")
    if paused and not current_job_id:
        raise HTTPException(status_code=409, detail="Upload has not started yet; wait until file transfer begins before pausing.")

    if current_job_id:
        # The child job drives both sides of this pipeline: browser chunks arrive
        # through wait_for_all_chunks while provider parts are sent by upload_growing_file.
        # Publish the pause flag here so both workers observe the same state.
        job_metadata = await get_upload_job_metadata(current_job_id)
        if job_metadata and job_metadata.get("username") == user.username:
            job_metadata["paused"] = paused
            await Redis.set_json(upload_job_key(current_job_id), job_metadata)

            try:
                progress: Progress = cast("Progress", await Redis.get(current_job_id, "Progress"))
                if paused:
                    metadata["paused_progress_message"] = str(progress.message or "")
                    metadata["paused_upload_speed"] = float(progress.upload_speed or 0)
                    metadata["paused_download_speed"] = float(progress.download_speed or 0)
                    progress.message = "Paused by you"
                    progress.upload_speed = 0
                    progress.download_speed = 0
                    await Redis.set(current_job_id, progress)
                elif progress.message == "Paused by you":
                    progress.message = str(metadata.get("paused_progress_message") or "Receiving and uploading")
                    progress.upload_speed = float(metadata.get("paused_upload_speed") or 0)
                    progress.download_speed = float(metadata.get("paused_download_speed") or 0)
                    await Redis.set(current_job_id, progress)
            except KeyError:
                pass

    metadata = await update_upload_batch(
        batch_id,
        user.username,
        paused=paused,
        status="paused" if paused else ("uploading" if metadata.get("current_job_id") else "queued"),
        message="Paused by you" if paused else "Resuming upload",
        paused_at=time() if paused else None,
    )
    return JSONResponse({"batch_id": batch_id, "status": str(metadata.get("status") or "queued"), "paused": bool(metadata.get("paused"))})


@router.post("/upload-batches/{batch_id}/cancel")
async def cancel_upload_batch(batch_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Cancel the active item and prevent any remaining queued items from starting."""

    metadata = await get_upload_batch_metadata(batch_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload task not found")

    if metadata.get("status") in {"completed", "error", "cancelled"}:
        return JSONResponse({"batch_id": batch_id, "status": str(metadata.get("status"))})

    metadata = await update_upload_batch(
        batch_id,
        user.username,
        cancelled=True,
        cancelled_at=time(),
        status="cancelled",
        message="Upload task cancelled",
    )
    if metadata.get("status") != "cancelled":
        return JSONResponse({"batch_id": batch_id, "status": str(metadata.get("status") or "queued")})

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
            child_progress: Progress = cast("Progress", await Redis.get(child_id, "Progress"))
            child_status = resolve_upload_job_status(str(child_progress.message or ""), child_meta)

        except KeyError:
            continue

        if child_status == "completed" and child_id == current_child:
            # The browser queue can be between a completed child and the combined
            # next-item transition PATCH. Preserve the just-finished file on cancel.
            completed_during_cancel = True
            break

        if child_status != "uploading":
            continue

        try:
            await cancel_upload_job_state(child_id, user.username)

        except HTTPException as error:
            if error.status_code != 409:
                raise

            try:
                finished_progress: Progress = cast("Progress", await Redis.get(child_id, "Progress"))
                finished_meta = await get_upload_job_metadata(child_id)
                completed_during_cancel = resolve_upload_job_status(str(finished_progress.message or ""), finished_meta or {}) == "completed"

            except KeyError:
                completed_during_cancel = False

        break

    metadata = await get_upload_batch_metadata(batch_id) or metadata
    item_states = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in metadata.get("items", [])]
    current_index = int(metadata.get("current_index") or 0)
    message = "Upload task cancelled"

    # Sparse byte progress is stored separately from the batch JSON. Copy the last
    # confirmed values into the terminal item so cancellation does not reset its tray progress.
    if 0 <= current_index < len(item_states):
        raw_progress = await Redis.redis.hget(upload_batch_progress_key(batch_id), str(current_index))
        if raw_progress:
            try:
                progress_state = json.loads(raw_progress)
                if isinstance(progress_state, dict):
                    for field in ("transfer", "total", "progress"):
                        if field in progress_state:
                            item_states[current_index][field] = progress_state[field]
            except (TypeError, ValueError):
                pass

    if completed_during_cancel and 0 <= current_index < len(item_states):
        current_item = item_states[current_index]
        current_item["status"] = "completed"
        current_item["message"] = "Completed while cancellation was requested"
        message = "Cancelled remaining items; the active item completed as cancellation arrived"

    for item in item_states:
        # A torrent row can be actively downloading without having entered the
        # uploading phase. Treat every nonterminal phase as cancelled so stale
        # download progress cannot remain visible after the parent is cancelled.
        if item.get("status") not in {"completed", "error", "failed", "cancelled", "skipped"}:
            item["status"] = "cancelled"
            item["message"] = "Cancelled or skipped"

    await update_upload_batch(
        batch_id,
        user.username,
        cancelled=True,
        status="cancelled",
        message=message,
        items=item_states,
        total_items=len(item_states),
        completed_items=sum(1 for item in item_states if item.get("status") == "completed"),
    )
    return JSONResponse({"batch_id": batch_id, "status": "cancelled"})


@router.post("/upload-batches/{batch_id}/items/{item_index}/cancel")
async def cancel_upload_batch_item(
        batch_id: str,
        item_index: int,
        user: User = Depends(get_current_user),
) -> JSONResponse:
    """Cancel only the active browser-file item and leave the remaining queue runnable."""

    metadata = await get_upload_batch_metadata(batch_id)
    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload task not found")
    if metadata.get("kind") != "file":
        raise HTTPException(status_code=409, detail="Per-file cancellation is only available for browser file queues")
    if metadata.get("cancelled") or metadata.get("status") in {"completed", "error", "cancelled"}:
        raise HTTPException(status_code=409, detail="The upload queue is no longer active")

    items = [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in metadata.get("items", [])]
    if not 0 <= item_index < len(items):
        raise HTTPException(status_code=404, detail="Upload queue item not found")
    if item_index != int(metadata.get("current_index") or 0):
        raise HTTPException(status_code=409, detail="Only the currently uploading file can be cancelled")

    item = items[item_index]
    item_status = str(item.get("status") or "queued")
    if item_status in {"completed", "error", "failed", "cancelled", "skipped"}:
        return JSONResponse({
            "batch_id": batch_id,
            "item_index": item_index,
            "item_status": item_status,
            "status": str(metadata.get("status") or "uploading"),
            "continued": True,
        })

    child_id = str(metadata.get("current_job_id") or "")
    child_progress: Progress | None = None
    child_metadata: dict[str, object] | None = None

    # The child can be registered a moment before the browser publishes current_job_id.
    # Recover that in-flight child from the batch's ordered child list during this window.
    candidate_child_ids = [child_id] if child_id else []
    candidate_child_ids.extend(
        str(value) for value in reversed(metadata.get("child_job_ids", []))
        if str(value) and str(value) not in candidate_child_ids
    )
    for candidate_id in candidate_child_ids:
        candidate_metadata = await get_upload_job_metadata(candidate_id)
        try:
            candidate_item_index = int(candidate_metadata.get("batch_item_index", -1)) if candidate_metadata else -1
        except (TypeError, ValueError):
            candidate_item_index = -1
        if (
                candidate_metadata
                and candidate_metadata.get("username") == user.username
                and str(candidate_metadata.get("batch_id") or "") == batch_id
                and candidate_item_index == item_index
        ):
            child_id = candidate_id
            child_metadata = candidate_metadata
            break

    if child_id:
        child_metadata = child_metadata or await get_upload_job_metadata(child_id)
        if (
                child_metadata is None
                or child_metadata.get("username") != user.username
                or str(child_metadata.get("batch_id") or "") != batch_id
                or int(child_metadata.get("batch_item_index", -1)) != item_index
        ):
            raise HTTPException(status_code=409, detail="The active upload does not match this queue item")

        try:
            child_progress = cast("Progress", await Redis.get(child_id, "Progress"))
        except KeyError:
            child_progress = None

        if child_progress is None:
            # A pending child has no transfer worker yet, but its registration can
            # still be cancelled safely and its staging metadata cleaned up.
            try:
                await cancel_upload_job_state(child_id, user.username)
            except HTTPException as error:
                if error.status_code != 409:
                    raise

        if child_progress is not None:
            child_status = resolve_upload_job_status(str(child_progress.message or ""), child_metadata)
            if child_status == "completed":
                completed_size = max(0, int(item.get("size") or item.get("total") or child_progress.total or 0))
                item.update({
                    "status": "completed",
                    "message": "Uploaded successfully",
                    "size": completed_size,
                    "total": completed_size,
                    "transfer": completed_size,
                    "progress": 100.0,
                    "download_transfer": completed_size,
                    "download_total": completed_size,
                    "upload_transfer": completed_size,
                    "upload_total": completed_size,
                })
                updated = await update_upload_batch(
                    batch_id,
                    user.username,
                    items=items,
                    completed_items=sum(entry.get("status") == "completed" for entry in items),
                )
                return JSONResponse({
                    "batch_id": batch_id,
                    "item_index": item_index,
                    "item_status": "completed",
                    "status": str(updated.get("status") or "uploading"),
                    "continued": True,
                })

            if child_status == "uploading":
                try:
                    await cancel_upload_job_state(child_id, user.username)
                except HTTPException as error:
                    if error.status_code != 409:
                        raise
                    latest_progress = await Redis.get(child_id, "Progress")
                    latest_metadata = await get_upload_job_metadata(child_id) or child_metadata
                    if resolve_upload_job_status(str(latest_progress.message or ""), latest_metadata or {}) == "completed":
                        completed_size = max(0, int(item.get("size") or item.get("total") or latest_progress.total or 0))
                        item.update({
                            "status": "completed",
                            "message": "Uploaded successfully",
                            "size": completed_size,
                            "total": completed_size,
                            "transfer": completed_size,
                            "progress": 100.0,
                            "download_transfer": completed_size,
                            "download_total": completed_size,
                            "upload_transfer": completed_size,
                            "upload_total": completed_size,
                        })
                        updated = await update_upload_batch(
                            batch_id,
                            user.username,
                            items=items,
                            completed_items=sum(entry.get("status") == "completed" for entry in items),
                        )
                        return JSONResponse({
                            "batch_id": batch_id,
                            "item_index": item_index,
                            "item_status": "completed",
                            "status": str(updated.get("status") or "uploading"),
                            "continued": True,
                        })
                    raise

    # Preserve the last confirmed byte counters on the cancelled card. These are
    # per-file values; the parent batch remains uploading so the browser can advance.
    if child_progress is not None:
        download_total = max(0, int(child_progress.download_total or item.get("size") or 0))
        download_transfer = max(0, int(child_progress.download_transfer or 0))
        upload_total = max(0, int(child_progress.total or item.get("size") or 0))
        upload_transfer = max(0, int(child_progress.transfer or 0))
        upload_transfer = min(upload_transfer, download_transfer)
        item.update({
            "download_transfer": download_transfer,
            "download_total": download_total,
            "upload_transfer": upload_transfer,
            "upload_total": upload_total,
        })

    item["status"] = "cancelled"
    item["message"] = "Cancelled by you"
    updated = await update_upload_batch(
        batch_id,
        user.username,
        items=items,
        completed_items=sum(entry.get("status") == "completed" for entry in items),
        message=f"Cancelled {str(item.get('name') or 'current file')}; continuing queue",
    )
    await Redis.redis.hdel(upload_batch_progress_key(batch_id), str(item_index))

    write_log(
        "INFO",
        Database,
        "UPLOAD QUEUE",
        user.username,
        f"Cancelled one file item batch_id={batch_id} item_index={item_index} child_job_id={child_id or '-'}; remaining items stay queued.",
    )
    return JSONResponse({
        "batch_id": batch_id,
        "item_index": item_index,
        "item_status": "cancelled",
        "status": str(updated.get("status") or "uploading"),
        "continued": True,
    })


async def _delete_upload_batch(batch_id: str, username: str, *, cancel_active: bool = False) -> None:
    """Delete a terminal batch and the child job records belonging to it."""

    metadata = await get_upload_batch_metadata(batch_id)

    if metadata is None:
        await Redis.redis.srem(user_upload_batches_key(username), batch_id)
        await Redis.delete_key(upload_batch_progress_key(batch_id))
        return

    if metadata.get("username") != username:
        raise HTTPException(status_code=404, detail="Upload task not found")

    child_ids = [str(value) for value in metadata.get("child_job_ids", [])]

    if metadata.get("status") in {"queued", "uploading", "paused"}:
        if not cancel_active:
            raise HTTPException(status_code=409, detail="Cancel the active upload task before removing it")

        metadata = await update_upload_batch(
            batch_id,
            username,
            cancelled=True,
            cancelled_at=time(),
            status="cancelled",
            message="Upload task cancelled and removed",
        )
        if metadata.get("status") != "cancelled":
            raise HTTPException(status_code=409, detail="Upload task is already finishing")

        child_ids = [str(value) for value in metadata.get("child_job_ids", [])]
        current_child = str(metadata.get("current_job_id") or "")

        if current_child and current_child not in child_ids:
            child_ids.append(current_child)

        for child_id in reversed(child_ids):
            child_meta = await get_upload_job_metadata(child_id)

            if not child_meta or child_meta.get("username") != username:
                continue

            try:
                child_progress: Progress = cast("Progress", await Redis.get(child_id, "Progress"))
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

        await update_upload_batch(
            batch_id,
            username,
            cancelled=True,
            status="cancelled",
            message="Upload task cancelled and removed",
        )

    for child_id in child_ids:

        child_meta = await get_upload_job_metadata(child_id)
        if child_meta and child_meta.get("username") == username:
            await cleanup_upload_artifacts(child_id)
            await Redis.redis.srem(user_upload_jobs_key(username), child_id)
            await Redis.delete_key(upload_job_key(child_id))
            await Redis.delete(child_id, "Progress")

    await Redis.redis.srem(user_upload_batches_key(username), batch_id)
    await Redis.delete_key(upload_batch_progress_key(batch_id))
    await Redis.delete_key(upload_batch_key(batch_id))


@router.delete("/upload-batches/{batch_id}")
async def dismiss_upload_batch(batch_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Remove one terminal task from the tray."""

    await _delete_upload_batch(batch_id, user.username)
    return JSONResponse({"message": "Upload task removed"})


@router.delete("/upload-batches")
async def clear_upload_batches(user: User = Depends(get_current_user)) -> JSONResponse:
    """Clear terminal tasks and terminal rows inside active multi-file batches."""

    terminal_statuses = {"completed", "error", "failed", "cancelled", "skipped"}
    batch_ids = await Redis.redis.smembers(user_upload_batches_key(user.username))
    removed_batch_ids: list[str] = []
    dismissed_items: dict[str, list[int]] = {}

    # Reconcile child-job results before deciding what is terminal. In particular,
    # a browser-file batch may still be uploading while earlier file rows completed
    # or failed and later files remain queued.
    await list_user_upload_batches(user.username)

    for raw_batch_id in list(batch_ids):
        batch_id = str(raw_batch_id)
        metadata = await get_upload_batch_metadata(batch_id)

        # Clean stale account-index entries, but never touch a batch owned by another user.
        if metadata is None:
            await _delete_upload_batch(batch_id, user.username)
            removed_batch_ids.append(batch_id)
            continue

        if metadata.get("username") != user.username:
            continue

        batch_status = str(metadata.get("status") or "queued").lower()
        if batch_status in terminal_statuses:
            await _delete_upload_batch(batch_id, user.username)
            removed_batch_ids.append(batch_id)
            continue

        raw_items = metadata.get("items")
        if not isinstance(raw_items, list):
            continue

        # Keep the original item indexes intact. The browser queue runner and its
        # child jobs address items by index, so removing rows from this list would
        # corrupt current_index and per-item cancellation/progress references.
        items = [
            dict(item) if isinstance(item, dict) else {"name": str(item)}
            for item in raw_items
        ]
        terminal_indexes: list[int] = []

        for item_index, item in enumerate(items):
            item_status = str(item.get("status") or "queued").lower()
            if item_status in terminal_statuses and not item.get("dismissed"):
                item["dismissed"] = True
                terminal_indexes.append(item_index)

        if terminal_indexes:
            await update_upload_batch(batch_id, user.username, items=items)
            await Redis.redis.hdel(
                upload_batch_progress_key(batch_id),
                *(str(index) for index in terminal_indexes),
            )
            dismissed_items[batch_id] = terminal_indexes

    return JSONResponse({
        "message": "Terminal upload history cleared",
        "removed_batch_ids": removed_batch_ids,
        "dismissed_items": dismissed_items,
    })


@router.get("/upload-jobs")
async def get_upload_jobs(user: User = Depends(get_current_user)) -> JSONResponse:
    """List upload jobs belonging to the authenticated username across all devices."""

    return JSONResponse({"jobs": await list_user_upload_jobs(user.username)})


@router.get("/upload-jobs/{job_id}/status")
async def get_upload_job_status(job_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Return one job's status, including a short-lived result after tray cleanup."""

    metadata: dict[str, object] | None = await get_upload_job_metadata(job_id)

    if metadata is not None:
        if metadata.get("username") != user.username:
            raise HTTPException(status_code=404, detail="Upload job not found")

        try:
            progress: Progress = cast("Progress", await Redis.get(job_id, "Progress"))

        except KeyError:
            progress = Progress(str(metadata.get("name") or "Upload"), "Upload status unavailable", 0)

        message: str = str(progress.message or "")
        return JSONResponse({
            "job_id": job_id,
            "name": str(metadata.get("name") or progress.name or "Upload"),
            "status": resolve_upload_job_status(message, metadata),
            "message": message,
            "transfer": max(0, int(progress.transfer or 0)),
            "total": max(0, int(progress.total or metadata.get("total") or 0)),
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
        })

    terminal: object = await Redis.get_json(terminal_upload_job_key(job_id))

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

    metadata: dict[str, object] | None = await get_upload_job_metadata(job_id)

    if metadata is None:
        await Redis.redis.srem(user_upload_jobs_key(user.username), job_id)
        return JSONResponse({"message": "Upload job dismissed"})

    if metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    try:
        current: Progress = cast("Progress", await Redis.get(job_id, "Progress"))

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

    metadata: dict[str, object] | None = await get_upload_job_metadata(job_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    if metadata.get("kind") != "file":
        raise HTTPException(status_code=400, detail="Only browser file uploads can report transfer failures")

    try:
        current: Progress = cast("Progress", await Redis.get(job_id, "Progress"))

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

    metadata: dict[str, object] | None = await get_upload_job_metadata(job_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    if metadata.get("cancelled"):
        raise HTTPException(status_code=409, detail="Upload was cancelled")

    if metadata.get("failed"):
        raise HTTPException(status_code=409, detail="Upload has already failed")

    if metadata.get("paused"):
        raise HTTPException(status_code=409, detail="Upload is paused")

    session_key: str = upload_session_key(job_id)
    state: object = await Redis.get_json(session_key)

    if not isinstance(state, dict):
        pending: object = await Redis.get_json(pending_upload_key(job_id))

        if not isinstance(pending, dict):
            raise HTTPException(status_code=404, detail="Upload job not found")

        pending_file_data: object = pending.get("file")

        if not isinstance(pending_file_data, dict):
            raise HTTPException(status_code=404, detail="Upload job metadata is invalid")

        pending_file: File = File.model_validate(pending_file_data)

        if pending_file.username != user.username:
            raise HTTPException(status_code=403, detail="Access denied")

        raw_file_path: object = pending.get("file_path")

        if not isinstance(raw_file_path, str) or not raw_file_path:
            raise HTTPException(status_code=404, detail="Upload file path is missing")

        file_path: Path = Path(raw_file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=True)
        metadata["total"] = total
        await Redis.set_json(upload_job_key(job_id), metadata)
        new_state: dict[str, object] = {
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
            initial_progress = Progress(pending_file.name, "Receiving and uploading", total)
            initial_progress.download_active = True
            initial_progress.download_total = total
            await Redis.set(job_id, initial_progress)
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
        file_state: object = state.get("file")
        fallback_name: str = (str(file_state.get("name", "upload")) if isinstance(file_state, dict) else "upload")
        await Redis.delete_key(session_key)
        await Redis.set(job_id, Progress(fallback_name, "Upload failed: temporary file is missing; please restart the upload", 0))
        raise HTTPException(status_code=409, detail="Upload was interrupted and its temporary file is missing. Please restart the upload.")

    if offset > total:
        raise HTTPException(status_code=416, detail="Chunk offset exceeds declared file size")

    position: int = offset
    last_activity_heartbeat: float = 0.0

    try:
        # Start the idle timer only once this authenticated chunk request is ready
        # to receive bytes. Refresh it while the body is actively streaming.
        await touch_upload_activity(job_id)
        last_activity_heartbeat = time()

        async with aiofiles.open(file_path, "r+b") as buffer:
            await buffer.seek(offset)

            async for chunk in request.stream():
                if not chunk:
                    continue

                if position + len(chunk) > total:
                    raise HTTPException(status_code=413, detail="Chunk exceeds declared file size")

                await buffer.write(chunk)
                position += len(chunk)

                # Publish the in-flight body offset independently of completed ranges.
                # GET /upload-batches can then reflect bytes arriving within a large
                # 8 MiB request instead of waiting for the whole request to finish.
                # This is display-only progress: record_written_range remains the sole
                # source of committed ranges used by the storage worker.
                await Redis.redis.hset(
                    upload_chunk_progress_key(job_id),
                    str(offset),
                    str(min(total, position)),
                )
                await Redis.redis.expire(upload_chunk_progress_key(job_id), 60 * 60)

                # A range is committed to Redis only after its complete request body
                # arrives. Large/slow 8 MiB requests must still count as activity while
                # their fragments are being written, or the producer's 120s idle timer
                # can incorrectly fail a healthy transfer.
                current_time = time()
                if current_time - last_activity_heartbeat >= 5:
                    await touch_upload_activity(job_id, current_time)
                    last_activity_heartbeat = current_time

            # Flush once after the complete HTTP range has arrived. The range is
            # not published to Redis until after this point, so the storage worker
            # never treats buffered, incomplete bytes as a completed chunk.
            await buffer.flush()
    except ClientDisconnect:
        # The browser intentionally aborts in-flight XHRs when Pause is pressed.
        # A partially written range is not recorded as complete, so resume safely
        # retransmits this chunk from its original offset.
        latest_metadata = await get_upload_job_metadata(job_id)
        detail = "Upload is paused." if latest_metadata and latest_metadata.get("paused") else "Chunk transfer was interrupted."
        return JSONResponse({"job_id": job_id, "detail": detail, "interrupted": True}, status_code=409)

    latest_metadata = await get_upload_job_metadata(job_id)

    if latest_metadata is None or latest_metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    if latest_metadata.get("cancelled"):
        raise HTTPException(status_code=409, detail="Upload was cancelled")

    if latest_metadata.get("failed"):
        raise HTTPException(status_code=409, detail="Upload has already failed")

    if latest_metadata.get("paused"):
        raise HTTPException(status_code=409, detail="Upload is paused")

    contiguous: int = await record_written_range(job_id, offset, position)
    return JSONResponse({"job_id": job_id, "contiguous": contiguous})


@router.get("/torrent-preview")
async def torrent_preview(link: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Return torrent file names and sizes without downloading their payload."""
    raw_link: str = link.strip()
    parsed_link: SplitResult = urlsplit(raw_link)
    query: dict[str, list[str]] = {cast("str", key.lower()): cast("list[str]", values) for key, values in parse_qs(parsed_link.query).items()}
    has_info_hash: bool = any(topic.lower().startswith(("urn:btih:", "urn:btmh:")) for topic in query.get("xt", []))

    if parsed_link.scheme.lower() != "magnet" or not raw_link.lower().startswith("magnet:?") or not has_info_hash:
        raise HTTPException(status_code=400, detail="Invalid torrent magnet URI")

    try:
        preview: dict[str, object] = await preview_torrent_files(raw_link)

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
    storelimitless_token = _storelimitless_share_token(raw_link)
    initial_hostname: str = (urlsplit(raw_link).hostname or "").lower()
    is_telegram_message_link: bool = initial_hostname in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
    parsed_link: SplitResult = urlsplit(raw_link)
    is_magnet: bool = parsed_link.scheme.lower() == "magnet"

    if is_magnet:
        query: dict[str, list[str]] = {cast("str", key.lower()): cast("list[str]", values) for key, values in parse_qs(parsed_link.query).items()}
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
        raw_name = "Telegram message" if is_telegram_message_link else (unquote(Path(parsed_link.path).name).strip() or (parsed_link.hostname or "Link import"))

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

    if storelimitless_token:
        # These links use an existing StoreLimitless storage record. Create a separate
        # recipient-owned database row while reusing the original provider message links.
        try:
            share_payload: dict[str, str | int] = verify_public_stream_token(storelimitless_token)
        except ValueError:
            share_payload = resolve_public_share_id(storelimitless_token) or {}

        if not share_payload:
            raise HTTPException(
                status_code=404,
                detail=(
                    "This StoreLimitless share was not found in this database. "
                    "Direct database copies work only for shares created by this StoreLimitless instance."
                ),
            )

        # Session-bound private links are not public shares and must not be imported.
        if share_payload.get("type") is not None:
            raise HTTPException(status_code=403, detail="Private StoreLimitless links cannot be imported as public shares")

        source_username = share_payload.get("username")
        source_file_id = share_payload.get("file_id")
        if (
            not isinstance(source_username, str)
            or not source_username
            or not isinstance(source_file_id, int)
            or isinstance(source_file_id, bool)
            or source_file_id <= 0
        ):
            raise HTTPException(status_code=404, detail="Invalid StoreLimitless share link")

        source_file = await File.get(source_username, fid=source_file_id)
        if source_file is None or source_file.username != source_username:
            raise HTTPException(status_code=404, detail="The shared StoreLimitless file no longer exists")

        copied_file = File(
            directory_id=cast("int", directory_obj.id),
            name=source_file.name,
            type=source_file.type,
            size=source_file.size,
            modified_at=source_file.modified_at,
            deleted_at=None,
            data_center=source_file.data_center,
            links=list(source_file.links),
            username=user.username,
        )
        await copied_file.save()

        job_id = str(uuid4())
        progress = Progress(copied_file.name, f"StoreLimitless copy complete (file id={copied_file.id})", 0)
        await Redis.set(job_id, progress)
        await register_upload_job(
            job_id,
            username=user.username,
            name=copied_file.name,
            kind="link",
            directory=str(directory_obj.path),
            data_center=copied_file.data_center,
            total=0,
            batch_id=batch_id,
        )
        write_log(
            "INFO",
            Database,
            "STORELIMITLESS COPY",
            user.username,
            f"Copied file metadata source_user={source_username!r} source_file_id={source_file_id} "
            f"new_file_id={copied_file.id} name={copied_file.name!r} shared_parts={len(copied_file.links)} "
            f"directory={directory_obj.path}",
        )
        return JSONResponse({
            "job_id": job_id,
            "import_type": "storelimitless-copy",
            "file_id": copied_file.id,
            "file_name": copied_file.name,
            "file_size": copied_file.size,
        })

    file: File = File(
        directory_id=cast("int", directory_obj.id),
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
    import_kind: str = (
        "torrent" if is_magnet else "telegram-message" if is_telegram_message_link
        else "youtube" if is_youtube else "http-link"
    )
    write_log(
        "INFO",
        Database,
        "UPLOAD JOB",
        user.username,
        f"Registered link import job_id={job_id} kind={import_kind} source_host={source_host or 'unknown'} "
        f"name={link_name!r} data_center={data_center} directory={directory_obj.path} "
        f"batch_id={batch_id or '-'}",
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

        write_log(
            "INFO",
            Database,
            "UPLOAD JOB",
            user.username,
            f"Background import started job_id={job_id} kind={import_kind} name={file.name!r}",
        )

        try:
            download_link: str = link

            if is_telegram_message_link:
                # Telegram processing happens inside this persistent job so the API request
                # can return immediately while the bot works. Never pass the original
                # Telegram message URL to the normal downloader.
                write_log("INFO", Database, "TELEGRAM IMPORT", user.username, f"Resolving message link job_id={job_id}")
                await Redis.set(job_id, Progress(file.name, "Processing Telegram message", 0))

                if batch_id:
                    await update_upload_batch(batch_id, user.username, message="Processing Telegram message")

                download_link, source_name = await resolve_telegram_message_link(link)
                resolved_name: str = source_name.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255] or "Telegram download"
                write_log(
                    "INFO",
                    Database,
                    "TELEGRAM IMPORT",
                    user.username,
                    f"Resolved message link job_id={job_id} filename={resolved_name!r} download_url_received={bool(download_link)}",
                )
                file.name = resolved_name
                file.type = get_file_mime_type(file.name)
                await Redis.set(job_id, Progress(file.name, "Starting download", 0))

                latest_metadata = await get_upload_job_metadata(job_id)

                if latest_metadata is not None and latest_metadata.get("username") == user.username:
                    latest_metadata["name"] = file.name
                    await Redis.set_json(upload_job_key(job_id), latest_metadata)

                if batch_id:
                    latest_batch = await get_upload_batch_metadata(batch_id)

                    if latest_batch and latest_batch.get("username") == user.username:
                        batch_updates: dict[str, object] = {"current_name": file.name, "message": "Downloading file"}
                        batch_items: object = latest_batch.get("items")

                        if isinstance(batch_items, list):
                            normalized_items: list[dict[str, object]] = [
                                dict(item) if isinstance(item, dict) else {"name": str(item)}
                                for item in batch_items
                            ]
                            item_index: int = int(latest_batch.get("current_index") or 0)

                            if 0 <= item_index < len(normalized_items):
                                normalized_items[item_index]["name"] = file.name
                                batch_updates["items"] = normalized_items

                        await update_upload_batch(batch_id, user.username, **batch_updates)

            latest_metadata = await get_upload_job_metadata(job_id)

            if latest_metadata is None or latest_metadata.get("cancelled"):
                await Redis.set(job_id, Progress(file.name, "Upload cancelled", 0))
                return

            # The task is cancelled while the async generator is paused at a
            # yielded progress update. Explicitly close the stream so the torrent
            # generator's finally block pauses/removes libtorrent and cancels uploads.
            async with aclosing(link_upload(file, download_link, selected_torrent_files)) as link_progress:
                async for progress in link_progress:
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
                            batch_updates: dict[str, object] = {}

                            if is_resolved_name:
                                if str(latest_batch.get("current_name") or "") != candidate_name:
                                    batch_updates["current_name"] = candidate_name

                                batch_items: object = latest_batch.get("items")

                                if isinstance(batch_items, list):
                                    normalized_items: list[dict[str, object]] = [
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

                    if is_telegram_message_link and batch_id:
                        stage_message: str = str(progress.message or "").strip().casefold()
                        display_stage: str | None = None

                        if stage_message in {"downloading and uploading file", "downloading file"}:
                            display_stage = "Downloading file"
                        elif stage_message.startswith("upload complete of id="):
                            display_stage = "Finalizing upload"

                        if display_stage:
                            latest_batch = await get_upload_batch_metadata(batch_id)

                            if (
                                    latest_batch
                                    and latest_batch.get("username") == user.username
                                    and str(latest_batch.get("message") or "") != display_stage
                            ):
                                await update_upload_batch(batch_id, user.username, message=display_stage)

                    await Redis.set(job_id, progress)

            # The storage worker, not the browser's polling loop, owns successful completion.
            # This avoids leaving a finished link/torrent batch stuck at 95% when a proxy
            # request drops the child's final status or the browser's final PATCH.
            write_log(
                "INFO",
                Database,
                "UPLOAD JOB",
                user.username,
                f"Transfer pipeline finished job_id={job_id}; background generator exhausted",
            )
            if batch_id:
                await reconcile_completed_link_batch(batch_id, user.username, job_id)

        except CancelledError:
            write_log("INFO", Database, "UPLOAD JOB", user.username, f"Background import cancelled job_id={job_id}")
            return

        except Exception as error:
            latest_metadata = await get_upload_job_metadata(job_id)

            if latest_metadata and latest_metadata.get("cancelled"):
                await Redis.set(job_id, Progress(file.name, "Upload cancelled", 0))
                return

            failure_detail: str = " ".join(sanitize_sensitive_urls(str(error)).split())[:400]
            failure_message: str = f"Upload failed: {failure_detail or type(error).__name__}"
            write_log(
                "ERROR",
                Database,
                "UPLOAD JOB",
                user.username,
                f"Background import failed job_id={job_id} kind={import_kind} "
                f"error={type(error).__name__}: {failure_detail or 'no error detail'}",
            )

            try:
                await Redis.set(job_id, Progress(file.name, failure_message, 0))

            except Exception as status_error:
                write_log(
                    "ERROR",
                    cast("type[DataCenter]", DataCenter(file.data_center)),
                    "UPLOAD",
                    file.username,
                    f"Could not persist failed upload job {job_id} ({type(status_error).__name__})"
                )

    upload_task: Task[None] = create_task(run_upload_job())
    track_upload_task(upload_task, job_id)
    return JSONResponse({"job_id": job_id})
