from datetime import datetime, timezone
from json import dumps
from mimetypes import guess_type
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from backend.database import (add_user, File, get_files, get_user, LoginRequest, User, get_file,
                              add_file, update_file, purge_expired_trash, get_trashed_files, delete_file)
from backend.server.jwt_handler import create_access_token, get_current_user
from backend.server.security import hash_password, verify_password, create_public_stream_token, verify_public_stream_token
from core import (TRANSFER_PATH, get_transfer_path, BackEnd, download_link, ChunkCache,
                  get_chunks, parse_range, stream_range, ByteRange, upload, FileChunk)

auth: APIRouter = APIRouter(prefix="/auth")


class CreateFolderRequest(BaseModel):
    directory: str


class LinkDownloadRequest(BaseModel):
    link: str
    data_center: str
    directory: str = ""


def validate_directory_path(directory: str) -> str:
    directory = directory.strip().strip("/")

    if not directory:
        return directory

    for segment in directory.split("/"):
        if not segment or segment in {".", ".."} or "\\" in segment:
            raise HTTPException(status_code=400, detail="Invalid folder name")

    return directory


# Ensure there is a .__folder__ chain cause the frontend is accessing the files through that chain only
def ensure_folder_chain(username: str, directory: str) -> None:
    if not directory:
        return

    segments: list[str] = directory.split("/")
    existing: set[str] = {f.directory for f in (get_files(username=username) or []) if f.name == ".__folder__"}
    path_so_far: str = ""

    for segment in segments:
        path_so_far = f"{path_so_far}/{segment}" if path_so_far else segment

        if path_so_far not in existing:
            add_file(File(directory=path_so_far, name=".__folder__", type="folder", size=0,
                          modified_at=datetime.now(timezone.utc), links=[], data_center="", username=username))
            existing.add(path_so_far)


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


@auth.post("/upload")
async def upload_route(file: UploadFile, data_center: str = Form(...), directory: str = Form(""),
                       current_user: User = Depends(get_current_user)) -> StreamingResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file name provided")

    filename: str = Path(file.filename).name
    directory: str = validate_directory_path(directory)

    ensure_folder_chain(current_user.username, directory)

    file_path: Path = get_transfer_path(current_user.username, directory, filename)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "wb") as buffer:
        while chunk := await file.read(BackEnd.MAX_SIZE):
            buffer.write(chunk)

    file_job: File = File(directory=directory, name=file.filename, type=guess_type(filename)[0] or "application/octet-stream",
                          size=file_path.stat().st_size, modified_at=datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc),
                          links=[], data_center=data_center, username=current_user.username)

    async def progress_stream() -> AsyncGenerator[str, None]:
        async for progress in upload(file_job):
            yield dumps({"progress": progress}) + "\n"

    return StreamingResponse(progress_stream(), media_type="application/x-ndjson")


@auth.post("/upload-from-link")
async def upload_from_link_route(request: LinkDownloadRequest, current_user: User = Depends(get_current_user)) -> StreamingResponse:
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
    ensure_folder_chain(current_user.username, directory)
    file_job = File(directory=directory, name="link_file", type="application/octet-stream", size=0,
                    modified_at=datetime.now(timezone.utc), links=[], data_center=data_center, username=current_user.username)

    async def progress_stream() -> AsyncGenerator[str, None]:
        try:
            async for progress in download_link(file_job, link):
                yield dumps({"status": "uploading", "progress": progress}) + "\n"

            yield dumps({"status": "completed", "progress": 100}) + "\n"

        except Exception as e:
            print(f"Link upload error: {e}")
            yield dumps({"status": "error", "error": str(e)}) + "\n"

    return StreamingResponse(progress_stream(), media_type="application/x-ndjson")


@auth.post("/create-folder")
def create_folder(folder: CreateFolderRequest, current_user: User = Depends(get_current_user)):
    directory: str = folder.directory.strip().strip("/")

    if not directory:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty")

    directory: str = validate_directory_path(directory)
    files: list[File] = get_files(username=current_user.username) or []
    existing_folder: File | None = next((f for f in files if f.directory == directory and f.name == ".__folder__"), None)

    if existing_folder:
        raise HTTPException(status_code=400, detail="Folder already exists")

    folder_path: Path = TRANSFER_PATH / current_user.username / directory
    folder_path.mkdir(parents=True, exist_ok=True)
    ensure_folder_chain(current_user.username, directory)
    return {"message": "Folder created successfully", "directory": directory}


