from __future__ import annotations

from datetime import datetime
from pathlib import Path
from subprocess import Popen
from typing import TYPE_CHECKING, Literal

from requests import Response
from tqdm import tqdm

from .auth import StoreLimitless
from .exception import StoreLimitlessResponseError

if TYPE_CHECKING:
    from .directory import Directory


class File:
    def __init__(
            self,
            id: int,
            directory: Directory,
            name: str,
            type: str,
            size: int,
            modified_at: datetime,
            deleted_at: datetime | None,
            data_center: str
    ) -> None:
        self.id: int = id
        self.directory: Directory = directory
        self.name: str = name
        self.type: str = type
        self.size: int = size
        self.modified_at: datetime = modified_at
        self.deleted_at: datetime | None = deleted_at
        self.data_center: str = data_center

    def __repr__(self) -> str:
        return (
            f"File(id={self.id}, "
            f"directory={self.directory!r}, "
            f"name={self.name!r}, "
            f"type={self.type!r}, "
            f"size={self.size}, "
            f"modified_at={self.modified_at!r}, "
            f"deleted_at={self.deleted_at!r}, "
            f"data_center={self.data_center!r})"
        )

    def __str__(self) -> str:
        unit: str = ""
        size: float = float(self.size)
        units: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB")

        for unit in units:
            if size < 1024 or unit == units[-1]:
                break

            size /= 1024

        return (
            f"File: {self.name}\n"
            f"Path: {self.directory.path}\n"
            f"Size: {size:.2f} {unit}\n"
            f"Modified: {self.modified_at}\n"
            f"Data Center: {self.data_center}\n"
        )

    def download(self, path: str | Path | None = None, *, quiet: bool = False) -> Path:
        path = Path(path) if path is not None else Path(self.name)

        try:
            token: str = StoreLimitless.request(self.directory.user.token, "POST", f"/auth/file/{self.id}/public-link").json()["public_token"]

        except (ValueError, KeyError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid public link response") from e

        response: Response = StoreLimitless.request(self.directory.user.token, "GET", f"/public/download/{token}", stream=True)

        try:
            total: int = int(response.headers.get("Content-Length", self.size))

        except (ValueError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid content length") from e

        path.parent.mkdir(parents=True, exist_ok=True)

        with tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=self.name,
                disable=quiet,
                ascii=" ━",
                colour="green",
                bar_format="\033[92m{desc}\033[0m{bar:40}\033[0m \033[92m{n_fmt}/{total_fmt}\033[0m \033[91m{rate_fmt}\033[0m eta \033[96m{remaining}\033[0m",
        ) as bar:
            with path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        bar.update(file.write(chunk))

        return path

    def stream(self, *, application: Literal["vlc"] = "vlc") -> None:
        try:
            token: str = StoreLimitless.request(self.directory.user.token, "POST", f"/auth/file/{self.id}/public-link").json()["public_token"]

        except (ValueError, KeyError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid public link response") from e

        process: Popen = Popen([application, f"{StoreLimitless.API_URL}/public/stream/{token}"])
        process.wait()

    def rm(self) -> File:
        StoreLimitless.request(self.directory.user.token, "DELETE", f"/auth/file/{self.id}")
        return self

    def delete(self) -> None:
        StoreLimitless.request(self.directory.user.token, "DELETE", f"/auth/trash/file/{self.id}")

    def restore(self) -> File:
        StoreLimitless.request(self.directory.user.token, "POST", f"/auth/trash/file/{self.id}/restore")
        return self
