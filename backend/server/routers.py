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


@auth.get("/upload/{job_id}/status")
async def upload_status(job_id: str) -> JSONResponse:
    progress: Progress = cast(Progress, await Redis.get(job_id, "Progress"))
    completed: bool = "complete" in progress.message.lower()
    return JSONResponse({
        "status": "completed" if completed else "uploading",
        "message": " ".join(progress.message.split()[:2]) if completed else progress.message,
        "transfer": progress.transfer,
        "total": progress.total,
        "file_id": int(progress.message.split("id=")[-1]) if completed else None,
    })


@auth.post("/upload")
async def upload_file(
        request: Request,
        data_center: str = Header(..., alias="X-Data-Center"),
        directory: str = Header(..., alias="X-Directory"),
        file_name: str = Header(..., alias="X-File-Name"),
        user: User = Depends(get_current_user)
) -> JSONResponse:
    data_center = perform_validation(data_center, "data_center")
    file_name = perform_validation(file_name, "file_name")
    directory_obj: Directory | None = await Directory.get(path=Path(directory), username=user.username)

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
            chunk: bytes

            async for chunk in request.stream():
                buffer.write(chunk)

    upload_task: Task[None] = create_task(write_file())
    job_id: str = str(uuid4())
    await Redis.set(job_id, Progress("Starting", 0))

    async def run_upload_job() -> None:
        progress: Progress

        async for progress in file_upload(file, file_path, upload_task):
            await Redis.set(job_id, progress)

    create_task(run_upload_job())
    await upload_task
    return JSONResponse({"job_id": job_id})


@auth.post("/upload-link")
async def upload_link(link: str, data_center: str, directory: str, user: User = Depends(get_current_user)) -> JSONResponse:
    link = perform_validation(link, "link")
    data_center = perform_validation(data_center, "data_center")
    directory_obj: Directory | None = await Directory.get(path=Path(directory), username=user.username)

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
    await Redis.set(job_id, Progress("Starting", 0))

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
    directory_obj: Directory | None = await Directory.get(path=Path(directory), username=user.username)

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    return await Directory.get_all(user.username, directory=Path(directory)), await File.get_all(user.username, directory_id=directory_obj.id)


@auth.get("/trash")
async def get_trash(user: User = Depends(get_current_user)) -> tuple[list[Directory], list[File]]:
    await File.purge_expired(user.username)
    return await Directory.get_all(user.username, trashed_only=True), await File.get_all(user.username, trashed_only=True)


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
    file: File | None = await File.get(user.username, fid=fid, trashed_only=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    await file.delete()
    return JSONResponse({"message": "Permanently deleted"})


@auth.delete("/trash/directory/{did}")
async def permanently_delete_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = await Directory.get(user.username, did=did, trashed_only=True)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    for file in await File.get_all(user.username, directory_id=directory.id, trashed_only=True):
        await file.delete()

    for child in await Directory.get_all(user.username, directory=directory.path, trashed_only=True):
        await permanently_delete_directory(cast(int, child.id), user)

    await directory.delete()
    return JSONResponse({"message": "Permanently deleted"})


@auth.post("/trash/file/{fid}/restore")
async def restore_file(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    file: File | None = await File.get(user.username, fid=fid, trashed_only=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    await file.restore()
    return JSONResponse({"message": "Restored"})


@auth.post("/trash/directory/{did}/restore")
async def restore_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    directory: Directory | None = await Directory.get(user.username, did=did, trashed_only=True)

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
