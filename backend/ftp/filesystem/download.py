"""Seekable, read-only file object that downloads byte ranges through the StoreLimitless stream API."""

from __future__ import annotations

from asyncio import AbstractEventLoop
from types import TracebackType

from backend.database.models.file import File
from backend.ftp.filesystem.utils import fill_buffer
from backend.server.security import create_public_stream_token


class FTPDownloadFile:
    """Provide a seekable read-only file interface over the public byte-range streaming endpoint."""

    RANGE_CHUNK_SIZE: int = 1024 * 1024

    def __init__(self, file: File, event_loop: AbstractEventLoop) -> None:
        """Initialize the file position, signed stream token, application event loop, and read buffer."""

        self.file: File = file
        self.name: str = file.name
        self.mode: str = "rb"
        self.position: int = 0
        self.closed: bool = False
        self.token: str = create_public_stream_token(file=file, username=file.username)
        self.event_loop: AbstractEventLoop = event_loop
        self._buffer_start: int = 0
        self._buffer: bytes = b""

    def read(self, size: int = -1) -> bytes:
        """Read bytes from the current position, fetching ranges when needed."""

        if self.closed:
            raise ValueError("I/O operation on closed file")

        if self.position >= self.file.size:
            return b""

        if size < 0:
            size = self.file.size - self.position

        if size <= 0:
            return b""

        size = min(size, self.file.size - self.position)
        data: bytearray = bytearray()

        while len(data) < size:
            buffer_offset: int = self.position - self._buffer_start

            if 0 <= buffer_offset < len(self._buffer):
                available: int = len(self._buffer) - buffer_offset
                amount: int = min(available, size - len(data))
                data.extend(self._buffer[buffer_offset:buffer_offset + amount])
                self.position += amount
                continue

            fill_buffer(self, self.position)

        return bytes(data)

    def readinto(self, buffer: bytearray | memoryview) -> int:
        """Read bytes into a writable buffer and return the number of bytes copied."""

        data: bytes = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        """Move the read position using the standard file seek modes."""

        if self.closed:
            raise ValueError("I/O operation on closed file")

        if whence == 0:
            position: int = offset

        elif whence == 1:
            position = self.position + offset

        elif whence == 2:
            position = self.file.size + offset

        else:
            raise ValueError("Invalid whence")

        if position < 0:
            raise ValueError("Invalid seek position")

        self.position = position
        return position

    def tell(self) -> int:
        """Return the current read position."""

        return self.position

    def readable(self) -> bool:
        """Report that the file supports reading."""

        return True

    def writable(self) -> bool:
        """Report that the file is read-only."""

        return False

    def seekable(self) -> bool:
        """Report that the file supports seeking."""

        return True

    def close(self) -> None:
        """Mark the file object as closed."""

        self.closed = True
        self._buffer = b""

    def fileno(self) -> int:
        """Report that this remote file object has no local file descriptor."""

        raise OSError("No file descriptor")

    def __enter__(self) -> FTPDownloadFile:
        """Return this file object for use in a context manager."""

        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the file object when leaving a context manager."""

        self.close()

    def __repr__(self) -> str:
        """Return a concise representation of the file and its current position."""

        return f"<FTPDownloadFile {self.name!r} pos={self.position}/{self.file.size}>"
