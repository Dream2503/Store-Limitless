from time import sleep
from typing import Any, Generator

from backend.database import File
from core.data_center import Discord, Telegram
from core.utils import write_log


def upload(file: File) -> Generator[float, Any, None]:
    write_log("INFO", file.data_center, "UPLOAD", str(file.uid), f"Got file: {file}")

    match file.data_center:
        case Discord.NAME:
            pass

        case Telegram.NAME:
            pass

    for i in range(10):
        sleep(0.5)
        yield float(i)


def download(file: File) -> Generator[float, Any, None]:
    write_log("INFO", file.data_center, "DOWNLOAD", str(file.uid), f"Got file: {file}")

    match file.data_center:
        case Discord.NAME:
            pass

        case Telegram.NAME:
            pass

    for i in range(10):
        sleep(0.5)
        yield float(i)
