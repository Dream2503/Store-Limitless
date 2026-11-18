from asyncio import Event, Task, sleep
from asyncio import create_task
from dataclasses import dataclass, field
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path
from time import monotonic
from typing import cast
from urllib.parse import quote
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request, Header, Query
from fastapi.responses import Response, StreamingResponse, JSONResponse

from backend.database.models import Directory, File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import create_access_token, get_current_user
from backend.server.security import hash_password, verify_password, create_public_stream_token, verify_public_stream_token
from backend.server.utils import perform_validation
from core.config import TRANSFER_PATH
from core.stream import Stream
from core.transfer import link_upload, file_upload
from core.utils.discord_ import Discord
from core.utils.progress import Progress

auth: APIRouter = APIRouter(prefix="/auth")
public: APIRouter = APIRouter(prefix="/public")

PENDING_UPLOADS: dict[str, tuple[File, Path]] = {}
UPLOAD_IDLE_TIMEOUT: int = 120


@dataclass
class UploadSession:
    file: File
    file_path: Path
    total: int
    finished: Event
    ranges: list[tuple[int, int]] = field(default_factory=list)  # merged, written byte ranges
    contiguous: int = 0  # bytes written from 0 with no gaps
    last_activity: float = field(default_factory=monotonic)
    upload_task: Task[None] | None = None
    job_task: Task[None] | None = None


UPLOAD_SESSIONS: dict[str, UploadSession] = {}


def mark_written(session: UploadSession, start: int, end: int) -> None:
    if end > start:
        session.ranges.append((start, end))
        session.ranges.sort()
        merged: list[tuple[int, int]] = []

        for range_start, range_end in session.ranges:
            if merged and range_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], range_end))

            else:
                merged.append((range_start, range_end))

        session.ranges = merged

    if session.ranges and session.ranges[0][0] == 0:
        session.contiguous = session.ranges[0][1]

    session.file.size = session.contiguous
    session.last_activity = monotonic()

    if session.contiguous >= session.total:
        session.finished.set()


async def wait_for_all_chunks(session: UploadSession) -> None:
    while not session.finished.is_set():
        if monotonic() - session.last_activity > UPLOAD_IDLE_TIMEOUT:
            raise TimeoutError("No data received from the browser")

        await sleep(0.25)


@public.get("/ready")
async def ready() -> JSONResponse:
    return JSONResponse({"ready": Discord.ready})


@auth.post("/register")
async def register(user: User) -> JSONResponse:
    user.verify()

    if await User.get(user.username):
        raise HTTPException(status_code=400, detail="Username already registered")

    user.password = hash_password(user.password)
    await user.save()
    return JSONResponse({"message": "User registered successfully"})


@auth.post("/login")
async def login(username: str, password: str) -> tuple[dict[str, str], dict[str, str], Directory]:
    await Redis.delete(f"user:{username}", "User")
    await Redis.delete(f"user:{username}:directories", "Directory")
    await Redis.delete(f"user:{username}:files", "File")

    user: User | None = await User.get(username)

    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return ({
                "message": "Login successful",
                "access_token": create_access_token(data={"sub": user.username}),
                "token_type": "bearer",
            }, {
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
            }, await user.home)


@public.get("/upload/{job_id}/status")
async def upload_status(job_id: str) -> JSONResponse:
    progress: Progress | None = cast(Progress | None, await Redis.get(job_id, "Progress"))

    if progress is None:
        raise HTTPException(status_code=404, detail="Upload job not found")

    completed: bool = "complete" in progress.message.lower()
    file_id: int | None = None

    if completed and "id=" in progress.message:
        try:
            file_id = int(progress.message.split("id=")[-1].split()[0])

        except (ValueError, IndexError):
            file_id = None

    return JSONResponse({
        "status": "completed" if completed else "uploading",
        "message": " ".join(progress.message.split()[:2]) if completed else progress.message,
        "transfer": progress.transfer,
        "total": progress.total,
        "file_id": file_id,
    })


