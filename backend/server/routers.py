from asyncio import Task, create_task
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path
from typing import cast
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import Response, StreamingResponse, JSONResponse

from backend.database.models import Directory, File, User
from backend.server.jwt_handler import create_access_token, get_current_user
from backend.server.security import hash_password, verify_password, create_public_stream_token, verify_public_stream_token
from backend.server.utils import perform_validation
from core.config import UPLOAD_JOBS, TRANSFER_PATH
from core.stream import Stream
from core.transfer import link_upload, file_upload
from core.utils import Progress

auth: APIRouter = APIRouter(prefix="/auth")
public: APIRouter = APIRouter(prefix="/public")


@auth.post("/register")
def register(user: User) -> JSONResponse:
    user.verify()

    if User.get(user.username):
        raise HTTPException(status_code=400, detail="Username already registered")

    user.password = hash_password(user.password)
    user.save()
    return JSONResponse({"message": "User registered successfully"})


@auth.post("/login")
def login(username: str, password: str) -> JSONResponse:
    user: User | None = User.get(username)

    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return JSONResponse({
        "message": "Login successful",
        "access_token": create_access_token(data={"sub": user.username}),
        "token_type": "bearer",
    })


@auth.get("/upload/{job_id}/status")
async def upload_status(job_id: str) -> JSONResponse:
    progress: Progress = UPLOAD_JOBS[perform_validation(job_id, "job_id")]
    return JSONResponse({
        "status": "completed" if "complete" in progress.message.lower() else "uploading",
        "message": progress.message,
        "transfer": progress.transfer,
        "total": progress.total,
    })


@auth.post("/upload")
async def upload_file(request: Request,
                      data_center: str = Header(..., alias="X-Data-Center"),
                      directory: str = Header(""),
                      file_name: str = Header(..., alias="X-File-Name"),
                      user: User = Depends(get_current_user)) -> JSONResponse:
    data_center = perform_validation(data_center, "datacenter")
    file_name = perform_validation(file_name, "file_name")
    directory_obj: Directory | None = Directory.get(path=directory, username=user.username)

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    file_path: Path = TRANSFER_PATH / user.username / file_name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch()

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

    async def write_file() -> None:
        with file_path.open("wb") as buffer:
            async for chunk in request.stream():
                buffer.write(chunk)

    upload_task: Task[None] = create_task(write_file())
    job_id: str = str(uuid4())
    UPLOAD_JOBS[job_id] = Progress("Starting", 0)

    async def run_upload_job() -> None:
        async for progress in file_upload(file, file_path, upload_task):
            UPLOAD_JOBS[job_id] = progress

    create_task(run_upload_job())
    await upload_task
    return JSONResponse({"job_id": job_id})


@auth.post("/upload-link")
async def upload_link(link: str, data_center: str, directory: str = "", user: User = Depends(get_current_user)) -> JSONResponse:
    link = perform_validation(link, "link")
    data_center = perform_validation(data_center, "datacenter")
    directory_obj: Directory | None = Directory.get(path=directory, username=user.username)

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
    UPLOAD_JOBS[job_id] = Progress("Starting", 0)

    async def run_upload_job() -> None:
        async for progress in link_upload(file, link):
            UPLOAD_JOBS[job_id] = progress

    create_task(run_upload_job())
    return JSONResponse({"job_id": job_id})


@auth.post("/create-folder")
def create_folder(directory: str, name: str, user: User = Depends(get_current_user)) -> JSONResponse:
    folder: Directory = Directory(
        path=f"{perform_validation(directory, "directory")}/{perform_validation(name, "file_name")}",
        modified_at=datetime.now(timezone.utc),
        username=user.username,
    )
    folder.save()
    return JSONResponse({
        "message": "Folder created successfully",
        "directory": f"/{folder.path}",
    })


@auth.get("/directory")
def get_directory(directory: str, user: User = Depends(get_current_user)) -> tuple[list[Directory], list[File]]:
    directory_obj: Directory | None = Directory.get(path=directory, username=user.username)

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    return Directory.get_all(user.username, directory=directory), File.get_all(user.username, directory_id=directory_obj.id)


@auth.get("/trash")
def get_trash(user: User = Depends(get_current_user)) -> tuple[list[Directory], list[File]]:
    File.purge_expired(user.username)
    return Directory.get_all(user.username, trashed_only=True), File.get_all(user.username, trashed_only=True),


@auth.delete("/files/{fid}")
def file_delete(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    file: File | None = File.get(fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    file.move_to_trash()
    return JSONResponse({"message": "File moved to trash"})


@auth.delete("/directories/{did}")
def directory_delete(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = Directory.get(did=did)

    if directory is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    if directory.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    for file in File.get_all(user.username, directory_id=directory.id):
        file.move_to_trash()

    for child in Directory.get_all(user.username, directory=directory.path):
        directory_delete(cast(int, child.id), user)

    directory.move_to_trash()
    return JSONResponse({"message": "Directory moved to trash"})


@auth.delete("/trash/{fid}")
def permanently_delete(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    file: File | None = File.get(fid=fid, trashed_only=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    file.delete()
    return JSONResponse({"message": "Permanently deleted"})


@auth.delete("/trash/directory/{did}")
def permanently_delete_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = Directory.get(did=did, trashed_only=True)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    for file in File.get_all(user.username, directory_id=directory.id, trashed_only=True):
        file.delete()

    for child in Directory.get_all(user.username, directory=directory.path, trashed_only=True):
        permanently_delete_directory(cast(int, child.id), user)

    directory.delete()
    return JSONResponse({"message": "Permanently deleted"})


@auth.post("/trash/{fid}/restore")
def restore_file(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    file: File | None = File.get(fid=fid, trashed_only=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    file.restore()
    return JSONResponse({"message": "Restored"})


@auth.post("/trash/directory/{did}/restore")
def restore_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = Directory.get(did=did, trashed_only=True)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    directory.restore()

    return JSONResponse({"message": "Restored"})


@auth.post("/files/{fid}/public-link")
def create_public_link(fid: int, user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = File.get(fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    return {"url": f"/auth/stream/{create_public_stream_token(file=file, username=file.username)}"}


@public.get("/stream/{token}")
async def public_stream(token: str, request: Request) -> Response:
    try:
        payload: dict[str, str | int] = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid download stream link")

    file: File | None = File.get(fid=int(payload["file_id"]))

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

    file: File | None = File.get(fid=int(payload["file_id"]))

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
