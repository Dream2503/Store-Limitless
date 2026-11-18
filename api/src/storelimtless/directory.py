from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Literal, TYPE_CHECKING, cast, Iterator

from requests import Response
from tqdm import tqdm

from .auth import StoreLimitless
from .exception import StoreLimitlessResponseError
from .file import File

if TYPE_CHECKING:
    from .user import User


class DirectoryResult:
    def __init__(self, directories: list[Directory], files: list[File]):
        self.directories: list[Directory] = directories
        self.files: list[File] = files

    def __iter__(self) -> Iterator[list[Directory] | list[File]]:
        yield self.directories
        yield self.files

    @property
    def directory(self) -> Directory | None:
        return self.directories[0] if len(self.directories) else None

    @property
    def file(self) -> File | None:
        return self.files[0] if len(self.files) else None


class Directory:
    def __init__(self, user: User, id: int, path: Path, modified_at: datetime, deleted_at: datetime | None):
        self.user: User = user
        self.id: int = id
        self.path: Path = path
        self.modified_at: datetime = modified_at
        self.deleted_at: datetime | None = deleted_at

    def __repr__(self) -> str:
        return (
            f"Directory(id={self.id}, "
            f"path={self.path!r}, "
            f"modified_at={self.modified_at}, "
            f"deleted_at={self.deleted_at}, "
            f"user={self.user.username!r})"
        )

    def __str__(self) -> str:
        folders: int = 0
        files_count: int = 0
        total_size: int = 0
        directories_to_visit: list[Directory] = [self]

        while directories_to_visit:
            directory: Directory = directories_to_visit.pop()
            directories, files = directory.ls
            folders += len(directories)
            files_count += len(files)
            total_size += sum(file.size for file in files)
            directories_to_visit.extend(directories)

        unit: str = ""
        size: float = float(total_size)
        units: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB")

        for unit in units:
            if size < 1024 or unit == units[-1]:
                break

            size /= 1024

        return (
            f"Directory: {self.path}\n"
            f"Folders: {folders}\n"
            f"Files: {files_count}\n"
            f"Size: {size:.2f} {unit}\n"
            f"Modified: {self.modified_at}\n"
        )

    def __truediv__(self, arg: str) -> Directory:
        return self.cd(arg)

    def mkdir(self, name: str) -> Directory:
        if not name or name in (".", ".."):
            raise ValueError("Invalid folder name")

        StoreLimitless.request(self.user.token, "POST", "/auth/create-folder", params={"directory": str(self.path), "name": name})
        return self

    def cd(self, arg: str) -> Directory:
        if arg in ("~", "/"):
            return self.user.home

        if arg == "..":
            if self.path == self.user.home.path:
                return self.user.home

            path = self.path.parent
            directory = self.user.home

            for part in path.relative_to(self.user.home.path).parts:
                directory = directory / part

            return directory

        if len(Path(arg).parts) != 1:
            raise ValueError("cd accepts only one directory at a time")

        path = self.path / arg

        for directory in self.ls.directories:
            if directory.path == path:
                return directory

        raise ValueError(f"Directory does not exist: {path}")

    @property
    def ls(self) -> DirectoryResult:
        current_directory = str(self.path)
        directories, files = StoreLimitless.request(self.user.token, "GET", "/auth/directory", params={"directory": current_directory}).json()

        return DirectoryResult([
            Directory(
                self.user,
                directory["id"],
                Path(directory["path"]),
                datetime.fromisoformat(directory["modified_at"]),
                datetime.fromisoformat(directory["deleted_at"]) if directory["deleted_at"] else None
            ) for directory in directories
        ], [
            File(
                file["id"],
                self,
                file["name"],
                file["type"],
                file["size"],
                datetime.fromisoformat(file["modified_at"]),
                datetime.fromisoformat(file["deleted_at"]) if file["deleted_at"] else None,
                file["data_center"]
            ) for file in files
        ])

    def find(self, file_name: str) -> DirectoryResult:
        directories: list[Directory] = []
        files: list[File] = []
        directories_to_visit: list[Directory] = [self]
        query: list[str] = file_name.lower().split()

        while directories_to_visit:
            directory: Directory = directories_to_visit.pop()
            child_directories, child_files = directory.ls
            directories.extend(directory for directory in child_directories if all(word in directory.path.name.lower() for word in query))
            files.extend(file for file in child_files if all(word in file.name.lower() for word in query))
            directories_to_visit.extend(child_directories)

        return DirectoryResult(directories, files)

    def upload(self, source: str | Path, data_center: Literal["Discord", "Telegram"] = "Discord", *, quiet: bool = False) -> File:
        if isinstance(source, Path) or not source.startswith(("http://", "https://")):
            path: Path = Path(source).expanduser()

            with path.open("rb") as file:
                response: Response = StoreLimitless.request(
                    self.user.token,
                    "POST",
                    "/auth/upload",
                    headers={
                        "X-Data-Center": data_center,
                        "X-File-Name": path.name,
                        "X-Directory": str(self.path),
                        "Content-Type": "application/octet-stream",
                    },
                    data=file,
                )
        else:
            response: Response = StoreLimitless.request(
                self.user.token,
                "POST",
                "/auth/upload-link",
                params={
                    "link": source,
                    "data_center": data_center,
                    "directory": str(self.path),
                },
            )

        try:
            job_id: str = response.json()["job_id"]

        except (ValueError, KeyError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid upload response") from e

        with tqdm(
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                disable=quiet,
                ascii=" ━",
                colour="green",
                bar_format="\033[92m{desc}\033[0m{bar:40}\033[0m \033[92m{n_fmt}/{total_fmt}\033[0m \033[91m{rate_fmt}\033[0m eta \033[96m{remaining}\033[0m"
        ) as bar:
            while True:
                response: Response = StoreLimitless.request(self.user.token, "GET", f"/auth/upload/{job_id}/status")

                try:
                    status: dict[str, str | int] = response.json()

                except (ValueError, KeyError, TypeError) as e:
                    raise StoreLimitlessResponseError("StoreLimitless server returned an invalid upload status") from e

                bar.total = status["total"]
                bar.n = status["transfer"]
                bar.set_description(cast(str, status["message"]), refresh=False)
                bar.refresh()

                if status["status"] == "completed":
                    break

                sleep(0.1)

        if status["file_id"] is None:
            raise StoreLimitlessResponseError("Upload completed but the server did not return a file ID")

        for file in self.ls.files:
            if file.id == status["file_id"]:
                return file

        raise StoreLimitlessResponseError(f"Upload completed but file {status["file_id"]} could not be found")

    def rm(self) -> Directory:
        StoreLimitless.request(self.user.token, "DELETE", f"/auth/directory/{self.id}")
        return self

    def delete(self) -> None:
        StoreLimitless.request(self.user.token, "DELETE", f"/auth/trash/directory/{self.id}")

    def restore(self) -> Directory:
        StoreLimitless.request(self.user.token, "POST", f"/auth/trash/directory/{self.id}/restore")
        return self
