"""Download remote links and upload their content to configured storage providers."""

from __future__ import annotations

from asyncio import Task, create_task, sleep, to_thread
from datetime import datetime, timezone
from email.message import Message
from mimetypes import guess_type
from pathlib import Path
from shutil import rmtree
from traceback import format_exc
from typing import Any, AsyncGenerator, cast
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import gdown
import requests
from yt_dlp import YoutubeDL

import core.transfer
from backend.database.models import File
from core.config import TRANSFER_PATH
from core.data_center import DataCenter
from core.utils import write_log
from core.utils.binaries import get_ffmpeg_path
from core.utils.env import GOOGLE_API_KEY
from core.utils.progress import Progress, upload_growing_file


async def download_unprocessed_link(file: File, link: str) -> AsyncGenerator[Progress, None]:
    """Download a Google Drive file or direct HTTP(S) file while uploading completed chunks."""

    data_center: type[DataCenter] | None = DataCenter(file.data_center)

    if data_center is None:
        raise ValueError(f"Unsupported data center: {file.data_center}")

    parsed = urlparse(link)

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Expected an HTTP(S) download URL: {link}")

    is_google_drive: bool = parsed.hostname.lower().removeprefix("www.") == "drive.google.com"
    temp_dir: Path = TRANSFER_PATH / file.username / "link-download" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    download_task: Task[Any] | None = None
    response: requests.Response | None = None
    output: Path = temp_dir / "pending-download"
    response_type: str | None = None
    total_size: int = 0

    def safe_filename(name: str) -> str:
        name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        return name if name not in {"", ".", ".."} else "download"

    try:
        if is_google_drive:
            file_id: str | None = None

            if "/file/d/" in parsed.path:
                file_id = parsed.path.split("/file/d/", 1)[1].split("/", 1)[0]

            if not file_id:
                file_id = parse_qs(parsed.query).get("id", [None])[0]

            if not file_id:
                raise ValueError("Could not determine the Google Drive file ID from the URL")

            metadata_response: requests.Response = await to_thread(
                requests.get,
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"fields": "name,size", "key": GOOGLE_API_KEY},
                timeout=30,
            )

            try:
                if not metadata_response.ok:
                    raise OSError(f"Google Drive API error: {metadata_response.status_code}: {metadata_response.text}")

                metadata: dict[str, Any] = metadata_response.json()

            finally:
                metadata_response.close()

            if "size" not in metadata:
                raise OSError("Could not determine Google Drive file size")

            file.name = safe_filename(str(metadata.get("name") or file_id))
            total_size = int(metadata["size"])
            output = temp_dir / file.name
            output.touch()

            def download_google_drive_file() -> None:
                downloaded: str | None = gdown.download(link, output=str(output), quiet=True)

                if downloaded is None or not output.is_file():
                    raise OSError("Google Drive download failed")

            download_task = create_task(to_thread(download_google_drive_file))

        else:
            response = await to_thread(
                requests.get,
                link,
                stream=True,
                allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"},
                timeout=(30, 60),
            )
            response.raise_for_status()
            disposition: Message = Message()
            disposition["content-disposition"] = response.headers.get("Content-Disposition", "")
            header_name: str | None = disposition.get_filename()
            url_name: str = unquote(urlparse(response.url).path.rstrip("/").rsplit("/", 1)[-1])
            file.name = safe_filename(header_name or url_name or file.name)
            response_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip() or None

            if response_type == "text/html" and not header_name and Path(file.name).suffix.lower() not in {".html", ".htm"}:
                raise ValueError("The URL returned an HTML page rather than a direct file download")

            try:
                total_size = max(0, int(response.headers.get("Content-Length", "0")))

            except ValueError:
                total_size = 0

            output = temp_dir / file.name
            output.touch()
            response_to_stream: requests.Response = response

            def stream_response() -> None:
                try:
                    with output.open("wb", buffering=0) as destination:
                        for chunk in response_to_stream.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                destination.write(chunk)

                finally:
                    response_to_stream.close()

            download_task = create_task(to_thread(stream_response))

        progress: Progress = Progress(file.name, "Downloading and uploading file", total_size)

        async for progress_value in upload_growing_file(file, output, data_center, progress, download_task, total_size):
            yield progress_value

        file.size = output.stat().st_size
        file.type = guess_type(file.name)[0] or response_type or "application/octet-stream"
        file.modified_at = datetime.now(timezone.utc)

    except Exception as error:
        write_log("ERROR", data_center, "UPLOAD", file.username, f"Unhandled exception: {error}\n{format_exc()}")
        raise

    finally:
        try:
            if response is not None:
                response.close()

            if download_task is not None:
                try:
                    await download_task

                except Exception:
                    pass

        finally:
            rmtree(temp_dir, ignore_errors=True)


