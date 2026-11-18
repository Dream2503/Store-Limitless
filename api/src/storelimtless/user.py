from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast, TYPE_CHECKING

from .auth import StoreLimitless

if TYPE_CHECKING:
    from .directory import Directory, DirectoryResult
    from .file import File


class User:
    def __init__(self, token: str, username: str, first_name: str, last_name: str, home: dict[str, int | str | None]) -> None:
        from .directory import Directory

        self.token = token
        self.username: str = username
        self.first_name: str = first_name
        self.last_name: str = last_name
        self._home: Directory = Directory(self,
                                          cast(int, home["id"]),
                                          Path(cast(str, home["path"])),
                                          datetime.fromisoformat(cast(str, home["modified_at"])),
                                          datetime.fromisoformat(cast(str, home["deleted_at"])) if home["deleted_at"] else None)

    def __repr__(self) -> str:
        return (
            f"User(username={self.username!r}, "
            f"first_name={self.first_name!r}, "
            f"last_name={self.last_name!r}, "
            f"home={self._home!r})"
        )

    def __str__(self) -> str:
        return (
            f"{self.first_name} {self.last_name} (@{self.username})\n"
            f"{str(self.home).split("\n", 1)[1]}"
        )

    @property
    def home(self) -> Directory:
        return self._home

    @property
    def trash(self) -> DirectoryResult:
        from .directory import Directory, DirectoryResult
        from .file import File

        directories, files = StoreLimitless.request(self.token, "GET", "/auth/trash", ).json()
        return DirectoryResult([
            Directory(
                self,
                directory["id"],
                Path(directory["path"]),
                datetime.fromisoformat(directory["modified_at"]),
                datetime.fromisoformat(directory["deleted_at"]) if directory["deleted_at"] else None
            ) for directory in directories
        ], [
            File(
                file["id"],
                self.home,
                file["name"],
                file["type"],
                file["size"],
                datetime.fromisoformat(file["modified_at"]),
                datetime.fromisoformat(file["deleted_at"]) if file["deleted_at"] else None,
                file["data_center"]
            ) for file in files
        ])
