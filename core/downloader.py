from asyncio import sleep
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path
from traceback import format_exc
from typing import AsyncGenerator, Callable
from urllib.parse import urlparse, ParseResult

from backend.database import File, add_file
from core.data_center import DataCenter
from core.download_utils import download_google_drive, download_youtube, upload_part
from core.utils import write_log


async def download_link(file: File, link: str) -> AsyncGenerator[float | int, None]:
    data_center: DataCenter = DataCenter(file.data_center)
    write_log("INFO", data_center, "DOWNLOAD", file.username, f"Starting download: {link}")
    temp_dir: Path | None = None

    try:
        parsed: ParseResult = urlparse(link)
        host: str = parsed.netloc.lower().removeprefix("www.")

        if host == "drive.google.com":
            downloader: Callable = download_google_drive
            provider: str = "Google Drive"

        elif host in {"youtube.com", "youtu.be", "m.youtube.com"}:
            downloader: Callable = download_youtube
            provider: str = "YouTube"

        else:
            raise ValueError(f"Unsupported link: {link}")

        if provider == "YouTube":
            temp_dir, final_path, total_size, download_task, progress = await downloader(file, link)

            while not download_task.done():
                yield round(progress["value"] * 50, 2)
                await sleep(0.1)

            final_path, total_size = await download_task
            yield 50

            if not final_path.exists():
                raise OSError("YouTube download file not found")

            size: int = final_path.stat().st_size
            max_size: int = data_center.MAX_SIZE
            links: dict[int, str] = {}
            part: int = 0

            while part * max_size < size:
                start: int = part * max_size
                chunk_size: int = min(max_size, size - start)
                links[part] = await upload_part(file, data_center, final_path, part, chunk_size, size, file.name)
                part += 1
                yield round(50 + (start + chunk_size) / size * 50, 2)

        else:
            temp_dir, final_path, total_size, download_task = await downloader(file, link)
            max_size: int = data_center.MAX_SIZE
            part: int = 0
            links: dict[int, str] = {}

            while not download_task.done():
                files: list[Path] = [path for path in temp_dir.iterdir() if path.is_file()]

                if not files:
                    await sleep(0.1)
                    continue

                downloaded_path: Path = files[0]

                try:
                    size: int = downloaded_path.stat().st_size

                except FileNotFoundError:
                    continue

                while size >= (part + 1) * max_size:
                    links[part] = await upload_part(file, data_center, downloaded_path, part, max_size, total_size, file.name)
                    part += 1

                    if total_size:
                        yield round(min(part * max_size, total_size) / total_size * 100, 2)

                    else:
                        yield 0

                    try:
                        downloaded_path = next(path for path in temp_dir.iterdir() if path.is_file())
                        size = downloaded_path.stat().st_size

                    except (StopIteration, FileNotFoundError):
                        break

                await sleep(0.1)

            await download_task
            downloaded_path: Path = final_path

            if not downloaded_path.exists():
                raise OSError(f"{provider} download file not found")

            size = downloaded_path.stat().st_size

            while size >= (part + 1) * max_size:
                links[part] = await upload_part(file, data_center, downloaded_path, part, max_size, size, file.name)
                part += 1
                yield round(min(part * max_size, size) / size * 100, 2) if size else 0
                size = downloaded_path.stat().st_size

            start: int = part * max_size

            if size > start:
                links[part] = await upload_part(file, data_center, downloaded_path, part, size - start, size, file.name)
                part += 1

        file.size = size
        file.links = [links[i] for i in range(part)]
        file.type = guess_type(file.name)[0] or "application/octet-stream"
        file.modified_at = datetime.now(timezone.utc)
        add_file(file)
        write_log("INFO", data_center, "DOWNLOAD", file.username, f"Download and upload complete `{file.name}`")
        yield 100

    except Exception as e:
        write_log("ERROR", data_center, "DOWNLOAD", file.username, f"Unhandled exception: {e}\n{format_exc()}")
        raise

    finally:
        if temp_dir is not None and temp_dir.exists():
            for path in temp_dir.iterdir():
                if path.is_file():
                    path.unlink()
