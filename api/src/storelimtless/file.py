from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tqdm import tqdm

from .exception import StoreLimitlessResponseError

if TYPE_CHECKING:
    from .directory import Directory


class File:
    def __init__(self, id: int, directory: Directory, name: str, type: str, size: int, modified_at: datetime, data_center: str, ):
        self.id: int = id
        self.directory: Directory = directory
        self.name: str = name
        self.type: str = type
        self.size: int = size
        self.modified_at: datetime = modified_at
        self.data_center: str = data_center

    def __repr__(self) -> str:
        return (
            f"File(id={self.id}, "
            f"directory={self.directory!r}, "
            f"name={self.name!r}, "
            f"type={self.type!r}, "
            f"size={self.size}, "
            f"modified_at={self.modified_at!r}, "
            f"data_center={self.data_center!r})"
        )

    def download(self, path: str | Path | None = None, *, quiet: bool = False) -> Path:
        path = Path(path) if path is not None else Path(self.name)

        response = self.directory.client.request(
            "POST",
            f"/auth/files/{self.id}/public-link",
        )

        try:
            url: str = response.json()["url"]

        except (ValueError, KeyError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid public link response") from e

        response = self.directory.client.request("GET", url, stream=True)

        try:
            total: int = int(response.headers.get("Content-Length", self.size))

        except (ValueError, TypeError) as e:
            raise StoreLimitlessResponseError("StoreLimitless server returned an invalid content length") from e

        path.parent.mkdir(parents=True, exist_ok=True)

        with tqdm(total=total, unit="B", unit_scale=True, desc=self.name, disable=quiet) as bar:
            with path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
                        bar.update(len(chunk))

        return path
