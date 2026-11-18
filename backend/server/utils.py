from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel

from backend.database import get_files, add_file, File


class LoginRequest(BaseModel):
    username: str
    password: str


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


def ensure_folder_chain(username: str, directory: str) -> None:
    if not directory:
        return

    segments: list[str] = directory.split("/")
    existing: set[str] = {f.directory for f in (get_files(username=username) or []) if f.name == ".__folder__"}
    path_so_far: str = ""

    for segment in segments:
        path_so_far = f"{path_so_far}/{segment}" if path_so_far else segment

        if path_so_far not in existing:
            add_file(File(
                directory=path_so_far,
                name=".__folder__",
                type="folder",
                size=0,
                modified_at=datetime.now(timezone.utc),
                links=[],
                data_center="",
                username=username)
            )
            existing.add(path_so_far)
