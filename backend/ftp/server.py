"""Threaded lifecycle wrapper for the StoreLimitless FTP server."""

from asyncio import AbstractEventLoop
from threading import Thread

from pyftpdlib.servers import FTPServer

from .auth import StoreLimitlessAuthorizer
from .handler import StoreLimitlessHandler


class StoreLimitlessFTPServer(Thread):
    """Run the FTP listener in a dedicated daemon thread."""

    def __init__(self, loop: AbstractEventLoop, host: str = "0.0.0.0", port: int = 2121) -> None:
        """Configure the authorizer, event loop, listener address, and FTP handler."""

        super().__init__(daemon=True)
        self.authorizer = StoreLimitlessAuthorizer()
        StoreLimitlessHandler.authorizer = self.authorizer
        StoreLimitlessHandler.event_loop = loop
        self.server = FTPServer((host, port), StoreLimitlessHandler)

    def run(self) -> None:
        """Serve FTP requests until the server is stopped."""

        self.server.serve_forever()

    def stop(self) -> None:
        """Close the FTP listener and its active sessions."""

        self.server.close()
