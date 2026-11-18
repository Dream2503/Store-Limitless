from datetime import datetime
from logging import basicConfig, getLogger, INFO, WARNING

from filelock import BaseFileLock, FileLock

from core.data_center import DataCenter
from core.settings import LOG_HANDLER, LOG_PATH

LOCK: BaseFileLock = FileLock("logs.txt.lock")
basicConfig(level=INFO, filename=LOG_PATH, filemode="a", format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
getLogger("httpx").setLevel(WARNING)


def write_log(level: str, data_center: type[DataCenter], func: str, user: str, message: str) -> None:
    with LOCK:
        LOG_HANDLER.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{data_center}] [{level}] [{func}] [{user}] {message}\n")
        LOG_HANDLER.flush()
