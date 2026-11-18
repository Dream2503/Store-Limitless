"""FTP command-handler integration with StoreLimitless authentication and filesystem."""

from asyncio import AbstractEventLoop

from pyftpdlib.handlers import FTPHandler

from backend.database.models import User
from backend.ftp.filesystem.filesystem import StoreLimitlessFS
from core.data_center import Database
from core.utils import write_log


class StoreLimitlessHandler(FTPHandler):
    """Connect pyftpdlib commands to the StoreLimitless virtual filesystem."""

    abstracted_fs: type[StoreLimitlessFS] = StoreLimitlessFS
    event_loop: AbstractEventLoop
    store_limitless_user: User | None = None

    def on_logout(self, username: str) -> None:
        """Clear the authenticated user reference when the FTP session ends."""

        write_log("INFO", Database, "FTP AUTH", username, "FTP session closed")
        self.store_limitless_user = None