@auth.post("/upload")
async def upload_file(
        data_center: str = Header(..., alias="X-Data-Center"),
        directory: str = Header(..., alias="X-Directory"),
        file_name: str = Header(..., alias="X-File-Name"),
        user: User = Depends(get_current_user)
) -> JSONResponse:
    data_center = perform_validation(data_center, "data_center")
    file_name = perform_validation(file_name, "file_name")
    directory_obj: Directory | None = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    file_path: Path = TRANSFER_PATH / user.username / file_name
    file_path.parent.mkdir(parents=True, exist_ok=True)

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

    job_id: str = str(uuid4())
    PENDING_UPLOADS[job_id] = (file, file_path)

    await Redis.set(job_id, Progress(file_name, "Waiting for upload", 0))

    return JSONResponse({"job_id": job_id})


@public.put("/upload/{job_id}/chunk")
async def upload_chunk(
        job_id: str,
        request: Request,
        offset: int = Query(..., ge=0),
        total: int = Query(..., ge=0),
        user: User = Depends(get_current_user)
) -> JSONResponse:
    session: UploadSession | None = UPLOAD_SESSIONS.get(job_id)

    if session is None:
        pending: tuple[File, Path] | None = PENDING_UPLOADS.get(job_id)

        if pending is None:
            raise HTTPException(status_code=404, detail="Upload job not found")

        file, file_path = pending

        if file.username != user.username:
            raise HTTPException(status_code=403, detail="Access denied")

        PENDING_UPLOADS.pop(job_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"")

        session: UploadSession = UploadSession(file=file, file_path=file_path, total=total, finished=Event())
        UPLOAD_SESSIONS[job_id] = session

        await Redis.set(job_id, Progress(file.name, "Receiving and uploading", 0))

        session.upload_task = create_task(wait_for_all_chunks(session))

        async def run_upload_job() -> None:
            try:
                async for progress in file_upload(session.file, session.file_path, session.upload_task):
                    await Redis.set(job_id, progress)

            except Exception as error:
                await Redis.set(job_id, Progress(session.file.name, f"Upload failed: {error}", 0))

            finally:
                if session.upload_task is not None and not session.upload_task.done():
                    session.upload_task.cancel()

                UPLOAD_SESSIONS.pop(job_id, None)

        session.job_task = create_task(run_upload_job())

    elif session.file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    if total != session.total:
        raise HTTPException(status_code=409, detail="File size changed during upload")

    position = offset

    async with aiofiles.open(session.file_path, "r+b") as buffer:
        await buffer.seek(offset)

        async for chunk in request.stream():
            if not chunk:
                continue

            if position + len(chunk) > session.total:
                raise HTTPException(status_code=413, detail="Chunk exceeds declared file size")

            await buffer.write(chunk)
            await buffer.flush()

            mark_written(session, position, position + len(chunk))
            position += len(chunk)

    mark_written(session, offset, position)

    return JSONResponse({"job_id": job_id, "contiguous": session.contiguous})


@auth.post("/upload-link")
async def upload_link(link: str, data_center: str, directory: str, user: User = Depends(get_current_user)) -> JSONResponse:
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
        progress: Progress

        async for progress in link_upload(file, link):
            await Redis.set(job_id, progress)

    create_task(run_upload_job())
    return JSONResponse({"job_id": job_id})


@auth.post("/create-folder")
async def create_folder(directory: str, name: str, user: User = Depends(get_current_user)) -> JSONResponse:
    folder: Directory = Directory(
        path=Path(perform_validation(directory, "directory")) / perform_validation(name, "file_name"),
        modified_at=datetime.now(timezone.utc),
        username=user.username,
    )
    await folder.save()
    return JSONResponse({
        "message": "Folder created successfully",
        "directory": str(folder),
    })


@auth.get("/directory")
async def get_directory(directory: str, user: User = Depends(get_current_user)) -> tuple[list[Directory], list[File]]:
    directory_obj: Directory | None = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    return await Directory.get_all(user.username, Path(directory)), await File.get_all(user.username, directory_obj.id)


