from datetime import datetime
from logging import basicConfig, INFO

from core.module import Module
from core.settings import LOG_HANDLER, LOG_PATH
from filelock import BaseFileLock, FileLock

LOCK: BaseFileLock = FileLock("logs.txt.lock")
basicConfig(level=INFO, filename=LOG_PATH, filemode="a", format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")


def write_log(level: str, module: type[Module], func: str, user: str, message: str) -> None:
    with LOCK:
        LOG_HANDLER.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{module}] [{level}] [{func}] [{user}] {message}\n")
        LOG_HANDLER.flush()
