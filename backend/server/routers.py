from json import dumps
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.database import add_user, File, get_files, get_user, LoginRequest, User
from backend.server.jwt_handler import create_access_token, get_current_user
from backend.server.security import hash_password, verify_password
from core.data_center import BackEnd
from core.settings import TRANSFER_PATH
from core.transfer import upload

router: APIRouter = APIRouter(prefix="/auth")


@router.post("/register")
def register(user: User) -> dict[str, str]:
    existing_user: User | None = get_user(username=user.username)

    if existing_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    user.password = hash_password(user.password)
    add_user(user)
    return {"message": "User registered successfully"}


@router.post("/login")
def login(credentials: LoginRequest) -> dict[str, str]:
    user: User | None = get_user(username=credentials.username)

    if not user or not verify_password(credentials.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    access_token: str = create_access_token(data={"sub": user.username})
    return {
        "Message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
    }


@router.post("/upload")
async def upload_route(file: UploadFile, data_center: str = Form(...), current_user: User = Depends(get_current_user)) -> StreamingResponse:
    if current_user.uid is None:
        raise HTTPException(status_code=400, detail="User ID missing")
    uid = current_user.uid
    if not file.filename:
        raise ValueError("No file name provided in /upload")

    file_path: Path = TRANSFER_PATH / file.filename

    with open(file_path, "wb") as buffer:
        while chunk := await file.read(BackEnd.MAX_SIZE):
            buffer.write(chunk)

    file_job: File = File(fname=file.filename, flinks=[], data_center=data_center, uid=uid)

    async def progress_stream() -> AsyncGenerator[str, None]:
        async for progress in upload(file_job):
            yield dumps({"progress": progress}) + "\n"

    return StreamingResponse(progress_stream(), media_type="text/plain")


@router.get("/files")
def get_files_route(current_user: User = Depends(get_current_user)):
    files = get_files(uid=current_user.uid)

    if not files:
        return []

    return [
        {
            "fid": f.fid,
            "fname": f.fname,
            "data_center": f.data_center,
        }
        for f in files
    ]
