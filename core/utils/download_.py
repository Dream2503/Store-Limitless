from asyncio import Task, to_thread, create_task, sleep
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path
from traceback import format_exc
from typing import Any, AsyncGenerator, cast

import gdown
import requests
from yt_dlp import YoutubeDL

import core.transfer
from backend.database.models import File
from core.config import TRANSFER_PATH
from core.data_center import DataCenter
from core.utils import Progress, upload_growing_file, write_log, GOOGLE_API_KEY


async def download_google_drive(file: File, link: str) -> AsyncGenerator[Progress, None]:
    data_center: DataCenter = DataCenter(file.data_center)
    temp_dir: Path = TRANSFER_PATH / file.username / "gdown"
    temp_dir.mkdir(parents=True, exist_ok=True)

    for path in temp_dir.iterdir():
        if path.is_file():
            path.unlink()

    try:
        progress: Progress = Progress(f"Uploading File", 0)
        file_id: str = link.split("/file/d/")[1].split("/")[0]
        response = await to_thread(
            requests.get,
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"fields": "name,size", "key": GOOGLE_API_KEY},
            timeout=30
        )

        if not response.ok:
            raise OSError(f"Google Drive API error: {response.status_code}: {response.text}")

        data: dict[str, str] = response.json()

        if "size" not in data:
            raise OSError("Could not determine Google Drive file size")

        file.name = data.get("name") or file_id
        total_size: int = int(data["size"])
        progress.total = total_size
        download_task: Task[Any] = create_task(to_thread(gdown.download, link, output=str(temp_dir), quiet=False))

        while True:
            files = [path for path in temp_dir.iterdir() if path.is_file()]

            if files:
                output = files[0]
                break

            await sleep(0.1)

        async for progress_value in upload_growing_file(file=file,
                                                        path=output,
                                                        data_center=data_center,
                                                        progress=progress,
                                                        producer=download_task,
                                                        total_size=total_size):
            yield progress_value

        file.type = guess_type(file.name)[0] or "application/octet-stream"
        file.modified_at = datetime.now(timezone.utc)

    except Exception as e:
        write_log("ERROR", data_center, "UPLOAD", file.username, f"Unhandled exception: {e}\n{format_exc()}")
        raise

    finally:
        if temp_dir.exists():
            for path in temp_dir.iterdir():
                if path.is_file():
                    path.unlink()


async def download_youtube(file: File, link: str) -> AsyncGenerator[Progress, None]:
    temp_dir: Path = TRANSFER_PATH / file.username / "yt-dlp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    for path in temp_dir.iterdir():
        if path.is_file():
            path.unlink()

    download_progress: Progress = Progress("Downloading Video", 0)
    merge_progress: Progress = Progress("Merging Audio and Video", 0)
    current_progress: dict[str, Progress] = {"value": download_progress}

    def hook(data: dict) -> None:
        filename: str = data.get("filename", "")

        if filename.endswith((".vtt", ".srt", ".ass", ".ttml", ".srv1", ".srv2", ".srv3")):
            return

        info: dict = data.get("info_dict", {})
        vcodec: str | None = info.get("vcodec")
        acodec: str | None = info.get("acodec")

        if vcodec != "none" and acodec == "none":
            if current_progress["value"] is not download_progress:
                current_progress["value"] = download_progress
                download_progress.message = "Downloading Video"

        elif acodec != "none" and vcodec == "none":
            if current_progress["value"] is download_progress:
                current_progress["value"] = Progress("Downloading Audio", 0)

        progress: Progress = current_progress["value"]

        if data["status"] == "downloading":
            progress.total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            progress.transfer = data.get("downloaded_bytes", 0)

        elif data["status"] == "finished":
            progress.transfer = progress.total

    def postprocessor_hook(data: dict) -> None:
        if data.get("status") == "started":
            current_progress["value"] = merge_progress
            merge_progress.message = "Merging Audio and Video"

        elif data.get("status") == "finished":
            merge_progress.transfer = merge_progress.total

    def download() -> None:
        with YoutubeDL(cast(Any, {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best",
            "outtmpl": str(temp_dir / "%(title)s.%(ext)s"),
            "merge_output_format": "mkv",
            "writesubtitles": True,
            "subtitleslangs": ["en"],
            "embedsubtitles": True,
            "progress_hooks": [hook],
            "postprocessor_hooks": [postprocessor_hook],
        })) as ydl:
            ydl.download([link])

    try:
        download_task: Task[Any] = create_task(to_thread(download))

        while not download_task.done():
            yield current_progress["value"]
            await sleep(0.1)

        await download_task

        files: list[Path] = [path for path in temp_dir.iterdir() if path.is_file()]

        if not files:
            raise OSError("YouTube download produced no file")

        output: Path = max(files, key=lambda path: path.stat().st_size)
        file.name = output.name
        file.size = output.stat().st_size
        file.type = guess_type(file.name)[0] or "application/octet-stream"
        yield current_progress["value"]

        async for progress in core.transfer.file_upload(file, output, intermediate=True):
            yield progress

    finally:
        if temp_dir.exists():
            for path in temp_dir.iterdir():
                if path.is_file():
                    path.unlink()

# async def download_torrent(file: File, link: str) -> AsyncGenerator[float | int, None]:
#     data_center: type[DataCenter] = DataCenter(file.data_center)
#     write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Starting torrent download: {link}")
#
#     try:
#         loop = asyncio.get_running_loop()
#         queue: asyncio.Queue[float | None] = asyncio.Queue()
#         next_log = 10.0
#         next_yield = 1.0
#
#         def download():
#             session = lt.session()
#             params = lt.parse_magnet_uri(link)
#             params.save_path = str(TRANSFER_PATH)
#             handle = session.add_torrent(params)
#
#             while True:
#                 status = handle.status()
#                 value = round(status.progress * 100, 2)
#                 loop.call_soon_threadsafe(queue.put_nowait, value)
#
#                 if status.is_seeding:
#                     break
#
#                 import time
#                 time.sleep(0.5)
#
#             return session, handle
#
#         download_task = asyncio.create_task(asyncio.to_thread(download))
#
#         while not download_task.done():
#             try:
#                 value = await asyncio.wait_for(queue.get(), timeout=0.1)
#
#                 if value is None:
#                     continue
#
#                 if value >= next_log:
#                     write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Torrent download ({value:.1f}%)")
#                     next_log += 10
#
#                 if value >= next_yield:
#                     yield value
#                     next_yield += 1
#
#             except asyncio.TimeoutError:
#                 pass
#
#         session, handle = await download_task
#
#         while not queue.empty():
#             value = queue.get_nowait()
#
#             if value is not None and value >= next_yield:
#                 yield value
#                 next_yield += 1
#
#         if not handle.is_valid():
#             raise OSError("Torrent download failed")
#
#         torrent_info = handle.torrent_file()
#
#         if torrent_info is None:
#             raise OSError("Failed to obtain torrent metadata")
#
#         if torrent_info.num_files() != 1:
#             raise OSError("Only single-file torrents are supported")
#
#         file.name = Path(torrent_info.layout().file_path(0)).name
#
#         target_path = TRANSFER_PATH / file.name
#         downloaded_path = TRANSFER_PATH / torrent_info.layout().file_path(0)
#
#         if downloaded_path != target_path:
#             downloaded_path.replace(target_path)
#
#         write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Torrent download complete `{file.name}`")
#
#         async for progress in upload(file):
#             yield progress
#
#     except Exception as e:
#         write_log("ERROR", data_center, "DOWNLOAD", str(file.username), f"Unhandled exception: {e}\n{format_exc()}")
