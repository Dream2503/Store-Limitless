"""Upload initiation, chunk reception, and link-based upload endpoints."""

from __future__ import annotations

from asyncio import Task, create_task
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path
from time import time
from typing import Any, cast
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.database.models import Directory, File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import get_current_user
from backend.server.utils import perform_validation
from core.config import TRANSFER_PATH
from core.transfer import link_upload
from core.utils.progress import Progress
from .upload_state import (
    pending_upload_key,
    record_written_range,
    run_chunk_upload_job,
    track_upload_task,
    upload_session_key,
    wait_for_all_chunks,
)

router: APIRouter = APIRouter()
public_router: APIRouter = APIRouter()


@router.post("/upload")
async def upload_file(
        data_center: str = Header(..., alias="X-Data-Center"),
        directory: str = Header(..., alias="X-Directory"),
        file_name: str = Header(..., alias="X-File-Name"),
        user: User = Depends(get_current_user)
) -> JSONResponse:
    """Create an upload job and store its initial metadata in Redis."""

    data_center = perform_validation(data_center, "data_center")
    file_name = perform_validation(file_name, "file_name")
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
    return JSONResponse({"job_id": job_id})


@public_router.put("/upload/{job_id}/chunk")
async def upload_chunk(
        job_id: str,
        request: Request,
        offset: int = Query(..., ge=0),
        total: int = Query(..., ge=0),
        user: User = Depends(get_current_user)
) -> JSONResponse:
    """Write an uploaded byte range and update the Redis range metadata."""

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
            track_upload_task(upload_task)

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

    contiguous: int = await record_written_range(job_id, offset, position)
    return JSONResponse({"job_id": job_id, "contiguous": contiguous})


@router.post("/upload-link")
async def upload_link(link: str, data_center: str, directory: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Start a background job that imports a file from a URL."""

    link = perform_validation(link, "link")
    data_center = perform_validation(data_center, "data_center")
    directory_obj: Directory | None = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    file: File = File(
        directory_id=cast(int, directory_obj.id),
        name="update_name_from_backend",
        type="application/octet-stream",
        size=0,
        modified_at=datetime.now(timezone.utc),
        links=[],
        data_center=data_center,
        username=user.username
    )
    job_id: str = str(uuid4())
    await Redis.set(job_id, Progress(file.name, "Starting", 0))

    async def run_upload_job() -> None:
        """Persist progress while the source URL is imported into storage."""

        progress: Progress

        async for progress in link_upload(file, link):
            await Redis.set(job_id, progress)

    create_task(run_upload_job())
    return JSONResponse({"job_id": job_id})
