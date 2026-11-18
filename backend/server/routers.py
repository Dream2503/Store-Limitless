from json import dumps
from mimetypes import guess_type
from pathlib import Path
from typing import AsyncGenerator
# from fastapi.responses import FileResponse
# import os
from datetime import datetime,timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import Response, StreamingResponse

from backend.database import add_user, File, get_files, get_user, LoginRequest, User, get_file, add_file,update_file,update_user,delete_file
from backend.server.jwt_handler import create_access_token, get_current_user
from backend.server.security import hash_password, verify_password, create_public_stream_token, verify_public_stream_token
from core.config import TRANSFER_PATH
from core.data_center import BackEnd
from core.stream import ChunkCache, get_chunks, parse_range, stream_range
from core.transfer import upload

auth: APIRouter = APIRouter(prefix="/auth")

class CreateFolderRequest(BaseModel):
    directory: str

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
    print(user)

    if not user or not verify_password(credentials.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {
        "message": "Login successful",
        "access_token": create_access_token(data={"sub": user.username}),
        "token_type": "bearer",
    }


@auth.post("/upload")
async def upload_route(
    file: UploadFile,
    data_center: str = Form(...),
    directory: str = Form(""),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No file name provided",
        )
    filename = Path(file.filename).name
    
    directory = directory.strip().strip("/")

    if (directory in {".", ".."} or "/" in directory or "\\" in directory):
            raise HTTPException(
                status_code=400,
                detail="Invalid folder name",
            )
    

    user_transfer_path = TRANSFER_PATH / current_user.username
    if directory:
        user_transfer_path = user_transfer_path / directory
    user_transfer_path.mkdir(parents=True, exist_ok=True)

    file_path = user_transfer_path / filename

    with open(file_path, "wb") as buffer:
        while chunk := await file.read(BackEnd.MAX_SIZE):
            buffer.write(chunk)

    file_job: File = File(
        directory=directory,
        name=file.filename,
        type=guess_type(filename)[0] or "application/octet-stream",
        size=file_path.stat().st_size,
        modified_at=datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc),
        links=[],
        data_center=data_center,
        username=current_user.username,
    )

    async def progress_stream() -> AsyncGenerator[str, None]:
        async for progress in upload(file_job):
            yield dumps({"progress": progress}) + "\n"

    return StreamingResponse(progress_stream(), media_type="application/x-ndjson")

@auth.post("/create-folder")
def create_folder(
    folder: CreateFolderRequest,
    current_user: User = Depends(get_current_user),
):
    directory = folder.directory.strip().strip("/")

    if not directory:
        raise HTTPException(
            status_code=400,
            detail="Folder name cannot be empty",
        )

    # Prevent nested/invalid paths if you only want one folder name
    if (directory in {".", ".."} or "/" in directory or "\\" in directory):
        raise HTTPException(
            status_code=400,
            detail="Invalid folder name",
        )

    # Check existing folders/files
    files = get_files(username=current_user.username) or []

    existing_folder = next(
        (
            file
            for file in files
            if file.directory == directory
            and file.name == ".__folder__"
        ),
        None,
    )

    if existing_folder:
        raise HTTPException(
            status_code=400,
            detail="Folder already exists",
        )

    # Create actual folder on disk
    folder_path = TRANSFER_PATH / current_user.username / directory
    folder_path.mkdir(parents=True, exist_ok=True)

    # Create database entry representing the folder
    folder_file = File(
        directory=directory,
        name=".__folder__",
        type="folder",
        size=0,
        modified_at=datetime.now(timezone.utc),
        links=[],
        data_center="",
        username=current_user.username,
    )

    add_file(folder_file)

    return {
        "message": "Folder created successfully",
        "directory": directory,
    }

@auth.get("/files")
def get_user_files(
    current_user: User = Depends(get_current_user),
) -> list[File]:
    files = get_files(username=current_user.username) or []

    return [
        file
        for file in files
        if file.directory != "__trash__"
    ]

@auth.post("/files/{file_id}/public-link")
def create_public_stream_link(file_id: int, current_user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(file_id=file_id)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    if file.id is None:
        raise HTTPException(status_code=400, detail="Invalid file metadata")

    return {
        "url": f"/auth/public-stream/{create_public_stream_token(file_id=file.id, username=file.username)}",
    }


@auth.get("/public-stream/{token}")
async def public_stream_route(token: str, request: Request):
    try:
        payload = verify_public_stream_token(token)

    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid public stream link")

    file: File | None = get_file(file_id=payload["file_id"])

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found")

    return await stream_file(file, request)


@auth.delete("/files/{file_id}")
def delete_file_route(file_id: int, current_user: User = Depends(get_current_user)) -> dict[str, str]:
    file: File | None = get_file(file_id=file_id)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    file.directory = "__trash__"
    return {"message": "File moved to trash"}


async def stream_file(file: File, request: Request):
    try:
        parts = get_chunks(file.links, file.size)

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"File metadata failure: {e}") from e

    if not parts:
        raise HTTPException(status_code=404, detail="File has no parts")

    size = parts[-1].end + 1

    try:
        byte_range = parse_range(request.headers.get("range"), size)

    except (ValueError, IndexError):
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{size}",
                "Accept-Ranges": "bytes",
            },
        )

    cache = ChunkCache(str(file.id), file.data_center)
    length = byte_range.end - byte_range.start + 1
    content_type = file.type or guess_type(file.name)[0] or "application/octet-stream"

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": content_type,
        "Content-Disposition": f'inline; filename="{file.name}"',
        "Cache-Control": "public, max-age=3600",
    }

    if request.headers.get("range") is not None:
        headers["Content-Range"] = f"bytes {byte_range.start}-{byte_range.end}/{size}"
        status_code = 206

    else:
        status_code = 200

    return StreamingResponse(
        stream_range(parts, byte_range, cache),
        status_code=status_code,
        headers=headers,
        media_type=content_type,
    )
