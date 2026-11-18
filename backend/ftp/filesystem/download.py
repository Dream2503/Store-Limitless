import requests

from backend.database.models.file import File
from backend.server.security import create_public_stream_token

PUBLIC_STREAM_URL = "http://127.0.0.1:8000/public/stream"


class FTPDownloadFile:
    def __init__(self, file: File):
        self.file = file
        self.name = file.name
        self.mode = "rb"
        self.position = 0
        self.closed = False
        self.token = create_public_stream_token(file=file, username=file.username)
        self.session = requests.Session()

    def _request(self, start: int, end: int) -> requests.Response:
        if self.closed:
            raise ValueError("I/O operation on closed file")

        response = self.session.get(
            f"{PUBLIC_STREAM_URL}/{self.token}",
            headers={"Range": f"bytes={start}-{end}"},
            stream=True,
            timeout=(10, 300),
        )

        response.raise_for_status()
        return response

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file")

        if self.position >= self.file.size:
            return b""

        if size < 0:
            size = self.file.size - self.position

        if size <= 0:
            return b""

        start = self.position
        end = min(start + size - 1, self.file.size - 1)
        response = self._request(start, end)

        try:
            data = bytearray()

            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    data.extend(chunk)

                if len(data) >= size:
                    break

            data = bytes(data[:size])
            self.position += len(data)
            return data
        finally:
            response.close()

    def seek(self, offset: int, whence: int = 0) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")

        if whence == 0:
            position = offset
        elif whence == 1:
            position = self.position + offset
        elif whence == 2:
            position = self.file.size + offset
        else:
            raise ValueError("Invalid whence")

        if not 0 <= position <= self.file.size:
            raise ValueError("Invalid seek position")

        self.position = position
        return position

    def tell(self) -> int:
        return self.position

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        if self.closed:
            return

        self.closed = True
        self.session.close()

    def fileno(self):
        raise OSError("No file descriptor")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __repr__(self):
        return f"<FTPDownloadFile {self.name!r} pos={self.position}/{self.file.size}>"
