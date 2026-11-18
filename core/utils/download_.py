"""Backward-compatible import path for link download helpers."""

from __future__ import annotations

from asyncio import Task, create_task, sleep, to_thread
from datetime import datetime, timezone
from email.message import Message
from mimetypes import guess_type
from pathlib import Path
from shutil import rmtree
from traceback import format_exc
from typing import Any, AsyncGenerator, cast
from urllib.parse import parse_qs, unquote, urlparse, ParseResult
from uuid import uuid4

from requests import Response, get

from backend.database.models import File
from core.config import TRANSFER_PATH
from core.data_center import DataCenter
from core.utils import sanitize_sensitive_urls, write_log
from core.utils.binaries import get_ffmpeg_path
from core.utils.download_utils import (
    download_torrent,
    ProcessedDownloadProgress,
    download_youtube_file,
    update_progress_name,
    safe_filename,
    download_google_drive_file,
    stream_response
)
from core.utils.env import GOOGLE_API_KEY
from core.utils.progress import Progress, upload_growing_file


async def download_processed_link(file: File, link: str, selected_torrent_files: set[int] | None = None, ) -> AsyncGenerator[Progress, None]:
    """Download and upload a YouTube media URL or a torrent magnet URI."""

    from core.transfer import file_upload

    parsed: ParseResult = urlparse(link)

    if parsed.scheme.lower() == "magnet":
        exact_topics: list[str | bytes] = parse_qs(parsed.query).get("xt", [])
        has_info_hash: bool = any(topic.lower().startswith(("urn:btih:", "urn:btmh:")) for topic in exact_topics)

        if not link.lower().startswith("magnet:?") or not has_info_hash:
            raise ValueError("Expected a valid torrent magnet URI with an xt info-hash topic")

        async for progress_value in download_torrent(file, link, selected_torrent_files):
            yield progress_value

        return

    ffmpeg_path: Path = get_ffmpeg_path()
    temp_dir: Path = TRANSFER_PATH / file.username / "yt-dlp" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    download_progress: Progress = Progress(file.name, "Downloading Video", 0)
    audio_progress: Progress = Progress(file.name, "Downloading Audio", 0)
    merge_progress: Progress = Progress(file.name, "Merging Audio and Video", 0)
    progress_state: ProcessedDownloadProgress = ProcessedDownloadProgress(download_progress, audio_progress, merge_progress, download_progress)

    try:
        download_task: Task[None] = create_task(to_thread(download_youtube_file, link, temp_dir, ffmpeg_path, file, progress_state))

        while not download_task.done():
            yield progress_state.current
            await sleep(0.1)

        await download_task
        files: list[Path] = [path for path in temp_dir.iterdir() if path.is_file()]

        if not files:
            raise OSError("YouTube download produced no file")

        output: Path = max(files, key=lambda path: path.stat().st_size)
        update_progress_name(file, output.name, progress_state)
        file.size = output.stat().st_size
        file.type = guess_type(file.name)[0] or "application/octet-stream"
        file.modified_at = datetime.now(timezone.utc)
        yield progress_state.current

        async for progress_value in file_upload(file, output, intermediate=True):
            yield progress_value

    finally:
        rmtree(temp_dir, ignore_errors=True)


async def download_unprocessed_link(file: File, link: str) -> AsyncGenerator[Progress, None]:
    """Download a Google Drive file or direct HTTP(S) file while uploading completed chunks."""

    data_center: type[DataCenter] | None = DataCenter(file.data_center)

    if data_center is None:
        raise ValueError(f"Unsupported data center: {file.data_center}")

    parsed = urlparse(link)

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Expected a valid HTTP(S) download URL")

    is_google_drive: bool = parsed.hostname.lower().removeprefix("www.") == "drive.google.com"
    temp_dir: Path = TRANSFER_PATH / file.username / "link-download" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    download_task: Task[Any] | None = None
    response: Response | None = None
    temp_dir / "pending-download"
    response_type: str | None = None

    try:
        if is_google_drive:
            file_id: str | None = None

            if "/file/d/" in parsed.path:
                file_id = parsed.path.split("/file/d/", 1)[1].split("/", 1)[0]

            if not file_id:
                file_id = parse_qs(parsed.query).get("id", [None])[0]

            if not file_id:
                raise ValueError("Could not determine the Google Drive file ID from the URL")

            metadata_response: Response = await to_thread(
                get,
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
            total_size: int = int(metadata["size"])
            output = temp_dir / file.name
            output.touch()
            download_task = create_task(to_thread(download_google_drive_file, link, output))

        else:
            response = await to_thread(
                get,
                link,
                stream=True,
                allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"},
                timeout=(30, 60),
            )

            if not response.ok:
                remote_host: str = urlparse(response.url).hostname or parsed.hostname or "remote host"
                status: int = response.status_code
                reason: str = response.reason or "Request rejected"

                if status == 403:
                    raise OSError(
                        f"Remote download host {remote_host} returned HTTP 403 Forbidden. "
                        "The host may require a valid download session or may reject requests "
                        "from this server's network."
                    )

                raise OSError(f"Remote download host {remote_host} returned HTTP {status} {reason}.")

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
            download_response: Response = cast(Response, response)
            download_task = create_task(to_thread(stream_response, download_response, output, link))

        progress: Progress = Progress(file.name, "Downloading and uploading file", total_size)

        async for progress_value in upload_growing_file(file, output, data_center, progress, download_task, total_size):
            yield progress_value

        file.size = output.stat().st_size
        file.type = guess_type(file.name)[0] or response_type or "application/octet-stream"
        file.modified_at = datetime.now(timezone.utc)

    except Exception as error:
        write_log("ERROR", data_center, "UPLOAD", file.username, f"Remote download failed: {sanitize_sensitive_urls(f"{error}\n{format_exc()}")}")
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
