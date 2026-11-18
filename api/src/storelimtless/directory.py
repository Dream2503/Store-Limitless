from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Literal, TYPE_CHECKING

from tqdm import tqdm

from .exception import StoreLimitlessResponseError, StoreLimitlessHTTPError
from .file import File

if TYPE_CHECKING:
    from .client import StoreLimitlessClient


class Directory:
    def __init__(self, client: StoreLimitlessClient, token: str, path: Path = Path("")):
        self.client: StoreLimitlessClient = client
        self.token: str = token
        self.path: Path = path

    def __repr__(self) -> str:
        return f"Directory(path={str(self.path)!r})"

    def mkdir(self, name: str, *, missing_ok=False) -> "Directory":
        if not name or name in (".", ".."):
            raise ValueError("Invalid folder name")

        path = self.path / name

        try:
            self.client.request(
                "POST",
                "/auth/create-folder",
                json={
                    "directory": "" if path == Path("") else str(path),
                },
            )
        except StoreLimitlessHTTPError as e:
            if not (missing_ok and e.status_code == 400 and "already exists" in str(e)):
                raise

        return self

    def cd(self, arg: str) -> "Directory":
        if not arg or arg == "~" or arg == "/":
            self.path = Path("")
            return self

        if arg.startswith("/"):
            path = Path(arg.lstrip("/"))
        else:
            path = self.path / arg

        parts: list[str] = []

        for part in path.parts:
            if part in ("", "."):
                continue

            if part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)

        self.path = Path(*parts)
        return self

    def ls(self) -> list[File]:
        response = self.client.request("GET", "/auth/files")

        files: list[dict] = response.json()
        current_directory: str = "" if self.path == Path("") else str(self.path)

        return [
            File(
                id=file["id"],
                directory=Directory(self.client, self.token, Path(file["directory"])),
                name=file["name"],
                type=file["type"],
                size=file["size"],
                modified_at=datetime.fromisoformat(file["modified_at"]),
                data_center=file["data_center"],
            )
            for file in files
            if file["directory"] == current_directory and file["name"] != ".__folder__"
        ]

    def upload(self, source: str | Path, data_center: Literal["Discord", "Telegram"], *, quiet: bool = False) -> File:
        previous_files: list[File] = self.ls()

        if isinstance(source, Path) or not source.startswith(("http://", "https://")):
            path = Path(source)

            if not path.is_file():
                raise FileNotFoundError(path)

            file_name = path.name

            with path.open("rb") as file:
                response = self.client.request(
                    "POST",
                    "/auth/upload",
                    headers={
                        "X-Data-Center": data_center,
                        "X-File-Name": file_name,
                        "X-Directory": "" if self.path == Path("") else str(self.path),
                        "Content-Type": "application/octet-stream",
                    },
                    data=file,
                )
        else:
            response = self.client.request(
                "POST",
                "/auth/upload-link",
                json={
                    "link": source,
                    "data_center": data_center,
                    "directory": "" if self.path == Path("") else str(self.path),
                },
            )

        try:
            job_id: str = response.json()["job_id"]
        except (ValueError, KeyError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid upload response") from e

        with tqdm(unit="B", unit_scale=True, disable=quiet) as bar:
            while True:
                response = self.client.request(
                    "GET",
                    f"/auth/upload/{job_id}/status",
                )

                try:
                    status: dict = response.json()
                    transfer: int = status["transfer"]
                    total: int = status["total"]
                    message: str = status["message"]
                    state: str = status["status"]

                except (ValueError, KeyError, TypeError) as e:
                    raise StoreLimitlessResponseError("StoreLimitless server returned an invalid upload status") from e

                bar.total = total
                bar.n = transfer
                bar.set_description(message)
                bar.refresh()

                if state == "completed":
                    break

                sleep(0.1)

        previous_ids: set[int] = {file.id for file in previous_files}

        for file in self.ls():
            if file.id not in previous_ids:
                return file

        raise StoreLimitlessResponseError("Upload completed but the uploaded file could not be found")
