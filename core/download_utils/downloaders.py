import asyncio
from pathlib import Path
from traceback import format_exc
from typing import AsyncGenerator, BinaryIO

import gdown
import libtorrent as lt

from backend.database import File
from core.data_center import DataCenter
from core.config import TRANSFER_PATH
from core.transfer import upload
from core.utils import write_log


async def download_google_drive(file: File, link: str) -> AsyncGenerator[float | int, None]:
    data_center: type[DataCenter] = DataCenter(file.data_center)
    write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Starting download: {link}")

    try:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[float | None] = asyncio.Queue()
        next_log: float = 10.0
        next_yield: float = 1.0

        def progress(downloaded: int, total: int | None):
            nonlocal next_log, next_yield

            if not total:
                return

            value: float = round((downloaded / total) * 100, 2)

            if value >= next_log:
                write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Google Drive download ({value:.1f}%)")
                next_log += 10

            if value >= next_yield:
                loop.call_soon_threadsafe(queue.put_nowait, value)
                next_yield += 1

        download_task = asyncio.create_task(asyncio.to_thread(lambda: gdown.download(link, quiet=True, progress=progress)))

        while not download_task.done():
            try:
                value = await asyncio.wait_for(queue.get(), timeout=0.1)

                if value is not None:
                    yield value

            except asyncio.TimeoutError:
                pass

        downloaded: str | BinaryIO | None = await download_task

        while not queue.empty():
            value = queue.get_nowait()

            if value is not None:
                yield value

        if not isinstance(downloaded, str):
            raise OSError("Google Drive download failed")

        file.name = Path(downloaded).name
        target_path: Path = TRANSFER_PATH / file.name
        Path(downloaded).replace(target_path)
        write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Google Drive download complete `{file.name}`")

        async for progress in upload(file):
            yield progress

    except Exception as e:
        write_log("ERROR", data_center, "DOWNLOAD", str(file.username), f"Unhandled exception: {e}\n{format_exc()}")


async def download_torrent(file: File, link: str) -> AsyncGenerator[float | int, None]:
    data_center: type[DataCenter] = DataCenter(file.data_center)
    write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Starting torrent download: {link}")

    try:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[float | None] = asyncio.Queue()
        next_log: float = 10.0
        next_yield: float = 1.0

        def download():
            session = lt.session()
            params = lt.parse_magnet_uri(link)
            params.save_path = str(TRANSFER_PATH)
            handle = session.add_torrent(params)

            while True:
                status = handle.status()
                value: float = round(status.progress * 100, 2)
                loop.call_soon_threadsafe(queue.put_nowait, value)

                if status.is_seeding:
                    break

                import time
                time.sleep(0.5)

            return session, handle

        download_task = asyncio.create_task(asyncio.to_thread(download))

        while not download_task.done():
            try:
                value = await asyncio.wait_for(queue.get(), timeout=0.1)

                if value is None:
                    continue

                if value >= next_log:
                    write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Torrent download ({value:.1f}%)")
                    next_log += 10

                if value >= next_yield:
                    yield value
                    next_yield += 1

            except asyncio.TimeoutError:
                pass

        session, handle = await download_task

        while not queue.empty():
            value = queue.get_nowait()

            if value is not None and value >= next_yield:
                yield value
                next_yield += 1

        if not handle.is_valid():
            raise OSError("Torrent download failed")

        torrent_info = handle.torrent_file()

        if torrent_info is None:
            raise OSError("Failed to obtain torrent metadata")

        if torrent_info.num_files() != 1:
            raise OSError("Only single-file torrents are supported")

        file.name = Path(torrent_info.layout().file_path(0)).name

        target_path: Path = TRANSFER_PATH / file.name
        downloaded_path: Path = TRANSFER_PATH / torrent_info.layout().file_path(0)

        if downloaded_path != target_path:
            downloaded_path.replace(target_path)

        write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Torrent download complete `{file.name}`")

        async for progress in upload(file):
            yield progress

    except Exception as e:
        write_log("ERROR", data_center, "DOWNLOAD", str(file.username), f"Unhandled exception: {e}\n{format_exc()}")
