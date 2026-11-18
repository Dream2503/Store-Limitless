"""Shared helpers for the StoreLimitless FTP filesystem and download adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from backend.database.models import Directory, File
from core.config import TRANSFER_PATH
from core.transfer import file_upload

if TYPE_CHECKING:
    from backend.ftp.filesystem.download import FTPDownloadFile
    from backend.ftp.filesystem.filesystem import StoreLimitlessFS

T = TypeVar("T")


async def request_async(self: FTPDownloadFile, start: int, end: int) -> bytes:
    """Call the public stream route directly and collect its requested byte range."""

    from backend.server.routers.public_routes import public_stream

    path: str = f"/public/stream/{self.token}"
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"range", f"bytes={start}-{end}".encode("ascii"))],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 8000),
    }
    request: Request = Request(scope)
    response: Response = await public_stream(token=self.token, request=request)

    if response.status_code != 206:
        body: bytes = getattr(response, "body", b"")
        detail: str = body.decode("utf-8", errors="replace") or "The stream route did not return partial content."
        raise OSError(response.status_code, detail)

    content_range: str | None = response.headers.get("content-range")

    if content_range is None or not content_range.startswith(f"bytes {start}-"):
        raise OSError(f"The stream route returned an unexpected byte range: {content_range!r}")

    if isinstance(response, StreamingResponse):
        data: bytearray = bytearray()

        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                data.extend(chunk.encode("utf-8"))

            else:
                data.extend(chunk)

        result: bytes = bytes(data)

    else:
        result = response.body

    if not result:
        raise OSError(f"The stream route returned no data for bytes={start}-{end}.")

    return result[:end - start + 1]


def request(self: FTPDownloadFile, start: int, end: int) -> bytes:
    """Run the public stream route on the application's event loop for a byte range."""

    if self.closed:
        raise ValueError("I/O operation on closed file")

    future: Future[bytes] = asyncio.run_coroutine_threadsafe(request_async(self, start, end), self.event_loop)
    return future.result()


def fill_buffer(self: FTPDownloadFile, start: int) -> None:
    """Fetch a bounded byte range starting at the current file position."""

    end: int = min(start + self.RANGE_CHUNK_SIZE, self.file.size) - 1

    if end < start:
        self._buffer_start = start
        self._buffer = b""
        return

    self._buffer_start = start
    self._buffer = request(self, start, end)


class FilesystemUtils:
    """Provide shared database, transfer, and path operations for the FTP filesystem."""

    def run(self: StoreLimitlessFS, coro: Coroutine[Any, Any, T]) -> T:
        """Run an asynchronous database operation and wait for its result."""

        future: Future[T] = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
        return future.result()

    def get_directory(self: StoreLimitlessFS, path: str) -> Directory | None:
        """Resolve a normalized virtual path to a directory owned by the authenticated user."""

        path: str = self.ftpnorm(path)

        if path == "/":
            return self.run(self.user.home)

        parent: str
        name: str
        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        parent_directory: Directory | None = self.get_directory(parent)

        if parent_directory is None:
            return None

        directories: list[Directory] = self.run(
            Directory.get_all(self.user.username, directory=parent_directory.path)
        )
        return next((directory for directory in directories if directory.path.name == name), None)

    def get_file(self: StoreLimitlessFS, path: str) -> File | None:
        """Resolve a normalized virtual path to a file owned by the authenticated user."""

        path: str = self.ftpnorm(path)

        if path == "/":
            return None

        parent: str
        name: str
        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        directory: Directory | None = self.get_directory(parent)

        if directory is None:
            return None

        files: list[File] = self.run(File.get_all(self.user.username, directory_id=directory.id))
        return next((file for file in files if file.name == name), None)

    def get_transfer_path(self: StoreLimitlessFS, file: File) -> Path:
        """Build the temporary local path used while receiving an uploaded file."""

        directory: Path = TRANSFER_PATH / self.user.username
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{file.id}_{file.name}"

    def finish_upload(self: StoreLimitlessFS, file: File, path: Path) -> None:
        """Validate the staged upload and pass it to the StoreLimitless upload pipeline."""

        try:
            size: int = path.stat().st_size

            if size == 0:
                path.unlink(missing_ok=True)
                return

            file.size = size
            file.modified_at = datetime.now(timezone.utc)
            self.run(self.upload_file(file, path))

        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    async def upload_file(file: File, path: Path) -> None:
        """Consume the asynchronous upload pipeline until the upload operation finishes."""

        async for _ in file_upload(file, path):
            pass

    def get_descendant_directories(self: StoreLimitlessFS, path: Path) -> list[Directory]:
        """Collect descendants before a directory path is changed."""

        descendants: list[Directory] = []
        pending: list[Path] = [path]

        while pending:
            parent_path: Path = pending.pop()
            children: list[Directory] = self.run(Directory.get_all(self.user.username, directory=parent_path))

            for child in children:
                descendants.append(child)
                pending.append(child.path)

        return descendants

    def rename_path(self: StoreLimitlessFS, src: str, dst: str) -> None:
        """Rename or move a file or directory while updating all affected directory paths."""

        src: str = self.ftpnorm(src)
        dst: str = self.ftpnorm(dst)

        if src == "/" or dst == "/":
            raise OSError(1, "Permission denied", src)

        if self.lexists(dst):
            raise OSError(17, "File exists", dst)

        parent: str
        name: str
        parent, _, name = dst.rpartition("/")
        parent = parent or "/"
        destination_directory: Directory | None = self.get_directory(parent)

        if destination_directory is None:
            raise OSError(2, "No such file or directory", parent)

        file: File | None = self.get_file(src)

        if file is not None:
            file.name = name
            file.directory_id = cast(int, destination_directory.id)
            file.modified_at = datetime.now(timezone.utc)
            self.run(file.save())
            return

        source_directory: Directory | None = self.get_directory(src)

        if source_directory is None:
            raise OSError(2, "No such file or directory", src)

        old_path: Path = source_directory.path
        new_path: Path = destination_directory.path / name
        descendants: list[Directory] = self.get_descendant_directories(old_path)
        source_directory.path = new_path
        source_directory.modified_at = datetime.now(timezone.utc)
        self.run(source_directory.save())

        for descendant in descendants:
            relative_path: Path = descendant.path.relative_to(old_path)
            descendant.path = new_path / relative_path
            descendant.modified_at = datetime.now(timezone.utc)
            self.run(descendant.save())
