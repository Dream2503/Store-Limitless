import asyncio
from datetime import datetime, timezone
from mimetypes import guess_type
from os import stat_result
from stat import S_IFDIR, S_IFREG
from time import time

from backend.database.models import Directory, File
from backend.ftp.filesystem.download import FTPDownloadFile
from backend.ftp.filesystem.upload import FTPUploadFile
from core.config import TRANSFER_PATH
from core.transfer import file_upload
from pyftpdlib.filesystems import AbstractedFS, FilesystemError


class StoreLimitlessFS(AbstractedFS):
    def __init__(self, root: str, cmd_channel) -> None:
        super().__init__(root, cmd_channel)
        self.user = cmd_channel.store_limitless_user
        self.event_loop = cmd_channel.event_loop
        TRANSFER_PATH.mkdir(parents=True, exist_ok=True)

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
        return future.result()

    def _get_directory(self, path: str):
        path = self.ftpnorm(path)

        if path == "/":
            return self._run(self.user.home)

        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        parent_directory = self._get_directory(parent)

        if parent_directory is None:
            return None

        directories = self._run(Directory.get_all(self.user.username, parent_directory.path))
        return next((directory for directory in directories if directory.path.name == name), None)

    def _get_file(self, path: str):
        path = self.ftpnorm(path)

        if path == "/":
            return None

        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        directory = self._get_directory(parent)

        if directory is None:
            return None

        files = self._run(File.get_all(self.user.username, directory.id))
        return next((file for file in files if file.name == name), None)

    def _get_transfer_path(self, file):
        directory = TRANSFER_PATH / self.user.username
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{file.id}_{file.name}"

    def _finish_upload(self, file, path):
        try:
            size = path.stat().st_size

            if size == 0:
                path.unlink(missing_ok=True)
                return

            file.size = size
            file.modified_at = datetime.now(timezone.utc)
            self._run(self._upload_file(file, path))
        except Exception:
            path.unlink(missing_ok=True)
            raise

    async def _upload_file(self, file, path):
        async for _ in file_upload(file, path):
            pass

    def listdir(self, path):
        path = self.ftpnorm(path)
        directory = self._get_directory(path)

        if directory is None:
            raise OSError(2, "No such file or directory", path)

        directories = self._run(Directory.get_all(self.user.username, directory.path))
        files = self._run(File.get_all(self.user.username, directory.id))
        return [directory.path.name for directory in directories] + [file.name for file in files]

    def isdir(self, path):
        path = self.ftpnorm(path)
        return path == "/" or self._get_directory(path) is not None

    def isfile(self, path):
        path = self.ftpnorm(path)
        return path != "/" and self._get_file(path) is not None

    def islink(self, path):
        return False

    def lexists(self, path):
        path = self.ftpnorm(path)
        return path == "/" or self._get_directory(path) is not None or self._get_file(path) is not None

    def exists(self, path):
        return self.lexists(path)

    def getsize(self, path):
        path = self.ftpnorm(path)

        if path == "/":
            return 0

        file = self._get_file(path)

        if file is not None:
            return file.size

        if self._get_directory(path) is not None:
            return 0

        raise OSError(2, "No such file or directory", path)

    def getmtime(self, path):
        path = self.ftpnorm(path)

        if path == "/":
            return time()

        file = self._get_file(path)

        if file is not None:
            return file.modified_at.timestamp()

        directory = self._get_directory(path)

        if directory is not None:
            return directory.modified_at.timestamp()

        raise OSError(2, "No such file or directory", path)

    def realpath(self, path):
        return self.ftpnorm(path)

    def validpath(self, path):
        return True

    def stat(self, path):
        path = self.ftpnorm(path)

        if path == "/":
            now = int(time())
            return stat_result((S_IFDIR | 0o755, 0, 0, 1, 0, 0, 0, now, now, now))

        directory = self._get_directory(path)

        if directory is not None:
            modified = int(directory.modified_at.timestamp())
            return stat_result((S_IFDIR | 0o755, directory.id, 0, 1, 0, 0, 0, modified, modified, modified))

        file = self._get_file(path)

        if file is not None:
            modified = int(file.modified_at.timestamp())
            return stat_result((S_IFREG | 0o644, file.id, 0, 1, 0, 0, file.size, modified, modified, modified))

        raise OSError(2, "No such file or directory", path)

    def lstat(self, path):
        return self.stat(path)

    def chdir(self, path):
        path = self.ftpnorm(path)

        if self._get_directory(path) is None:
            raise OSError(2, "No such file or directory", path)

        self.cwd = path

    def mkdir(self, path):
        path = self.ftpnorm(path)

        if path == "/" or self.lexists(path):
            raise OSError(17, "File exists", path)

        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        directory = self._get_directory(parent)

        if directory is None:
            raise OSError(2, "No such file or directory", parent)

        self._run(Directory(path=directory.path / name, modified_at=datetime.now(timezone.utc), username=self.user.username).save())

    def remove(self, path):
        path = self.ftpnorm(path)
        file = self._get_file(path)

        if file is None:
            if self._get_directory(path) is not None:
                raise OSError(21, "Is a directory", path)
            raise OSError(2, "No such file or directory", path)

        self._run(file.move_to_trash())

    def rmdir(self, path):
        path = self.ftpnorm(path)

        if path == "/":
            raise OSError(1, "Permission denied", path)

        directory = self._get_directory(path)

        if directory is None:
            raise OSError(2, "No such file or directory", path)

        home = self._run(self.user.home)

        if directory.id == home.id:
            raise OSError(1, "Permission denied", path)

        self._run(directory.move_to_trash())

    def rename(self, src, dst):
        src = self.ftpnorm(src)
        dst = self.ftpnorm(dst)

        if src == "/" or dst == "/":
            raise OSError(1, "Permission denied", src)

        if self.lexists(dst):
            raise OSError(17, "File exists", dst)

        parent, _, name = dst.rpartition("/")
        parent = parent or "/"
        directory = self._get_directory(parent)

        if directory is None:
            raise OSError(2, "No such file or directory", parent)

        file = self._get_file(src)

        if file is not None:
            file.name = name
            file.directory_id = directory.id
            file.modified_at = datetime.now(timezone.utc)
            self._run(file.save())
            return

        source_directory = self._get_directory(src)

        if source_directory is None:
            raise OSError(2, "No such file or directory", src)

        source_directory.path = directory.path / name
        source_directory.modified_at = datetime.now(timezone.utc)
        self._run(source_directory.save())

    def chmod(self, path, mode):
        if not self.lexists(path):
            raise OSError(2, "No such file or directory", path)
        raise FilesystemError("chmod is not supported by StoreLimitless.")

    def open(self, filename, mode):
        filename = self.ftpnorm(filename)

        if "r" in mode:
            file = self._get_file(filename)

            if file is None:
                raise OSError(2, "No such file or directory", filename)

            return FTPDownloadFile(file)

        if "w" in mode or "a" in mode:
            parent, _, name = filename.rpartition("/")
            parent = parent or "/"

            directory = self._get_directory(parent)

            if directory is None:
                raise OSError(2, "No such file or directory", parent)

            existing = self._get_file(filename)

            if existing is not None:
                if "w" in mode:
                    self._run(existing.move_to_trash())
                else:
                    raise OSError(17, "File exists", filename)

            file = File(directory_id=directory.id, name=name, type=guess_type(name)[0] or "application/octet-stream", size=0,
                        modified_at=datetime.now(timezone.utc), links=[], data_center=getattr(self.user, "data_center", None) or "Discord",
                        username=self.user.username)

            transfer_path = self._get_transfer_path(file)

            if "a" in mode:
                transfer_path.touch()
            else:
                transfer_path.write_bytes(b"")

            return FTPUploadFile(self, file, transfer_path, "ab" if "a" in mode else "wb")

        raise FilesystemError(f"Unsupported file mode: {mode}")
