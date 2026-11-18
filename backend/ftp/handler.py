from asyncio import AbstractEventLoop

from pyftpdlib.handlers import FTPHandler

from backend.database.models import User
from backend.ftp.filesystem.filesystem import StoreLimitlessFS


class StoreLimitlessHandler(FTPHandler):
    abstracted_fs = StoreLimitlessFS

    event_loop: AbstractEventLoop
    store_limitless_user: User | None = None

    def on_logout(self, username: str) -> None:
        self.store_limitless_user = None
