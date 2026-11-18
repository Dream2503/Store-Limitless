from asyncio import AbstractEventLoop
from threading import Thread

from pyftpdlib.servers import FTPServer

from .auth import StoreLimitlessAuthorizer
from .handler import StoreLimitlessHandler


class StoreLimitlessFTPServer(Thread):
    def __init__(self, loop: AbstractEventLoop, host: str = "0.0.0.0", port: int = 2121) -> None:
        super().__init__(daemon=True)

        self.authorizer = StoreLimitlessAuthorizer()
        StoreLimitlessHandler.authorizer = self.authorizer
        StoreLimitlessHandler.event_loop = loop

        self.server = FTPServer((host, port), StoreLimitlessHandler)

    def run(self) -> None:
        self.server.serve_forever()

    def stop(self) -> None:
        self.server.close_all()
