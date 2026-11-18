import asyncio
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from traceback import format_exc
from typing import AsyncGenerator

import gdown
import libtorrent as lt
import requests

from backend.database import File, add_file, get_file
from core.config import GOOGLE_API_KEY, TRANSFER_PATH
from core.data_center import DataCenter
from core.transfer import upload
from core.utils import write_log


async def download_google_drive(file: File, link: str) -> AsyncGenerator[float | int, None]:
    data_center: type[DataCenter] = DataCenter(file.data_center)
    write_log("INFO", data_center, "DOWNLOAD", file.username, f"Starting download: {link}")
    downloaded_path: Path | None = None

    try:
        response = await asyncio.to_thread(requests.get, f"https://www.googleapis.com/drive/v3/files/{link.split('/file/d/')[1].split('/')[0]}",
                                           params={"fields": "size", "key": GOOGLE_API_KEY})
        response.raise_for_status()

        if "size" not in response.json():
            raise OSError("Could not determine Google Drive file size")

        total_size: int = int(response.json()["size"])
        temp_dir: Path = TRANSFER_PATH / file.username / "gdown"
        temp_dir.mkdir(parents=True, exist_ok=True)
        download_task = asyncio.create_task(asyncio.to_thread(gdown.download, link, output=str(temp_dir), quiet=True))
        part_file = None

        while not download_task.done():
            files = [path for path in temp_dir.iterdir() if path.is_file()]

            if files:
                part_file = max(files, key=lambda path: path.stat().st_mtime)
                break

            await asyncio.sleep(0.1)

        if part_file is None:
            downloaded = await download_task

            if not isinstance(downloaded, str):
                raise OSError("Google Drive download failed")

            downloaded_path = Path(downloaded)

        else:
            part = 0
            max_size = data_center.MAX_SIZE
            links: dict[int, str] = {}
            total_parts = (total_size + max_size - 1) // max_size

            while not download_task.done():
                size = part_file.stat().st_size
                limit = (part + 1) * max_size

                if size >= limit:
                    with part_file.open("rb") as buffer:
                        buffer.seek(part * max_size)
                        chunk = buffer.read(max_size)

                    msg_id = await data_center.upload(chunk, f"{part_file.name}.part{part}")
                    links[part] = msg_id
                    part += 1
                    progress = min(part * max_size, total_size) / total_size * 100
                    write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {part}/{total_parts}")
                    yield round(progress, 2)

                await asyncio.sleep(0.1)

            downloaded = await download_task

            if not isinstance(downloaded, str):
                raise OSError("Google Drive download failed")

            downloaded_path = Path(downloaded)
            file.name = downloaded_path.name
            file.size = downloaded_path.stat().st_size
            file.type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            file.modified_at = datetime.now(timezone.utc)
            size = file.size

            while size >= (part + 1) * max_size:
                with downloaded_path.open("rb") as buffer:
                    buffer.seek(part * max_size)
                    chunk = buffer.read(max_size)

                msg_id = await data_center.upload(chunk, f"{downloaded_path.name}.part{part}")
                links[part] = msg_id
                part += 1

                progress = min(part * max_size, total_size) / total_size * 100
                write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded part {part}/{total_parts}")
                yield round(progress, 2)

            start = part * max_size

            if size > start:
                with downloaded_path.open("rb") as buffer:
                    buffer.seek(start)
                    chunk = buffer.read()

                msg_id = await data_center.upload(chunk, f"{downloaded_path.name}.part{part}")
                links[part] = msg_id
                part += 1
                write_log("INFO", data_center, "UPLOAD", file.username, f"Uploaded final part {part}/{total_parts}")
                yield 100

            file.links = [links[i] for i in range(part)]

        if get_file(name=file.name, username=file.username):
            path = Path(file.name)
            stem, extension = path.stem, path.suffix
            i = 1

            while get_file(name=f"{stem}({i}){extension}", username=file.username):
                i += 1

            file.name = f"{stem}({i}){extension}"

        add_file(file)
        write_log("INFO", data_center, "DOWNLOAD", file.username, f"Download and upload complete `{file.name}`")

    except Exception as e:
        write_log("ERROR", data_center, "DOWNLOAD", file.username, f"Unhandled exception: {e}\n{format_exc()}")
        raise

    finally:
        if downloaded_path is not None and downloaded_path.exists():
            downloaded_path.unlink()


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
