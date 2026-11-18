from asyncio import Task
from pathlib import Path
from traceback import format_exc
from typing import AsyncGenerator, Callable, cast
from urllib.parse import urlparse, ParseResult

from backend.database.models import File, User
from core.data_center import DataCenter
from core.utils import write_log, Progress, upload_growing_file


async def file_upload(file: File, file_path: Path, upload_task: Task[None] | None = None,
                      intermediate: bool = False) -> AsyncGenerator[Progress, None]:
    data_center: DataCenter = DataCenter(file.data_center)
    write_log("INFO", data_center, "UPLOAD", file.username, f"Got file: {file}")

    try:
        write_log("INFO", data_center, "UPLOAD", file.username, f"Found local file: {file_path.name}")
        progress = Progress("Uploading File", 0)

        async for progress_value in upload_growing_file(file=file, path=file_path, data_center=data_center, progress=progress, producer=upload_task):
            yield progress_value

        if not intermediate:
            file.save()
            write_log("INFO", data_center, "UPLOAD", file.username, f"Upload complete `{file_path.name}`")

        file_path.unlink(missing_ok=True)

    except Exception as e:
        write_log("ERROR", data_center, "UPLOAD", file.username, f"Unhandled exception: {e}\n{format_exc()}")


async def link_upload(file: File, link: str) -> AsyncGenerator[Progress, None]:
    from core.utils.download_ import download_google_drive, download_youtube

    user: User = cast(User, User.get(file.username))
    data_center: DataCenter = DataCenter(file.data_center)
    write_log("INFO", data_center, "DOWNLOAD", user.username, f"Got link: {link}")

    try:
        write_log("INFO", data_center, "DOWNLOAD", user.username, f"Starting download from `{link}`")
        parsed: ParseResult = urlparse(link)
        host: str = cast(str, parsed.netloc.lower().removeprefix("www."))

        if host == "drive.google.com":
            downloader: Callable = download_google_drive

        elif host in {"youtube.com", "youtu.be", "m.youtube.com"}:
            downloader: Callable = download_youtube

        else:
            raise ValueError(f"Unsupported link: {link}")

        async for progress in downloader(file, link):
            yield progress

        file.save()
        write_log("INFO", data_center, "DOWNLOAD", user.username, f"Download and upload complete `{file.name}`")

    except Exception as e:
        write_log("ERROR", data_center, "DOWNLOAD", user.username, f"Unhandled exception: {e}\n{format_exc()}")
        raise