async def download_processed_link(file: File, link: str) -> AsyncGenerator[Progress, None]:
    ffmpeg_path: Path = get_ffmpeg_path()
    temp_dir: Path = TRANSFER_PATH / file.username / "yt-dlp" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)

    download_progress: Progress = Progress(file.name, "Downloading Video", 0)
    audio_progress: Progress = Progress(file.name, "Downloading Audio", 0)
    merge_progress: Progress = Progress(file.name, "Merging Audio and Video", 0)
    current_progress: dict[str, Progress] = {"value": download_progress}

    def update_progress_name(name: str) -> None:
        if not name:
            return

        file.name = name
        download_progress.name = name
        audio_progress.name = name
        merge_progress.name = name
        current_progress["value"].name = name

    def get_download_name(data: dict) -> str | None:
        info: dict = data.get("info_dict") or {}

        raw_filename: str = (data.get("tmpfilename") or data.get("filename") or "")

        if not raw_filename:
            return None

        filename = Path(raw_filename).name

        if filename.endswith(".part"):
            filename = filename[:-len(".part")]

        temp_path = Path(filename)
        extension = temp_path.suffix
        title: str | None = info.get("title")
        vcodec: str | None = info.get("vcodec")
        acodec: str | None = info.get("acodec")
        separate_stream = (vcodec not in (None, "none") and acodec == "none") or (acodec not in (None, "none") and vcodec == "none")

        if separate_stream:
            extension = ".mkv"

        if title:
            return f"{title}{extension}" if extension else title

        stem = temp_path.stem
        format_id = str(info.get("format_id") or "")

        if format_id:
            for marker in (f".f{format_id}", f".{format_id}"):
                if stem.endswith(marker):
                    stem = stem[:-len(marker)]
                    break
        else:
            possible_id = stem.rsplit(".f", 1)

            if (len(possible_id) == 2 and possible_id[1].isdigit()):
                stem = possible_id[0]

        return f"{stem}{extension}" if extension else stem

    def hook(data: dict) -> None:
        info: dict = data.get("info_dict") or {}
        resolved_name = get_download_name(data)

        if resolved_name and resolved_name != file.name:
            update_progress_name(resolved_name)

        filename: str = data.get("filename", "")

        if filename.endswith((".vtt", ".srt", ".ass", ".ttml", ".srv1", ".srv2", ".srv3")):
            return

        vcodec: str | None = info.get("vcodec")
        acodec: str | None = info.get("acodec")

        if vcodec not in (None, "none") and acodec == "none":
            current_progress["value"] = download_progress
            download_progress.message = "Downloading Video"

        elif acodec not in (None, "none") and vcodec == "none":
            current_progress["value"] = audio_progress
            audio_progress.message = "Downloading Audio"

        progress: Progress = current_progress["value"]

        if data["status"] == "downloading":
            progress.total = (data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
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
            "noprogress": True,
            "noplaylist": True,
            "format": "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best",
            "outtmpl": str(temp_dir / "%(title)s.%(ext)s"),
            "merge_output_format": "mkv",
            "ffmpeg_location": str(ffmpeg_path.parent),
            "socket_timeout": 60,
            "retries": 10,
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
        update_progress_name(output.name)

        file.size = output.stat().st_size
        file.type = guess_type(file.name)[0] or "application/octet-stream"

        yield current_progress["value"]
        progress: Progress

        async for progress in core.transfer.file_upload(file, output, intermediate=True):
            yield progress

    finally:
        rmtree(temp_dir, ignore_errors=True)
