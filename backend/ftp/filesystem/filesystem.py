"""Virtual FTP filesystem backed by StoreLimitless database models and transfer routines."""

from __future__ import annotations

from asyncio import AbstractEventLoop
from datetime import datetime, timezone
from mimetypes import guess_type
from os import stat_result
from pathlib import Path
from stat import S_IFDIR, S_IFREG
from time import time
from typing import TYPE_CHECKING, cast

from pyftpdlib.filesystems import AbstractedFS, FilesystemError

from backend.database.models import Directory, File, User
from backend.ftp.filesystem.download import FTPDownloadFile
from backend.ftp.filesystem.upload import FTPUploadFile
from backend.ftp.filesystem.utils import FilesystemUtils
from core.config import TRANSFER_PATH
from core.data_center import Database
from core.utils import write_log

if TYPE_CHECKING:
    from backend.ftp.handler import StoreLimitlessHandler


class StoreLimitlessFS(AbstractedFS, FilesystemUtils):
    """Expose each authenticated user's StoreLimitless directories and files as an FTP filesystem."""

    def __init__(self, root: str, cmd_channel: StoreLimitlessHandler) -> None:
        """Initialize the virtual filesystem with the authenticated user and application event loop."""

        super().__init__(root, cmd_channel)
        self.user: User = cast(User, cmd_channel.store_limitless_user)
        self.event_loop: AbstractEventLoop = cmd_channel.event_loop
        TRANSFER_PATH.mkdir(parents=True, exist_ok=True)

    def listdir(self, path: str) -> list[str]:
        """Return the immediate directory and file names for a virtual directory."""

        path: str = self.ftpnorm(path)
        directory: Directory | None = self.get_directory(path)

        if directory is None:
            raise OSError(2, "No such file or directory", path)

        directories: list[Directory] = self.run(Directory.get_all(self.user.username, directory=directory.path))
        files: list[File] = self.run(File.get_all(self.user.username, directory_id=directory.id))
        return [directory.path.name for directory in directories] + [file.name for file in files]

    def isdir(self, path: str) -> bool:
        """Report whether a virtual path represents a directory."""

        path: str = self.ftpnorm(path)
        return path == "/" or self.get_directory(path) is not None

    def isfile(self, path: str) -> bool:
        """Report whether a virtual path represents a file."""

        path: str = self.ftpnorm(path)
        return path != "/" and self.get_file(path) is not None

    def islink(self, path: str) -> bool:
        """Report that StoreLimitless does not expose symbolic links."""

        return False

    def lexists(self, path: str) -> bool:
        """Check whether a virtual path exists without following links."""

        path: str = self.ftpnorm(path)
        return path == "/" or self.get_directory(path) is not None or self.get_file(path) is not None

    def exists(self, path: str) -> bool:
        """Check whether a virtual path exists."""

        return self.lexists(path)

    def getsize(self, path: str) -> int:
        """Return a file's size, or zero for a directory."""

        path: str = self.ftpnorm(path)

        if path == "/":
            return 0

        file: File | None = self.get_file(path)

        if file is not None:
            return file.size

        if self.get_directory(path) is not None:
            return 0

        raise OSError(2, "No such file or directory", path)

    def getmtime(self, path: str) -> int | float:
        """Return the modification timestamp for a file or directory."""

        path: str = self.ftpnorm(path)

        if path == "/":
            return time()

        file: File | None = self.get_file(path)

        if file is not None:
            return file.modified_at.timestamp()

        directory: Directory | None = self.get_directory(path)

        if directory is not None:
            return directory.modified_at.timestamp()

        raise OSError(2, "No such file or directory", path)

    def realpath(self, path: str) -> str:
        """Return the normalized virtual FTP path."""

        return self.ftpnorm(path)

    def validpath(self, path: str) -> bool:
        """Validate a virtual path according to the filesystem's path policy."""

        return self.ftpnorm(path).startswith("/")

    def stat(self, path: str) -> stat_result:
        """Return filesystem metadata for the root, a directory, or a file."""

        path: str = self.ftpnorm(path)

        if path == "/":
            now: int = int(time())
            return stat_result((S_IFDIR | 0o755, 0, 0, 1, 0, 0, 0, now, now, now))

        directory: Directory | None = self.get_directory(path)

        if directory is not None:
            modified: int = int(directory.modified_at.timestamp())
            return stat_result((S_IFDIR | 0o755, directory.id, 0, 1, 0, 0, 0, modified, modified, modified))

        file: File | None = self.get_file(path)

        if file is not None:
            modified: int = int(file.modified_at.timestamp())
            return stat_result((S_IFREG | 0o644, file.id, 0, 1, 0, 0, file.size, modified, modified, modified))

        raise OSError(2, "No such file or directory", path)

    def lstat(self, path: str) -> stat_result:
        """Return metadata for a virtual path; links are not supported."""

        return self.stat(path)

    def chdir(self, path: str) -> None:
        """Change the FTP session's current working directory."""

        path: str = self.ftpnorm(path)

        if self.get_directory(path) is None:
            raise OSError(2, "No such file or directory", path)

        self.cwd = path

    def mkdir(self, path: str) -> None:
        """Create a directory under an existing parent directory."""

        path: str = self.ftpnorm(path)

        if path == "/" or self.lexists(path):
            raise OSError(17, "File exists", path)

        parent: str
        name: str
        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        directory: Directory | None = self.get_directory(parent)

        if directory is None:
            raise OSError(2, "No such file or directory", parent)

        self.run(Directory(path=directory.path / name, modified_at=datetime.now(timezone.utc), username=self.user.username).save())
        write_log("INFO", Database, "FTP FILESYSTEM", self.user.username, f"Created directory path={path}")

    def remove(self, path: str) -> None:
        """Move a file to the user's trash."""

        path: str = self.ftpnorm(path)
        file: File | None = self.get_file(path)

        if file is None:
            if self.get_directory(path) is not None:
                raise OSError(21, "Is a directory", path)

            raise OSError(2, "No such file or directory", path)

        self.run(file.move_to_trash())
        write_log("INFO", Database, "FTP FILESYSTEM", self.user.username, f"Moved file to trash path={path} file_id={file.id}")

    def rmdir(self, path: str) -> None:
        """Move a non-root, non-home directory to the user's trash."""

        path: str = self.ftpnorm(path)

        if path == "/":
            raise OSError(1, "Permission denied", path)

        directory: Directory | None = self.get_directory(path)

        if directory is None:
            raise OSError(2, "No such file or directory", path)

        home: Directory = self.run(self.user.home)

        if directory.id == home.id:
            raise OSError(1, "Permission denied", path)

        self.run(directory.move_to_trash())
        write_log("INFO", Database, "FTP FILESYSTEM", self.user.username, f"Moved directory to trash path={path} directory_id={directory.id}")

    def rename(self, src: str, dst: str) -> None:
        """Rename or move a file or directory to a destination path."""

        self.rename_path(src, dst)
        write_log("INFO", Database, "FTP FILESYSTEM", self.user.username, f"Renamed/moved virtual path src={src!r} dst={dst!r}")

    def chmod(self, path: str, mode: int) -> None:
        """Reject permission changes, which the virtual filesystem does not support."""

        if not self.lexists(path):
            raise OSError(2, "No such file or directory", path)

        raise FilesystemError("chmod is not supported by StoreLimitless.")

    def open(self, filename: str, mode: str) -> FTPDownloadFile | FTPUploadFile:
        """Open a virtual file for downloading or create a staged upload for writing."""

        filename: str = self.ftpnorm(filename)

        if "r" in mode:
            file: File | None = self.get_file(filename)

            if file is None:
                raise OSError(2, "No such file or directory", filename)

            write_log("INFO", Database, "FTP DOWNLOAD", self.user.username, f"Opened file_id={file.id} name={file.name!r} size={file.size}")
            return FTPDownloadFile(file, self.event_loop)

        if "w" in mode or "a" in mode:
            parent: str
            name: str
            parent, _, name = filename.rpartition("/")
            parent = parent or "/"
            directory: Directory | None = self.get_directory(parent)

            if directory is None:
                raise OSError(2, "No such file or directory", parent)

            existing: File | None = self.get_file(filename)

            if existing is not None:
                if "w" in mode:
                    self.run(existing.move_to_trash())

                else:
                    raise OSError(17, "File exists", filename)

            file: File = File(
                directory_id=cast(int, directory.id),
                name=name, type=guess_type(name)[0] or "application/octet-stream",
                size=0,
                modified_at=datetime.now(timezone.utc),
                links=[],
                data_center=getattr(self.user, "data_center", None) or "Discord",
                username=self.user.username
            )
            transfer_path: Path = self.get_transfer_path(file)

            if "a" in mode:
                transfer_path.touch()

            else:
                transfer_path.write_bytes(b"")

            return FTPUploadFile(self, file, transfer_path, "ab" if "a" in mode else "wb")

        raise FilesystemError(f"Unsupported file mode: {mode}")
