"""File-object adapter that stages FTP uploads locally before handing them to StoreLimitless."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from types import TracebackType
from typing import TYPE_CHECKING, IO

from core.data_center import Database
from core.utils import sanitize_sensitive_urls, write_log

if TYPE_CHECKING:
    from backend.database.models.file import File
    from backend.ftp.filesystem.filesystem import StoreLimitlessFS


class FTPUploadFile:
    """Wrap a local temporary file with the file methods expected by pyftpdlib."""

    def __init__(self, filesystem: StoreLimitlessFS, file: File, path: Path, mode: str) -> None:
        """Initialize upload state and open the temporary file in the requested mode."""

        self.filesystem: StoreLimitlessFS = filesystem
        self.file: File = file
        self.path: Path = path
        self.mode: str = mode
        self.name: str = str(path)
        self.closed: bool = False
        self._lock: Lock = Lock()
        self.file_object: IO = path.open(mode)
        write_log(
            "INFO",
            Database,
            "FTP UPLOAD",
            file.username,
            f"Opened staging file name={file.name!r} path={path.name!r} mode={mode}",
        )

    def read(self, size: int = -1) -> bytes:
        """Read bytes from the staged file."""

        return self.file_object.read(size)

    def readinto(self, buffer: bytearray | memoryview) -> int:
        """Read bytes into a writable buffer."""

        return self.file_object.readinto(buffer)

    def write(self, data: bytes | bytearray | memoryview) -> int:
        """Write bytes to the staged file unless it has been closed."""

        if self.closed:
            raise ValueError("I/O operation on closed file")

        return self.file_object.write(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        """Move the staged file's read/write position."""

        return self.file_object.seek(offset, whence)

    def tell(self) -> int:
        """Return the staged file's current position."""

        return self.file_object.tell()

    def flush(self) -> None:
        """Flush buffered data to the staged file."""

        return self.file_object.flush()

    def fileno(self) -> int:
        """Return the underlying file descriptor."""

        return self.file_object.fileno()

    def readable(self) -> bool:
        """Report whether the underlying file can be read."""

        return self.file_object.readable()

    def writable(self) -> bool:
        """Report whether the underlying file can be written."""

        return self.file_object.writable()

    def seekable(self) -> bool:
        """Report whether the underlying file supports seeking."""

        return self.file_object.seekable()

    def close(self) -> None:
        """Close the staged file once, then finalize its upload through the virtual filesystem."""

        with self._lock:
            if self.closed:
                return

            self.closed = True

            try:
                self.file_object.flush()

            finally:
                self.file_object.close()

        try:
            self.filesystem.finish_upload(self.file, self.path)
            write_log(
                "INFO",
                Database,
                "FTP UPLOAD",
                self.file.username,
                f"Staging finalized and upload handed off name={self.file.name!r} "
                f"staged_bytes={self.path.stat().st_size if self.path.exists() else 0}",
            )
        except Exception as error:
            write_log(
                "ERROR",
                Database,
                "FTP UPLOAD",
                self.file.username,
                f"Could not finalize FTP upload name={self.file.name!r} "
                f"error={type(error).__name__}: {sanitize_sensitive_urls(str(error))[:250]}",
            )
            raise

    def __enter__(self) -> FTPUploadFile:
        """Return this upload object for use in a context manager."""

        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close and finalize the upload when leaving a context manager."""

        self.close()