@auth.get("/trash")
async def get_trash(user: User = Depends(get_current_user)) -> tuple[list[Directory], list[File]]:
    await File.purge_expired(user.username)
    return await Directory.get_all(user.username, trash=True), await File.get_all(user.username, trash=True)


@auth.delete("/file/{fid}")
async def file_delete(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    file: File | None = await File.get(user.username, fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    await file.move_to_trash()
    return JSONResponse({"message": "File moved to trash"})


@auth.delete("/directory/{did}")
async def directory_delete(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = await Directory.get(user.username, did=did)

    if directory is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    if directory.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    await directory.move_to_trash()
    return JSONResponse({"message": "Directory moved to trash"})


@auth.delete("/trash/file/{fid}")
async def permanently_delete(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    file: File | None = await File.get(user.username, fid=fid, trash=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    await file.delete()
    return JSONResponse({"message": "Permanently deleted"})


@auth.delete("/trash/directory/{did}")
async def permanently_delete_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = await Directory.get(user.username, did=did, trash=True)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    for file in await File.get_all(user.username, directory.id, True):
        await file.delete()

    for child in await Directory.get_all(user.username, directory.path, True):
        await permanently_delete_directory(cast(int, child.id), user)

    await directory.delete()
    return JSONResponse({"message": "Permanently deleted"})


@auth.post("/trash/file/{fid}/restore")
async def restore_file(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    file: File | None = await File.get(user.username, fid=fid, trash=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    await file.restore()
    return JSONResponse({"message": "Restored"})


@auth.post("/trash/directory/{did}/restore")
async def restore_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = await Directory.get(user.username, did=did, trash=True)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    await directory.restore()
    return JSONResponse({"message": "Restored"})


@auth.post("/file/{fid}/public-link")
async def create_public_link(fid: int, user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = await File.get(user.username, fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    return {"public_token": create_public_stream_token(file=file, username=file.username)}


@public.get("/stream/{token}")
async def public_stream(token: str, request: Request) -> Response:
    try:
        payload: dict[str, str | int] = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid download stream link")

    file: File | None = await File.get(cast(str, payload["username"]), fid=int(payload["file_id"]))

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found")

    range_header: str | None = request.headers.get("range")

    try:
        stream: Stream = Stream(file, range_header)

    except (ValueError, IndexError):
        size: int = file.size

        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{size}",
                "Accept-Ranges": "bytes",
            },
        )

    length: int = stream.byte_range.end - stream.byte_range.start + 1
    content_type: str = file.type or guess_type(file.name)[0] or "application/octet-stream"
    status_code: int = 206 if range_header else 200
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": content_type,
        "Content-Disposition": f"inline; filename=\"{file.name.encode('ascii', 'ignore').decode()}\"; filename*=UTF-8''{quote(file.name)}",
        "Cache-Control": "no-cache",
    }

    if status_code == 206:
        headers["Content-Range"] = f"bytes {stream.byte_range.start}-{stream.byte_range.end}/{file.size}"

    return StreamingResponse(stream.stream(), status_code=status_code, headers=headers, media_type=content_type)


@public.get("/download/{token}")
async def public_download(token: str) -> StreamingResponse:
    try:
        payload: dict[str, str | int] = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid public download link")

    file: File | None = await File.get(cast(str, payload["username"]), fid=int(payload["file_id"]))

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        stream: Stream = Stream(file)

    except (ValueError, IndexError) as e:
        raise HTTPException(status_code=502, detail=f"File metadata failure: {e}") from e

    size: int = file.size
    content_type: str = file.type or guess_type(file.name)[0] or "application/octet-stream"
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "Content-Type": content_type,
        "Content-Disposition": f'attachment; filename="{file.name.encode("ascii", "ignore").decode() or "download"}"; filename*=UTF-8\'\'{quote(file.name)}',
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(stream.stream(), status_code=200, headers=headers, media_type=content_type)
