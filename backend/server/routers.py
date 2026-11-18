from asyncio import Task, create_task
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import Response, StreamingResponse, JSONResponse

from backend.database import add_user, File, get_files, get_user, User, get_file, update_file, purge_expired_trash, delete_file
from backend.server.jwt_handler import create_access_token, get_current_user
from backend.server.security import hash_password, verify_password, create_public_stream_token, verify_public_stream_token
from backend.server.utils import LoginRequest, LinkDownloadRequest, CreateFolderRequest, validate_directory_path, ensure_folder_chain
from core.config import TRANSFER_PATH, UPLOAD_JOBS
from core.stream import ChunkCache, get_chunks, parse_range, stream_range, ByteRange, FileChunk
from core.transfer import link_upload, file_upload
from core.utils import get_transfer_path, Progress

auth: APIRouter = APIRouter(prefix="/auth")


@auth.post("/register")
def register(user: User) -> dict[str, str]:
    existing_user: User | None = get_user(username=user.username)

    if existing_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    user.password = hash_password(user.password)
    add_user(user)
    return {"message": "User registered successfully"}


@auth.post("/login")
def login(credentials: LoginRequest) -> dict[str, str]:
    user: User | None = get_user(username=credentials.username)

    if not user or not verify_password(credentials.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {
        "message": "Login successful",
        "access_token": create_access_token(data={"sub": user.username}),
        "token_type": "bearer",
    }


@auth.get("/upload/{job_id}/status")
async def upload_status(job_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    progress: Progress | None = UPLOAD_JOBS.get(job_id)

    if progress is None:
        raise HTTPException(status_code=404, detail="Job not found")

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
    directory = validate_directory_path(directory)
    ensure_folder_chain(user.username, directory)
    file_path: Path = get_transfer_path(user.username, directory, file_name)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch()

    file_job: File = File(
        directory=directory,
        name=file_name,
        type=request.headers.get("content-type", "application/octet-stream"),
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
                buffer.flush()

    upload_task: Task[None] = create_task(write_file())
    job_id: str = str(uuid4())
    UPLOAD_JOBS[job_id] = Progress("Starting", 0)

    async def run_upload_job() -> None:
        async for progress in file_upload(file_job, file_path, upload_task):
            UPLOAD_JOBS[job_id] = progress

    create_task(run_upload_job())
    await upload_task
    return JSONResponse({"job_id": job_id})


@auth.post("/upload-link")
async def upload_link(request: LinkDownloadRequest,
                      user: User = Depends(get_current_user)) -> JSONResponse:
    link: str = request.link.strip()
    data_center: str = request.data_center.strip()
    directory: str = request.directory.strip()

    if not link:
        raise HTTPException(status_code=400, detail="Link is required")

    if not data_center:
        raise HTTPException(status_code=400, detail="Data center is required")

    if data_center not in {"Discord", "Telegram"}:
        raise HTTPException(status_code=400, detail="Invalid data center")

    directory = validate_directory_path(directory)
    ensure_folder_chain(user.username, directory)
    file: File = File(
        directory=directory,
        name="link_file",
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
def create_folder(folder: CreateFolderRequest, user: User = Depends(get_current_user)):
    directory: str = folder.directory.strip().strip("/")

    if not directory:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty")

    directory: str = validate_directory_path(directory)
    files: list[File] = get_files(username=user.username) or []
    existing_folder: File | None = next((f for f in files if f.directory == directory and f.name == ".__folder__"), None)

    if existing_folder:
        raise HTTPException(status_code=400, detail="Folder already exists")

    folder_path: Path = TRANSFER_PATH / user.username / directory
    folder_path.mkdir(parents=True, exist_ok=True)
    ensure_folder_chain(user.username, directory)
    return {"message": "Folder created successfully", "directory": directory}


@auth.get("/files")
def get_user_files(user: User = Depends(get_current_user)) -> list[File]:
    return get_files(username=user.username)


@auth.get("/trash")
def get_trash(user: User = Depends(get_current_user)) -> list[File]:
    purge_expired_trash(username=user.username)
    return get_files(username=user.username, trashed_only=True)


@auth.post("/trash/{fid}/restore")
def restore_file(fid: int, user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(fid=fid, include_trashed=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    trashed: list[File] = get_files(username=user.username, trashed_only=True)

    if file.name == ".__folder__":
        prefix: str = f"{file.directory}/"
        targets: list[File] = [file] + [f for f in trashed if f.directory == file.directory or f.directory.startswith(prefix)]

        for target in targets:
            target.deleted_at = None
            update_file(target)

    else:
        file.deleted_at = None
        update_file(file)

        if file.directory:
            segments: list[str] = file.directory.split("/")
            path_so_far: str = ""
            parent_paths: set[str] = set()

            for segment in segments:
                path_so_far = f"{path_so_far}/{segment}" if path_so_far else segment
                parent_paths.add(path_so_far)

            for item in trashed:
                if item.name == ".__folder__" and item.directory in parent_paths:
                    item.deleted_at = None
                    update_file(item)

    return {"message": "Restored"}


@auth.post("/files/{fid}/public-link")
def create_public_link(fid: int, user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    if file.id is None:
        raise HTTPException(status_code=400, detail="Invalid file metadata")

    return {"url": f"/auth/stream/{create_public_stream_token(file=file, username=file.username)}"}


@auth.get("/stream/{token}")
async def public_stream_route(token: str, request: Request) -> Response:
    try:
        payload: dict[str, str | int] = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid download stream link")

    file: File | None = get_file(fid=int(payload["file_id"]))

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        parts: list[FileChunk] = get_chunks(file.links, file.size)

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"File metadata failure: {e}") from e

    if not parts:
        raise HTTPException(status_code=404, detail="File has no parts")

    size: int = file.size or (parts[-1].end + 1)
    range_header: str | None = request.headers.get("range")

    if range_header:
        try:
            byte_range: ByteRange = parse_range(range_header, size)

        except (ValueError, IndexError):
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{size}",
                    "Accept-Ranges": "bytes",
                },
            )
        status_code: int = 206

    else:
        byte_range: ByteRange = ByteRange(0, size - 1)
        status_code: int = 200

    cache: ChunkCache = ChunkCache(str(file.id), file.data_center)
    length: int = byte_range.end - byte_range.start + 1
    content_type: str = file.type or guess_type(file.name)[0] or "application/octet-stream"
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": content_type,
        "Content-Disposition": f"inline; filename=\"{file.name.encode('ascii', 'ignore').decode()}\"; filename*=UTF-8''{quote(file.name)}",
        "Cache-Control": "no-cache",  # Do not aggressively cache tokenized streams
    }

    if status_code == 206:
        headers["Content-Range"] = f"bytes {byte_range.start}-{byte_range.end}/{size}"

    return StreamingResponse(
        stream_range(parts, byte_range, cache),
        status_code=status_code,
        headers=headers,
        media_type=content_type,
    )


@auth.get("/download/{token}")
async def public_download_route(token: str) -> StreamingResponse:
    try:
        payload: dict[str, str | int] = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid public download link")

    file: File | None = get_file(fid=int(payload["file_id"]))

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        parts: list[FileChunk] = get_chunks(file.links, file.size)

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"File metadata failure: {e}") from e

    if not parts:
        raise HTTPException(status_code=404, detail="File has no parts")

    size: int = file.size or (parts[-1].end + 1)
    byte_range: ByteRange = ByteRange(0, size - 1)
    cache: ChunkCache = ChunkCache(str(file.id), file.data_center)
    content_type: str = file.type or guess_type(file.name)[0] or "application/octet-stream"
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "Content-Type": content_type,
        "Content-Disposition": f'attachment; filename="{file.name.encode("ascii", "ignore").decode() or "download"}"; filename*=UTF-8\'\'{quote(file.name)}',
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(
        stream_range(parts, byte_range, cache),
        status_code=200,
        headers=headers,
        media_type=content_type,
    )


@auth.delete("/files/{fid}")
def delete_file_route(fid: int, user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    file.deleted_at = datetime.now(timezone.utc)
    update_file(file)
    return {"message": "File moved to trash"}


@auth.delete("/trash/{fid}")
def permanently_delete(fid: int, user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(fid=fid, include_trashed=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    delete_file(file)
    return {"message": "Permanently deleted"}