@auth.get("/files")
def get_user_files(current_user: User = Depends(get_current_user)) -> list[File]:
    files: list[File] = get_files(username=current_user.username) or []
    return [file for file in files if file.directory != "__trash__"]


@auth.get("/trash")
def get_trash(current_user: User = Depends(get_current_user)) -> list[File]:
    purge_expired_trash(username=current_user.username)
    return get_trashed_files(username=current_user.username)


@auth.post("/trash/{file_id}/restore")
def restore_file(file_id: int, current_user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(file_id=file_id, include_trashed=True)

    if file is None or file.username != current_user.username:
        raise HTTPException(status_code=404, detail="File not found")

    if file.name == ".__folder__":
        prefix: str = f"{file.directory}/"
        trashed: list[File] = get_trashed_files(username=current_user.username)
        targets: list[File] = [file] + [f for f in trashed if f.directory == file.directory or f.directory.startswith(prefix)]

        for target in targets:
            target.deleted_at = None
            update_file(target)

    else:
        file.deleted_at = None
        update_file(file)

    return {"message": "Restored"}


@auth.post("/files/{file_id}/public-link")
def create_public_link(file_id: int, current_user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(file_id=file_id)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    if file.id is None:
        raise HTTPException(status_code=400, detail="Invalid file metadata")

    return {"url": f"/auth/stream/{create_public_stream_token(file=file, username=file.username)}"}


@auth.get("/stream/{token}")
async def public_stream_route(token: str, request: Request):
    try:
        payload: dict[str, str | int] = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid public stream link")

    file: File | None = get_file(file_id=payload["file_id"])

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        parts: list[FileChunk] = get_chunks(file.links, file.size)

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"File metadata failure: {e}") from e

    if not parts:
        raise HTTPException(status_code=404, detail="File has no parts")

    size: int = parts[-1].end + 1

    try:
        byte_range: ByteRange = parse_range(request.headers.get("range"), size)

    except (ValueError, IndexError):
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{size}",
                "Accept-Ranges": "bytes",
            },
        )

    cache: ChunkCache = ChunkCache(str(file.id), file.data_center)
    length: int = byte_range.end - byte_range.start + 1
    content_type: str = file.type or guess_type(file.name)[0] or "application/octet-stream"
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": content_type,
        "Content-Disposition": f"inline; filename=\"{file.name.encode('ascii', 'ignore').decode()}\"; filename*=UTF-8''{quote(file.name)}",
        "Cache-Control": "public, max-age=3600",
    }
    if request.headers.get("range") is not None:
        headers["Content-Range"] = f"bytes {byte_range.start}-{byte_range.end}/{size}"
        status_code: int = 206

    else:
        status_code = 200

    return StreamingResponse(stream_range(parts, byte_range, cache), status_code=status_code, headers=headers, media_type=content_type)


@auth.get("/download/{token}")
async def public_download_route(token: str):
    try:
        payload: dict[str, int] = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid public download link")

    file: File | None = get_file(file_id=payload["file_id"])

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        parts: list[FileChunk] = get_chunks(file.links, file.size)

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"File metadata failure: {e}") from e

    if not parts:
        raise HTTPException(status_code=404, detail="File has no parts")

    size: int = parts[-1].end + 1
    cache: ChunkCache = ChunkCache(str(file.id), file.data_center)
    content_type: str = file.type or guess_type(file.name)[0] or "application/octet-stream"

    headers: dict[str, str] = {
        "Content-Length": str(size),
        "Content-Type": content_type,
        "Content-Disposition": f'attachment; filename="{file.name}"',
        "Cache-Control": "public, max-age=3600",
    }
    return StreamingResponse(
        stream_range(parts, ByteRange(0, size - 1), cache),
        status_code=200,
        headers=headers,
        media_type=content_type,
    )


@auth.delete("/files/{file_id}")
def delete_file_route(file_id: int, current_user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(file_id=file_id)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    file.deleted_at = datetime.now(timezone.utc)
    update_file(file)
    return {"message": "File moved to trash"}


@auth.delete("/trash/{file_id}")
def permanently_delete(file_id: int, current_user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(file_id=file_id, include_trashed=True)

    if file is None or file.username != current_user.username:
        raise HTTPException(status_code=404, detail="File not found")

    delete_file(file)
    return {"message": "Permanently deleted"}
