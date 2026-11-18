"""Download URL, media, and torrent links into StoreLimitless storage."""

from __future__ import annotations

from asyncio import CancelledError, Queue, Task, create_task, gather, sleep, to_thread
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from html import unescape
import inspect
import math
import os
import re
from mimetypes import guess_type
from pathlib import Path, PurePosixPath
from shutil import rmtree
from re import fullmatch
from time import monotonic, sleep as thread_sleep
from traceback import format_exc
from typing import Any, AsyncGenerator, Callable, cast
from urllib.parse import urlparse
from uuid import uuid4

import gdown
import libtorrent
from requests import Response, get
from requests.exceptions import RequestException
from yt_dlp import YoutubeDL

from backend.database.models import Directory, File
from core.config import TRANSFER_PATH
from core.data_center import DataCenter
from core.utils import sanitize_sensitive_urls, write_log
from core.utils.progress import Progress
from core.utils.upload_manager import (
    IngestionUploadTicket,
    cancel_ingestion_upload,
    enqueue_ingestion_upload,
    ingestion_upload_slot,
)


@dataclass
class ProcessedDownloadProgress:
    """Track the currently active YouTube download stage."""

    video: Progress
    audio: Progress
    merge: Progress
    current: Progress


def safe_filename(name: str) -> str:
    """Reduce a remote filename to a plain filename component."""

    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name if name not in {"", ".", ".."} else "download"


def download_google_drive_file(link: str, output: Path) -> None:
    """Stream a Google Drive file directly into ``output`` for concurrent upload."""

    # Passing a path to gdown makes it write to a temporary .part file and rename
    # it only after the download completes. The upload worker watches ``output``
    # for growing chunks, so that behavior silently turns streaming into a
    # download-then-upload operation. A file-like destination disables gdown's
    # staging rename. Unbuffered writes make each received chunk visible to the
    # concurrent reader immediately.
    try:
        with output.open("wb", buffering=0) as destination:
            downloaded = gdown.download(
                url=link,
                output=destination,
                quiet=True,
                use_cookies=False,
            )
            destination.flush()
            if downloaded is None:
                raise OSError("Google Drive download failed")
    except Exception:
        output.unlink(missing_ok=True)
        raise

    if not output.is_file():
        raise OSError("Google Drive download failed")

MAX_STREAM_RETRIES: int = 50
STREAM_RETRY_MAX_DELAY: float = 5.0


class IncompleteDownloadResponseError(RuntimeError):
    """Raised when a server closes a response before its advertised byte count arrives."""


class DownloadResumeNotSupportedError(RuntimeError):
    """Raised when a remote server cannot resume a partially downloaded file."""


def _content_range(response: Response) -> tuple[int, int, int | None] | None:
    """Parse a standard Content-Range header into start, end, and optional total size."""

    value: str = response.headers.get("Content-Range", "").strip()
    match = fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", value, flags=2)

    if match is None:
        return None

    start: int = int(match.group(1))
    end: int = int(match.group(2))
    total: int | None = None if match.group(3) == "*" else int(match.group(3))
    return start, end, total


def stream_response(response: Response, output: Path, source_url: str | None = None) -> None:
    """Stream an HTTP response to disk, resuming interrupted transfers with byte ranges.

    The output is appended only after the source confirms the requested Range offset.
    This matters because completed chunks may already be uploading to the storage provider.
    """

    download_url: str = source_url or response.url
    request_headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "identity",
    }
    current_response: Response | None = response
    total_size: int = 0
    retry_count: int = 0

    try:
        try:
            total_size = max(0, int(response.headers.get("Content-Length", "0")))
        except (TypeError, ValueError):
            total_size = 0

        while True:
            offset: int = output.stat().st_size if output.exists() else 0

            if total_size > 0 and offset >= total_size:
                return

            segment_start: int = offset
            segment_end: int | None = None
            segment_size: int = 0

            try:
                if current_response is None:
                    headers: dict[str, str] = dict(request_headers)

                    if offset:
                        headers["Range"] = f"bytes={offset}-"

                    current_response = get(
                        download_url,
                        stream=True,
                        allow_redirects=True,
                        headers=headers,
                        timeout=(30, 60),
                    )

                    if offset:
                        status: int = current_response.status_code

                        if status in {408, 429} or status >= 500:
                            raise RequestException(f"Remote server returned HTTP {status} while resuming the download")

                        if status == 416:
                            unsatisfied: str = current_response.headers.get("Content-Range", "")
                            match = fullmatch(r"bytes\s+\*/(\d+)", unsatisfied.strip(), flags=2)

                            if match is not None and offset >= int(match.group(1)):
                                total_size = int(match.group(1))
                                current_response.close()
                                current_response = None
                                return

                            raise DownloadResumeNotSupportedError(
                                f"Remote server rejected the resume request at byte {offset} (HTTP 416)"
                            )

                        if status == 200:
                            raise DownloadResumeNotSupportedError(
                                "The remote server interrupted the download and ignored the HTTP Range request. "
                                "It must support byte-range requests to resume this partial download safely."
                            )

                        if status != 206:
                            raise DownloadResumeNotSupportedError(
                                f"The remote server could not resume the download (HTTP {status})"
                            )

                        content_range = _content_range(current_response)

                        if content_range is None or content_range[0] != offset:
                            raise DownloadResumeNotSupportedError(
                                "The remote server returned an invalid Content-Range for the resume request"
                            )

                        segment_start, segment_end, remote_total = content_range

                        if remote_total is not None:
                            if total_size > 0 and total_size != remote_total:
                                raise DownloadResumeNotSupportedError(
                                    "The remote file size changed while the download was being resumed"
                                )

                            total_size = remote_total

                    else:
                        if current_response.status_code in {408, 429} or current_response.status_code >= 500:
                            raise RequestException(
                                f"Remote server returned HTTP {current_response.status_code} while downloading"
                            )

                        if not current_response.ok:
                            raise OSError(
                                f"Remote server returned HTTP {current_response.status_code} "
                                f"{current_response.reason or 'Request rejected'}"
                            )

                elif current_response.status_code == 206:
                    content_range = _content_range(current_response)

                    if content_range is not None:
                        segment_start, segment_end, remote_total = content_range

                        if segment_start != offset:
                            raise DownloadResumeNotSupportedError(
                                "The remote server returned a byte range that does not match the downloaded file"
                            )

                        if remote_total is not None:
                            total_size = remote_total

                try:
                    segment_size = max(0, int(current_response.headers.get("Content-Length", "0")))
                except (TypeError, ValueError):
                    segment_size = 0

                if segment_end is not None:
                    range_size: int = segment_end - segment_start + 1

                    if segment_size > 0 and segment_size != range_size:
                        raise IncompleteDownloadResponseError(
                            "Remote Content-Length did not match the requested byte range"
                        )

                    segment_size = range_size

                bytes_received: int = 0
                write_mode: str = "ab" if offset else "wb"

                with output.open(write_mode, buffering=0) as destination:
                    for chunk in current_response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            destination.write(chunk)
                            bytes_received += len(chunk)

                current_response.close()
                current_response = None

                new_offset: int = output.stat().st_size if output.exists() else 0

                if segment_size > 0 and bytes_received < segment_size:
                    raise IncompleteDownloadResponseError(
                        f"Remote response ended after {bytes_received} of {segment_size} expected bytes"
                    )

                if total_size > 0 and new_offset < total_size:
                    # A server may intentionally return a bounded 206 range. A fully
                    # received segment is valid progress, so request the next byte range
                    # immediately instead of counting it as a failed attempt.
                    if segment_end is not None and bytes_received == segment_size and new_offset == segment_end + 1:
                        retry_count = 0
                        continue

                    raise IncompleteDownloadResponseError(
                        f"Remote response ended at byte {new_offset} of {total_size}"
                    )

                return

            except (RequestException, IncompleteDownloadResponseError) as error:
                if current_response is not None:
                    current_response.close()
                    current_response = None

                retry_count += 1

                if retry_count > MAX_STREAM_RETRIES:
                    current_size: int = output.stat().st_size if output.exists() else 0
                    raise RuntimeError(
                        f"Remote download failed after {MAX_STREAM_RETRIES} resume attempts at byte {current_size}. "
                        f"Last error: {error}"
                    ) from error

                # Use a short capped delay. Repeated mid-stream connection cuts should
                # recover quickly rather than accumulating long exponential backoffs.
                thread_sleep(min(STREAM_RETRY_MAX_DELAY, 0.5 * retry_count))

    finally:
        if current_response is not None:
            current_response.close()


def update_progress_name(file: File, name: str, progress_state: ProcessedDownloadProgress) -> None:
    """Keep the database file and all download progress stages consistently named."""

    if not name:
        return

    file.name = name
    progress_state.video.name = name
    progress_state.audio.name = name
    progress_state.merge.name = name
    progress_state.current.name = name


def get_download_name(data: dict[str, Any]) -> str | None:
    """Derive a stable output filename from yt-dlp's hook data."""

    info: dict[str, Any] = data.get("info_dict") or {}
    raw_filename: str = str(data.get("tmpfilename") or data.get("filename") or "")

    if not raw_filename:
        return None

    filename: str = Path(raw_filename).name

    if filename.endswith(".part"):
        filename = filename[:-len(".part")]

    temp_path: Path = Path(filename)
    extension: str = temp_path.suffix
    title: str | None = info.get("title")
    vcodec: str | None = info.get("vcodec")
    acodec: str | None = info.get("acodec")
    separate_stream: bool = (vcodec not in (None, "none") and acodec == "none") or (acodec not in (None, "none") and vcodec == "none")

    if separate_stream:
        extension = ".mkv"

    if title:
        return f"{title}{extension}" if extension else title

    stem: str = temp_path.stem
    format_id: str = str(info.get("format_id") or "")

    if format_id:
        for marker in (f".f{format_id}", f".{format_id}"):
            if stem.endswith(marker):
                stem = stem[:-len(marker)]
                break

    else:
        possible_id: list[str] = stem.rsplit(".f", 1)

        if len(possible_id) == 2 and possible_id[1].isdigit():
            stem = possible_id[0]

    return f"{stem}{extension}" if extension else stem


def youtube_progress_hook(data: dict[str, Any], *, file: File, progress_state: ProcessedDownloadProgress) -> None:
    """Update video/audio progress from yt-dlp's download hook."""

    info: dict[str, Any] = data.get("info_dict") or {}
    resolved_name: str | None = get_download_name(data)

    if resolved_name is not None and resolved_name != file.name:
        update_progress_name(file, cast(str, resolved_name), progress_state)

    filename: str = str(data.get("filename") or "")

    if filename.endswith((".vtt", ".srt", ".ass", ".ttml", ".srv1", ".srv2", ".srv3")):
        return

    vcodec: str | None = info.get("vcodec")
    acodec: str | None = info.get("acodec")

    if vcodec not in (None, "none") and acodec == "none":
        progress_state.current = progress_state.video
        progress_state.video.message = "Downloading Video"

    elif acodec not in (None, "none") and vcodec == "none":
        progress_state.current = progress_state.audio
        progress_state.audio.message = "Downloading Audio"

    progress: Progress = progress_state.current

    if data.get("status") == "downloading":
        progress.total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        progress.transfer = data.get("downloaded_bytes", 0)

    elif data.get("status") == "finished":
        progress.transfer = progress.total


def youtube_postprocessor_hook(data: dict[str, Any], *, progress_state: ProcessedDownloadProgress) -> None:
    """Update progress while yt-dlp merges audio and video streams."""

    if data.get("status") == "started":
        progress_state.current = progress_state.merge
        progress_state.merge.message = "Merging Audio and Video"

    elif data.get("status") == "finished":
        progress_state.merge.transfer = progress_state.merge.total


def download_youtube_file(link: str, temp_dir: Path, ffmpeg_path: Path, file: File, progress_state: ProcessedDownloadProgress) -> None:
    """Run yt-dlp synchronously; the caller runs this helper in a worker thread."""

    options: dict[str, Any] = {
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
        "progress_hooks": [partial(youtube_progress_hook, file=file, progress_state=progress_state)],
        "postprocessor_hooks": [partial(youtube_postprocessor_hook, progress_state=progress_state)],
    }

    with YoutubeDL(options) as ydl:
        ydl.download([link])


def safe_torrent_component(component: str) -> str:
    """Convert an untrusted torrent path component into a portable name."""

    cleaned: str = "".join(
        "_" if character in '<>:"|?*\\/' or ord(character) < 32 else character
        for character in str(component)
    ).strip().rstrip(" .")

    if cleaned in {"", ".", ".."}:
        raise ValueError(f"Torrent contains an invalid path component: {component!r}")

    return cleaned


def torrent_relative_parts(raw_path: str) -> tuple[str, ...]:
    """Validate a torrent-relative path before using it on disk or in the virtual tree."""

    path: PurePosixPath = PurePosixPath(raw_path)
    parts: tuple[str, ...] = tuple(part for part in path.parts if part not in {"", "."})

    if path.is_absolute() or not parts or any(part == ".." or "\\" in part or "\x00" in part for part in parts):
        raise ValueError(f"Torrent contains an unsafe file path: {raw_path!r}")

    return parts


def raise_torrent_error(status: Any) -> None:
    """Raise only for a non-zero libtorrent error code.

    Some libtorrent bindings expose ``errc`` as an error_code object whose
    truthiness does not reliably indicate failure. In particular, a successful
    code can stringify to ``Success`` while still evaluating as truthy.
    """

    error_code: Any | None = getattr(status, "errc", None)

    if error_code is None:
        return

    code_value: Any | None = getattr(error_code, "value", None)

    if callable(code_value):
        try:
            code_value = code_value()

        except Exception:
            code_value = None

    if code_value is not None:
        try:
            if int(code_value) == 0:
                return

        except (TypeError, ValueError):
            pass

    try:
        detail: str = str(error_code.message()).strip()

    except Exception:
        detail = str(error_code).strip()

    if not detail or detail.casefold() == "success":
        return

    raise OSError(f"Torrent engine error: {detail}")


async def wait_for_torrent_metadata(handle: Any, progress: Progress, fallback_name: str) -> AsyncGenerator[Progress, None]:
    """Wait for magnet metadata and report peer discovery while waiting."""

    while True:
        status: Any = handle.status()
        raise_torrent_error(status)

        if bool(getattr(status, "has_metadata", False)):
            return

        peers: int = int(getattr(status, "num_peers", 0) or 0)
        progress.name = str(getattr(status, "name", "") or fallback_name)
        progress.message = "Searching for peers" if peers == 0 else "Downloading torrent metadata"
        progress.transfer = 0
        progress.total = 0
        yield progress
        await sleep(0.5)


async def wait_for_torrent_download(
        handle: Any,
        progress: Progress,
        torrent_name: str,
        expected_total: int,
        processed_download: dict[str, bool],
        torrent_files: Any | None = None,
        selected_indices: set[int] | None = None,
) -> AsyncGenerator[Progress, None]:
    """Report torrent download stages until libtorrent confirms completion."""

    while True:
        status: Any = handle.status()
        raise_torrent_error(status)

        if not bool(getattr(status, "has_metadata", False)):
            progress.message = "Downloading torrent metadata"
            progress.transfer = 0
            progress.total = expected_total

        else:
            current_total: int = int(getattr(status, "total_wanted", 0) or expected_total)
            current_done: int = int(getattr(status, "total_wanted_done", 0) or 0)
            progress.total = current_total
            progress.transfer = min(current_done, current_total) if current_total else 0
            progress.name = str(getattr(status, "name", "") or torrent_name)

            if torrent_files is not None:
                try:
                    file_progress: list[int] = [int(value) for value in handle.file_progress()]
                    padding_check: Callable = cast(Callable, getattr(torrent_files, "pad_file_at", None))
                    item_progress: list[dict[str, Any]] = []

                    for file_index in range(int(torrent_files.num_files())):
                        if (
                                (selected_indices is not None and file_index not in selected_indices) or
                                callable(padding_check) and padding_check(file_index)
                        ):
                            continue

                        raw_path: str = str(torrent_files.file_path(file_index))
                        path_parts: tuple[str, ...] = torrent_relative_parts(raw_path)
                        display_path: str = "/".join(path_parts[1:] if len(path_parts) > 1 else path_parts)
                        file_size: int = int(torrent_files.file_size(file_index))
                        downloaded: int = min(file_size, max(0, file_progress[file_index])) if file_index < len(file_progress) else 0
                        item_progress.append({
                            "file_index": file_index,
                            "name": display_path or Path(raw_path).name,
                            "transfer": downloaded,
                            "total": file_size,
                            "status": "downloaded" if file_size == 0 or downloaded >= file_size else "downloading" if downloaded else "queued",
                        })

                    progress.torrent_items = item_progress

                except (AttributeError, RuntimeError, TypeError, ValueError, IndexError):
                    pass

            peers: int = int(getattr(status, "num_peers", 0) or 0)
            seeds: int = int(getattr(status, "num_seeds", 0) or 0)
            is_seeding: bool = bool(getattr(status, "is_seeding", False))
            is_finished: bool = bool(getattr(status, "is_finished", False))

            if is_seeding or is_finished:
                processed_download["value"] = True
                progress.transfer = progress.total
                progress.message = (
                    "Torrent seeding state reached; stopping peers and preparing upload"
                    if is_seeding else "Torrent download complete; preparing upload"
                )
                yield progress
                return

            if peers == 0:
                progress.message = "Searching for peers"

            else:
                progress.message = f"Downloading from peers ({peers} connected, {seeds} seeds)"

        yield progress
        await sleep(0.5)


async def ensure_torrent_directory(username: str, base_directory: Directory, relative_directory_parts: tuple[str, ...]) -> Directory:
    """Create or restore each missing directory beneath the selected destination."""

    current: Directory = base_directory

    for component in relative_directory_parts:
        next_path: Path = current.path / safe_torrent_component(component)
        child: Directory | None = await Directory.get(username, path=next_path)

        if child is None:
            child = await Directory.get(username, path=next_path, trash=True)

            if child is not None:
                await child.restore()

            else:
                child = Directory(path=next_path, username=username)
                await child.save()

        current = cast(Directory, child)

    return current


async def preview_torrent_files(link: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Read magnet metadata without downloading the torrent payload."""
    session: Any | None = None
    handle: Any | None = None
    preview_dir: Path = TRANSFER_PATH / "torrent-preview" / uuid4().hex
    preview_dir.mkdir(parents=True, exist_ok=True)
    deadline: float = monotonic() + timeout_seconds

    try:
        session = libtorrent.session({
            "listen_interfaces": "0.0.0.0:0",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
        })
        handle = session.add_torrent({"url": link, "save_path": str(preview_dir)})

        while True:
            status: Any = handle.status()
            raise_torrent_error(status)

            if bool(getattr(status, "has_metadata", False)):
                info: Any = handle.torrent_file()
                handle.prioritize_files([0] * int(info.files().num_files()))
                break

            if monotonic() >= deadline:
                raise TimeoutError("Timed out while retrieving torrent metadata")

            await sleep(0.5)

        info: Any = handle.torrent_file()
        torrent_files: Any = info.files()
        torrent_name: str = safe_torrent_component(str(info.name()))
        padding_check: Callable = cast(Callable, getattr(torrent_files, "pad_file_at", None))
        entries: list[dict[str, Any]] = []

        for index in range(int(torrent_files.num_files())):
            if callable(padding_check) and padding_check(index):
                continue

            raw_path = str(torrent_files.file_path(index))
            parts = torrent_relative_parts(raw_path)
            entries.append({"index": index, "path": "/".join(parts), "size": int(torrent_files.file_size(index))})

        return {"name": torrent_name, "files": entries}

    finally:
        if session is not None and handle is not None:
            try:
                handle.pause()
                session.remove_torrent(handle)

            except Exception:
                pass

        rmtree(preview_dir, ignore_errors=True)


def get_downloadable_torrent_files(
        torrent_files: Any,
        temp_dir: Path,
        torrent_name_raw: str,
        torrent_name: str,
        selected_indices: set[int] | None = None,
) -> list[tuple[int, Path, tuple[str, ...]]]:
    """Resolve completed non-padding torrent files, preserving their source indexes and directory layout."""

    root_on_disk: Path = temp_dir / torrent_name_raw
    has_torrent_root_folder: bool = root_on_disk.is_dir()
    download_root: Path = temp_dir.resolve()
    downloadable_files: list[tuple[int, Path, tuple[str, ...]]] = []
    file_count: int = int(torrent_files.num_files())
    padding_check: Callable = cast(Callable, getattr(torrent_files, "pad_file_at", None))

    for index in range(file_count):
        if (selected_indices is not None and index not in selected_indices) or callable(padding_check) and padding_check(index):
            continue

        parts: tuple[str, ...] = torrent_relative_parts(str(torrent_files.file_path(index)))
        direct_path: Path = temp_dir.joinpath(*parts)
        nested_path: Path = root_on_disk.joinpath(*parts) if parts[0] != torrent_name_raw else direct_path
        local_path: Path | None = None
        local_path_includes_root: bool = False

        for candidate in (direct_path, nested_path):
            try:
                resolved_candidate: Path = candidate.resolve()

                if not resolved_candidate.is_relative_to(download_root):
                    raise ValueError(f"Torrent path escapes its download directory: {candidate}")

            except (OSError, RuntimeError) as error:
                raise ValueError(f"Invalid torrent output path: {candidate}") from error

            if candidate.is_file() and not candidate.is_symlink():
                local_path = candidate
                local_path_includes_root = parts[0] == torrent_name_raw
                break

        if local_path is None:
            raise FileNotFoundError(f"Torrent download is missing expected file: {str(PurePosixPath(*parts))}")

        if has_torrent_root_folder:
            inner_parts: tuple[str, ...] = parts[1:] if local_path_includes_root and parts[0] == torrent_name_raw else parts

            if not inner_parts:
                inner_parts = (local_path.name,)

            target_parts: tuple[str, ...] = (torrent_name, *(safe_torrent_component(part) for part in inner_parts),)

        else:
            target_parts = tuple(safe_torrent_component(part) for part in parts)

        downloadable_files.append((index, local_path, target_parts))

    return downloadable_files


async def download_torrent_to_disk(
        link: str,
        temp_dir: Path,
        progress: Progress,
        fallback_name: str,
        result: dict[str, Any],
        selected_indices: set[int] | None = None,
) -> AsyncGenerator[Progress, None]:
    """Download a complete torrent and return its metadata through ``result``."""

    try:
        import libtorrent

    except ImportError as error:
        raise RuntimeError("Torrent downloads require the optional 'python-libtorrent' dependency") from error

    torrent_session: Any | None = None
    handle: Any | None = None
    processed_download: bool = False

    try:
        torrent_session = libtorrent.session({
            "listen_interfaces": "0.0.0.0:0",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
        })
        handle = torrent_session.add_torrent({"url": link, "save_path": str(temp_dir)})

        async for progress_value in wait_for_torrent_metadata(handle, progress, fallback_name):
            yield progress_value

        torrent_info: Any = handle.torrent_file()
        torrent_files: Any = torrent_info.files()
        torrent_name_raw: str = str(torrent_info.name())
        torrent_name_parts: tuple[str, ...] = torrent_relative_parts(torrent_name_raw)

        if len(torrent_name_parts) != 1:
            raise ValueError("Torrent root name must be a single safe path component")

        torrent_name_raw = torrent_name_parts[0]
        torrent_name: str = safe_torrent_component(torrent_name_raw)
        file_count: int = int(torrent_files.num_files())
        expected_total: int = 0
        padding_check: Callable = cast(Callable, getattr(torrent_files, "pad_file_at", None))
        if selected_indices is not None:
            if not selected_indices or any(index < 0 or index >= file_count for index in selected_indices):
                raise ValueError("Torrent file selection is empty or contains invalid file indexes")

            priorities = [4 if index in selected_indices else 0 for index in range(file_count)]
            handle.prioritize_files(priorities)

        for index in range(file_count):
            if (selected_indices is not None and index not in selected_indices) or callable(padding_check) and padding_check(index):
                continue

            expected_total += int(torrent_files.file_size(index))

        progress.name = torrent_name
        progress.total = expected_total
        progress.transfer = 0
        progress.message = "Torrent metadata received; waiting for peers"
        yield progress
        processed_state: dict[str, bool] = {"value": processed_download}

        async for progress_value in wait_for_torrent_download(
                handle, progress, torrent_name, expected_total, processed_state, torrent_files, selected_indices
        ):
            yield progress_value

        processed_download = processed_state["value"]

        if not processed_download:
            raise RuntimeError("Torrent did not reach a completed download state")

        result["torrent_files"] = torrent_files
        result["torrent_name_raw"] = torrent_name_raw
        result["torrent_name"] = torrent_name
        result["expected_total"] = expected_total
        progress.message = "Torrent download complete; preparing upload"
        yield progress

    finally:
        if torrent_session is not None and handle is not None:
            try:
                try:
                    handle.pause()

                finally:
                    torrent_session.remove_torrent(handle)

            except Exception:
                pass


def is_google_drive_folder_url(link: str) -> bool:
    """Return whether a URL identifies a Google Drive folder rather than a single file."""

    parsed = urlparse(str(link or "").strip())
    return (
        parsed.scheme.lower() in {"http", "https"}
        and (parsed.hostname or "").lower().removeprefix("www.") == "drive.google.com"
        and re.search(r"/drive/folders/[-\w]+(?:/view)?/?$", parsed.path or "") is not None
    )


def _public_google_drive_folder_title(link: str) -> str:
    """Resolve a public folder title when its public listing contains no files."""

    parsed = urlparse(link)
    folder_match = re.search(r"/drive/folders/([-\w]+)", parsed.path or "")
    if folder_match is None:
        raise ValueError("Could not determine the Google Drive folder ID from the URL")
    folder_id = folder_match.group(1)
    response: Response = get(
        f"https://drive.google.com/embeddedfolderview?id={folder_id}",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(15, 30),
    )

    try:
        response.raise_for_status()
        title_match = fullmatch(r"(?is).*?<title[^>]*>(.*?)</title>.*", response.text)
        if title_match is None:
            raise OSError("Google Drive did not return a folder title for this public link")
        title = unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        title = re.sub(r"\s+-\s+Google Drive$", "", title, flags=re.IGNORECASE).strip()
        if not title:
            raise OSError("Google Drive returned an empty folder name")
        return title
    finally:
        response.close()


def _download_google_drive_entry(
        file_id: str,
        output_path: Path,
        item: dict[str, Any],
) -> Path:
    """Download one public Google Drive file using gdown into a unique staging path."""

    parameters = inspect.signature(gdown.download).parameters
    output: str = str(output_path) if output_path.suffix else f"{output_path.parent}{os.sep}"

    kwargs: dict[str, Any] = {
        "id": file_id,
        "output": output,
        "quiet": True,
        # The job only accepts public links; do not silently use an unrelated server login.
        "use_cookies": False,
    }
    if "resume" in parameters:
        kwargs["resume"] = True

    if "progress" in parameters:
        def report_download(downloaded: int, total: int | None) -> None:
            item["download_transfer"] = max(0, int(downloaded or 0))
            if total is not None:
                item["download_total"] = max(0, int(total))
                item["total"] = max(int(item.get("total") or 0), int(total))
                item["size"] = max(int(item.get("size") or 0), int(total))
            item["message"] = "Downloading file"

        kwargs["progress"] = report_download

    result = gdown.download(**kwargs)
    if not result:
        raise OSError("Google Drive did not produce a downloadable file")

    actual_path = Path(result) if isinstance(result, (str, os.PathLike)) else output_path
    if not actual_path.is_file():
        if output_path.is_file():
            actual_path = output_path
        else:
            raise OSError("Google Drive download ended without producing a complete file")

    actual_size = actual_path.stat().st_size
    expected_size = max(0, int(item.get("download_total") or 0))
    if expected_size > 0 and actual_size != expected_size:
        raise OSError(f"Google Drive download was incomplete ({actual_size} of {expected_size} bytes)")

    item["download_transfer"] = actual_size
    item["download_total"] = actual_size
    item["total"] = actual_size
    item["size"] = actual_size
    return actual_path


async def download_google_drive_folder(file: File, link: str) -> AsyncGenerator[Progress, None]:
    """Download a public Drive folder concurrently and upload each ready file through the shared FIFO."""

    from core.transfer import file_upload

    data_center: type[DataCenter] | None = DataCenter(file.data_center)
    if data_center is None:
        raise ValueError(f"Unsupported data center: {file.data_center}")

    base_directory: Directory | None = await Directory.get(file.username, did=file.directory_id)
    if base_directory is None or base_directory.id is None:
        raise ValueError("The destination directory no longer exists")

    try:
        max_downloads = max(1, min(16, int(os.environ.get("STORELIMITLESS_GOOGLE_DRIVE_DOWNLOADS", "4"))))
    except ValueError:
        max_downloads = 4

    temp_dir: Path = (TRANSFER_PATH / file.username / "google-drive-folder" / uuid4().hex).resolve()
    temp_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(file.name or "Google Drive folder", "Discovering folder contents", 0)
    progress.ingestion_type = "google_drive_folder"
    progress.download_active = True
    items: list[dict[str, Any]] = []
    tickets: dict[int, IngestionUploadTicket] = {}
    ready_queue: Queue[Any] = Queue(maxsize=max_downloads * 2)
    download_queue: Queue[Any] = Queue()
    download_workers: list[Task[None]] = []
    coordinator: Task[None] | None = None
    uploader: Task[None] | None = None
    root_name = ""

    def refresh_snapshot(message: str | None = None) -> None:
        """Update backend-visible job and per-file progress from worker snapshots."""

        sample_at = monotonic()
        total_bytes = sum(max(0, int(item.get("total") or item.get("size") or 0)) for item in items)
        download_bytes = 0
        upload_bytes = 0
        known_download_total = 0
        known_upload_total = total_bytes

        for item in items:
            size = max(0, int(item.get("total") or item.get("size") or 0))
            status = str(item.get("status") or "queued")

            # gdown versions without a progress callback still write to a staging
            # file before atomically publishing the finished destination. Observe
            # those staged bytes so progress stays backend-driven on every version.
            if status == "downloading":
                staging_value = str(item.get("_staging_dir") or "")
                if staging_value:
                    staging_path = Path(staging_value)
                    try:
                        staged_bytes = sum(
                            candidate.stat().st_size
                            for candidate in staging_path.rglob("*")
                            if candidate.is_file()
                        )
                    except OSError:
                        staged_bytes = 0
                    item["download_transfer"] = max(
                        int(item.get("download_transfer") or 0),
                        staged_bytes,
                    )

            downloaded = max(0, int(item.get("download_transfer") or 0))
            uploaded = max(0, int(item.get("upload_transfer") or 0))
            if status in {"waiting_upload", "uploading", "completed"}:
                downloaded = max(downloaded, size)
            if status == "completed":
                uploaded = size
            item["download_transfer"] = downloaded
            item["upload_transfer"] = uploaded

            for phase, counter, phase_total in (
                ("download", downloaded, max(0, int(item.get("download_total") or 0))),
                ("upload", uploaded, max(0, int(item.get("upload_total") or 0))),
            ):
                previous_at = float(item.get(f"_{phase}_sample_at") or sample_at)
                elapsed = max(0.0, sample_at - previous_at)
                previous_bytes = max(0, int(item.get(f"_{phase}_sample_bytes") or 0))
                previous_speed = max(0.0, float(item.get(f"{phase}_speed") or 0))
                last_moved_at = float(item.get(f"_{phase}_last_moved_at") or sample_at)

                if counter < previous_bytes:
                    speed = 0.0
                    last_moved_at = sample_at
                elif elapsed > 0 and counter > previous_bytes:
                    measured_speed = (counter - previous_bytes) / elapsed
                    speed = measured_speed if previous_speed <= 0 else previous_speed * 0.7 + measured_speed * 0.3
                    last_moved_at = sample_at
                else:
                    speed = 0.0 if sample_at - last_moved_at >= 4 else previous_speed

                item[f"{phase}_speed"] = speed
                item[f"{phase}_eta"] = (
                    max(0.0, (phase_total - counter) / speed)
                    if phase_total > counter and speed > 0 else None
                )
                item[f"_{phase}_sample_at"] = sample_at
                item[f"_{phase}_sample_bytes"] = counter
                item[f"_{phase}_last_moved_at"] = last_moved_at

            download_bytes += min(size, downloaded) if size > 0 else downloaded
            upload_bytes += min(size, uploaded) if size > 0 else uploaded
            known_download_total += max(0, int(item.get("download_total") or 0))

        progress.name = root_name or progress.name
        progress.ingestion_type = "google_drive_folder"
        progress.download_active = True
        progress.download_transfer = download_bytes
        progress.download_total = known_download_total
        progress.transfer = upload_bytes
        progress.total = known_upload_total
        progress.torrent_items = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in items
        ]
        progress.file_counts = {
            "discovered": len(items),
            "pending": sum(item.get("status") in {"queued", "pending", "available"} for item in items),
            "downloading": sum(item.get("status") == "downloading" for item in items),
            "downloaded": sum(item.get("status") in {"downloaded", "waiting_upload", "uploading", "completed"} for item in items),
            "waiting_upload": sum(item.get("status") == "waiting_upload" for item in items),
            "uploading": sum(item.get("status") == "uploading" for item in items),
            "completed": sum(item.get("status") == "completed" for item in items),
            "failed": sum(item.get("status") == "error" for item in items),
            "cancelled": sum(item.get("status") in {"cancelled", "skipped"} for item in items),
        }
        if message:
            progress.message = message

    async def download_worker() -> None:
        """Download entries in parallel, then register each completed file in FIFO order."""

        while True:
            entry = await download_queue.get()
            if entry is None:
                download_queue.task_done()
                return

            index, file_id, relative_parts, target_directory, item = entry
            try:
                staging_dir = temp_dir / "payload" / f"{index:06d}"
                staging_dir.mkdir(parents=True, exist_ok=True)
                proposed_path = staging_dir / relative_parts[-1]
                item["_staging_dir"] = str(staging_dir)
                item["status"] = "downloading"
                item["message"] = "Downloading file"
                actual_path = await to_thread(_download_google_drive_entry, file_id, proposed_path, item)
                actual_size = actual_path.stat().st_size
                item.update({
                    "name": "/".join((root_name, *relative_parts[:-1], actual_path.name)),
                    "path": "/".join((root_name, *relative_parts[:-1], actual_path.name)),
                    "size": actual_size,
                    "total": actual_size,
                    "download_transfer": actual_size,
                    "download_total": actual_size,
                    "upload_transfer": 0,
                    "upload_total": actual_size,
                    "status": "waiting_upload",
                    "message": "Downloaded · waiting for upload",
                })
                ticket = await enqueue_ingestion_upload(str(item.get("path") or item.get("name") or f"folder-file-{index}"), file.username)
                tickets[index] = ticket
                await ready_queue.put((index, actual_path, target_directory, item, ticket))
            except CancelledError:
                raise
            except Exception as error:
                item.update({
                    "status": "error",
                    "message": f"Download failed: {sanitize_sensitive_urls(str(error))[:180]}",
                })
                write_log(
                    "ERROR", data_center, "GOOGLE DRIVE", file.username,
                    f"Failed to download folder entry {item.get('path')}: {sanitize_sensitive_urls(str(error))}",
                )
            finally:
                download_queue.task_done()

    async def upload_worker() -> None:
        """Store ready files one at a time; download workers continue independently."""

        while True:
            work = await ready_queue.get()
            if work is None:
                ready_queue.task_done()
                return

            index, local_path, target_directory, item, ticket = work
            try:
                name = local_path.name
                remote_file = File(
                    directory_id=cast(int, target_directory.id),
                    name=name,
                    type=guess_type(name)[0] or "application/octet-stream",
                    size=max(0, local_path.stat().st_size),
                    modified_at=datetime.now(timezone.utc),
                    links=[],
                    data_center=file.data_center,
                    username=file.username,
                )
                local_size = remote_file.size
                async with ingestion_upload_slot(ticket):
                    item.update({
                        "status": "uploading",
                        "message": "Uploading file",
                        "transfer": 0,
                        "upload_transfer": 0,
                        "upload_total": local_size,
                        "total": local_size,
                        "size": local_size,
                        "download_transfer": local_size,
                        "download_total": local_size,
                    })
                    async with aclosing(file_upload(remote_file, local_path, intermediate=True, queue_slot_held=True)) as upload_stream:
                        async for upload_progress in upload_stream:
                            transferred = min(local_size, max(0, int(upload_progress.transfer or 0)))
                            item.update({
                                "transfer": transferred,
                                "upload_transfer": transferred,
                                "upload_total": local_size,
                                "status": "uploading",
                                "message": f"Uploading · {transferred * 100 / local_size:.1f}%" if local_size else "Uploading file",
                            })
                            await sleep(0)

                    await remote_file.save()
                    # Match the database conflict-resolution name if a duplicate existed.
                    final_name = remote_file.name
                    if final_name != name:
                        parent_parts = tuple(str(item.get("path") or "").split("/")[1:-1])
                        item["name"] = "/".join((root_name, *parent_parts, final_name))
                        item["path"] = item["name"]
                    local_path.unlink(missing_ok=True)
                    item.update({
                        "transfer": local_size,
                        "total": local_size,
                        "size": local_size,
                        "download_transfer": local_size,
                        "download_total": local_size,
                        "upload_transfer": local_size,
                        "upload_total": local_size,
                        "status": "completed",
                        "message": "Completed",
                    })
            except CancelledError:
                raise
            except Exception as error:
                item.update({
                    "status": "error",
                    "message": f"Upload failed: {sanitize_sensitive_urls(str(error))[:180]}",
                })
                write_log(
                    "ERROR", data_center, "GOOGLE DRIVE", file.username,
                    f"Failed to upload folder entry {item.get('path')}: {sanitize_sensitive_urls(str(error))}",
                )
            finally:
                try:
                    await cancel_ingestion_upload(ticket)
                finally:
                    ready_queue.task_done()

    try:
        try:
            entries = await to_thread(
                gdown.download_folder,
                url=link,
                output=f"{temp_dir}{os.sep}",
                quiet=True,
                skip_download=True,
                use_cookies=False,
            )
        except TypeError as error:
            raise RuntimeError("Google Drive folder ingestion requires gdown>=6.0.0") from error

        if not isinstance(entries, list):
            raise OSError("Google Drive did not return a folder file listing")

        # gdown entry paths are relative to the submitted folder and generally omit
        # the root name. Resolve the root from the public folder listing instead of
        # accidentally treating the first file's parent directory as the root.
        root_name = safe_torrent_component(await to_thread(_public_google_drive_folder_title, link))
        progress.name = root_name
        progress.message = "Creating directories"
        base_directory = cast(Directory, await Directory.get(file.username, did=file.directory_id))
        await ensure_torrent_directory(file.username, base_directory, (root_name,))

        descriptors: list[tuple[Any, tuple[str, ...], Directory]] = []
        for index, entry in enumerate(entries):
            file_id = str(getattr(entry, "id", "") or "")
            raw_path = str(getattr(entry, "path", "") or "")
            if not file_id:
                raise OSError("Google Drive returned a file without an ID")
            try:
                raw_parts = torrent_relative_parts(raw_path)
                # Some folder listings include the root prefix, while gdown's current
                # public API normally returns paths relative to the selected folder.
                if len(raw_parts) > 1 and raw_parts[0].casefold() == root_name.casefold():
                    raw_parts = raw_parts[1:]
                relative_parts = tuple(safe_torrent_component(part) for part in raw_parts)
                if not relative_parts:
                    raise ValueError("Google Drive returned an empty file path")
            except ValueError as error:
                raise OSError(f"Google Drive returned an unsafe path: {raw_path!r}") from error

            target_directory = await ensure_torrent_directory(
                file.username,
                base_directory,
                (root_name, *relative_parts[:-1]),
            )
            item = {
                "file_index": index,
                "name": "/".join((root_name, *relative_parts)),
                "path": "/".join((root_name, *relative_parts)),
                "size": 0,
                "total": 0,
                "transfer": 0,
                "download_transfer": 0,
                "download_total": 0,
                "download_speed": 0.0,
                "download_eta": None,
                "upload_transfer": 0,
                "upload_total": 0,
                "upload_speed": 0.0,
                "upload_eta": None,
                "status": "queued",
                "message": "Queued for download",
            }
            items.append(item)
            descriptors.append((entry, relative_parts, target_directory))

        progress.torrent_items = [dict(item) for item in items]
        progress.message = "Downloading files" if entries else "Google Drive folder import complete"
        if not entries:
            refresh_snapshot("Google Drive folder import complete")
            yield progress
            return

        download_concurrency = max_downloads
        for index, (entry, relative_parts, target_directory) in enumerate(descriptors):
            file_id = str(getattr(entry, "id"))
            download_queue.put_nowait((index, file_id, relative_parts, target_directory, items[index]))

        for _ in range(download_concurrency):
            download_queue.put_nowait(None)

        download_workers = [
            create_task(download_worker(), name=f"gdrive-download-{i}")
            for i in range(download_concurrency)
        ]

        async def finish_downloads() -> None:
            await gather(*download_workers)
            await ready_queue.put(None)

        coordinator = create_task(finish_downloads(), name="gdrive-download-coordinator")
        uploader = create_task(upload_worker(), name="gdrive-upload-queue")
        while not coordinator.done() or not uploader.done():
            if any(item.get("status") == "uploading" for item in items):
                stage = "Uploading file"
            elif any(item.get("status") == "downloading" for item in items):
                stage = "Downloading files"
            elif any(item.get("status") == "waiting_upload" for item in items):
                stage = "Waiting for upload"
            else:
                stage = "Downloading files"
            refresh_snapshot(stage)
            yield progress
            await sleep(0.25)

        await gather(coordinator, uploader)
        failures = sum(item.get("status") == "error" for item in items)
        refresh_snapshot(
            f"Upload failed: {failures} of {len(items)} Google Drive folder files failed"
            if failures else "Google Drive folder import complete"
        )
        yield progress
        write_log(
            "INFO" if not failures else "WARNING",
            data_center,
            "GOOGLE DRIVE",
            file.username,
            f"Folder import finished for {root_name}: {len(items) - failures} succeeded, {failures} failed",
        )
    finally:
        await gather(
            *(cancel_ingestion_upload(ticket) for ticket in tickets.values()),
            return_exceptions=True,
        )
        for task in download_workers:
            if not task.done():
                task.cancel()
        if coordinator is not None and not coordinator.done():
            coordinator.cancel()
        if uploader is not None and not uploader.done():
            uploader.cancel()
        await gather(*download_workers, *(task for task in (coordinator, uploader) if task is not None), return_exceptions=True)
        rmtree(temp_dir, ignore_errors=True)


async def download_torrent(file: File, link: str, selected_indices: set[int] | None = None) -> AsyncGenerator[Progress, None]:
    """Upload each selected torrent file in the background as soon as it finishes downloading."""

    from core.transfer import file_upload

    data_center: type[DataCenter] | None = DataCenter(file.data_center)

    if data_center is None:
        raise ValueError(f"Unsupported data center: {file.data_center}")

    base_directory: Directory | None = await Directory.get(file.username, did=file.directory_id)

    if base_directory is None or base_directory.id is None:
        raise ValueError("The destination directory no longer exists")

    safe_link: str = sanitize_sensitive_urls(link)
    temp_dir: Path = TRANSFER_PATH / file.username / "torrent-download" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    progress: Progress = Progress(file.name or "Torrent", "Connecting to torrent network", 0)
    progress.ingestion_type = "torrent"
    progress.download_active = True
    torrent_session: Any | None = None
    handle: Any | None = None
    active_uploads: dict[int, Task[File]] = {}
    pending_uploads: dict[int, tuple[File, Path, dict[str, Any], str, int, int, IngestionUploadTicket]] = {}
    max_concurrent_file_uploads: int = max(1, min(2, int(data_center.MAX_PARALLEL_UPLOADS)))
    tray_items: list[dict[str, Any]] = []

    async def upload_completed_file(
            torrent_file: File,
            local_path: Path,
            item: dict[str, Any],
            relative_name: str,
            local_size: int,
            sequence: int,
            ticket: IngestionUploadTicket,
    ) -> File:
        """Upload one complete file under the shared Redis FIFO and persist its metadata."""

        try:
            async with ingestion_upload_slot(ticket):
                item.update({
                    "transfer": 0,
                    "total": local_size,
                    "download_transfer": local_size,
                    "download_total": local_size,
                    "upload_transfer": 0,
                    "upload_total": local_size,
                    "status": "uploading",
                    "message": "Uploading",
                })
                async with aclosing(file_upload(torrent_file, local_path, intermediate=True, queue_slot_held=True)) as upload_stream:
                    async for upload_progress in upload_stream:
                        transferred: int = min(local_size, max(0, int(upload_progress.transfer or 0)))
                        item.update({
                            "transfer": transferred,
                            "total": local_size,
                            "download_transfer": local_size,
                            "download_total": local_size,
                            "upload_transfer": transferred,
                            "upload_total": local_size,
                            "status": "uploading",
                            "message": (
                                f"Uploading · {transferred * 100 / local_size:.1f}%"
                                if local_size else "Uploading"
                            ),
                        })
                        await sleep(0)

                await torrent_file.save()
                local_path.unlink(missing_ok=True)
            item.update({
                "transfer": local_size,
                "total": local_size,
                "download_transfer": local_size,
                "download_total": local_size,
                "upload_transfer": local_size,
                "upload_total": local_size,
                "status": "completed",
                "message": "Completed",
            })
            write_log(
                "INFO",
                data_center,
                "TORRENT",
                file.username,
                f"Uploaded completed torrent file {sequence}: {relative_name}",
            )
            return torrent_file

        except Exception as error:
            item.update({
                "status": "error",
                "message": f"Upload failed: {str(error)[:160]}",
            })
            raise
        finally:
            if not ticket.claimed:
                await cancel_ingestion_upload(ticket)

    def start_pending_uploads() -> None:
        """Start only a bounded number of upload coroutines for already-enqueued FIFO tickets."""

        while pending_uploads and len(active_uploads) < max_concurrent_file_uploads:
            index = next(iter(pending_uploads))
            (
                torrent_file,
                local_path,
                item,
                relative_name,
                local_size,
                sequence,
                ticket,
            ) = pending_uploads.pop(index)
            active_uploads[index] = create_task(
                upload_completed_file(
                    torrent_file,
                    local_path,
                    item,
                    relative_name,
                    local_size,
                    sequence,
                    ticket,
                )
            )

    try:
        write_log("INFO", data_center, "TORRENT", file.username, f"Starting magnet download: {safe_link}")
        torrent_session = libtorrent.session({
            "listen_interfaces": "0.0.0.0:0",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
        })
        handle = torrent_session.add_torrent({"url": link, "save_path": str(temp_dir)})

        async for progress_value in wait_for_torrent_metadata(handle, progress, file.name or "Torrent"):
            yield progress_value

        torrent_info: Any = handle.torrent_file()
        torrent_files: Any = torrent_info.files()
        torrent_name_raw: str = str(torrent_info.name())
        torrent_name_parts: tuple[str, ...] = torrent_relative_parts(torrent_name_raw)

        if len(torrent_name_parts) != 1:
            raise ValueError("Torrent root name must be a single safe path component")

        torrent_name_raw = torrent_name_parts[0]
        torrent_name: str = safe_torrent_component(torrent_name_raw)
        file_count: int = int(torrent_files.num_files())
        padding_check: Callable = cast(Callable, getattr(torrent_files, "pad_file_at", None))
        selected: set[int] = set(selected_indices) if selected_indices is not None else {
            index for index in range(file_count)
            if not callable(padding_check) or not padding_check(index)
        }

        if not selected or any(index < 0 or index >= file_count for index in selected):
            raise ValueError("Torrent file selection is empty or contains invalid file indexes")

        handle.prioritize_files([4 if index in selected else 0 for index in range(file_count)])

        selected_order: list[int] = sorted(
            index for index in selected
            if not (callable(padding_check) and padding_check(index))
        )
        if not selected_order:
            raise OSError("Torrent selection did not contain any uploadable files")

        file_sizes: dict[int, int] = {index: int(torrent_files.file_size(index)) for index in selected_order}
        total_upload_bytes: int = sum(file_sizes.values())
        progress.name = torrent_name
        progress.total = total_upload_bytes
        progress.transfer = 0
        progress.message = "Torrent metadata received; downloading selected files"

        for index in selected_order:
            raw_path = str(torrent_files.file_path(index))
            parts = torrent_relative_parts(raw_path)
            display_parts = parts[1:] if len(parts) > 1 and parts[0] == torrent_name_raw else parts
            tray_items.append({
                "file_index": index,
                "name": "/".join(display_parts) or Path(raw_path).name,
                "transfer": 0,
                "total": file_sizes[index],
                "download_transfer": 0,
                "download_total": file_sizes[index],
                "download_speed": 0.0,
                "download_eta": None,
                "upload_transfer": 0,
                "upload_total": file_sizes[index],
                "upload_speed": 0.0,
                "upload_eta": None,
                "status": "queued",
                "message": "Waiting for download",
            })

        progress.torrent_items = [dict(item) for item in tray_items]
        yield progress

        processed_indices: set[int] = set()
        saved_count: int = 0
        scheduled_count: int = 0
        first_saved_id: int | None = None

        while len(processed_indices) < len(selected_order):
            # Collect finished uploads while libtorrent continues downloading.
            for index, task in list(active_uploads.items()):
                if not task.done():
                    continue

                del active_uploads[index]
                try:
                    saved_file: File = task.result()
                except Exception:
                    # One file's provider failure must not cancel downloads/uploads for
                    # other selected torrent files. The worker already recorded its error.
                    processed_indices.add(index)
                    continue

                processed_indices.add(index)
                saved_count += 1

                if first_saved_id is None:
                    first_saved_id = saved_file.id

            start_pending_uploads()
            status: Any = handle.status()
            raise_torrent_error(status)
            raw_file_progress = handle.file_progress()
            file_progress = [max(0, int(value)) for value in raw_file_progress]

            # Keep each file's download progress current without overwriting the
            # upload progress reported by its background task.
            for item in tray_items:
                index = int(item["file_index"])
                size = file_sizes[index]

                if index in processed_indices:
                    if item.get("status") != "error":
                        item.update({
                            "transfer": size,
                            "total": size,
                            "download_transfer": size,
                            "download_total": size,
                            "upload_transfer": size,
                            "upload_total": size,
                            "status": "completed",
                            "message": "Completed",
                        })
                    continue

                if index in active_uploads or index in pending_uploads:
                    continue

                downloaded = min(size, file_progress[index]) if index < len(file_progress) else 0
                item.update({
                    "transfer": downloaded,
                    "total": size,
                    "status": "downloading" if downloaded > 0 else "queued",
                    "message": "Downloading" if downloaded > 0 else "Waiting for download",
                })

            # Start uploads as soon as individual files are complete and visible.
            # These bounded background tasks run alongside libtorrent and remove each
            # local file immediately after its upload succeeds, rather than waiting for
            # the whole torrent to finish downloading.
            for index in selected_order:
                if index in processed_indices or index in active_uploads or index in pending_uploads:
                    continue

                size = file_sizes[index]
                downloaded = file_progress[index] if index < len(file_progress) else 0
                if downloaded < size:
                    continue

                try:
                    resolved = get_downloadable_torrent_files(
                        torrent_files,
                        temp_dir,
                        torrent_name_raw,
                        torrent_name,
                        {index},
                    )
                except FileNotFoundError:
                    continue

                if not resolved:
                    continue

                candidate_index, candidate_path, target_parts = resolved[0]
                if candidate_index != index or not candidate_path.is_file() or candidate_path.stat().st_size < size:
                    continue

                # The file is complete, so mark it as unwanted by libtorrent before
                # upload/delete. This helps prevent the torrent engine from retaining
                # or recreating the local payload after it has been transferred.
                file_priority = getattr(handle, "file_priority", None)
                if callable(file_priority):
                    try:
                        file_priority(index, 0)
                    except Exception:
                        pass

                # Directory creation stays in this single loop, preventing multiple
                # completed files from racing to create the same nested directory.
                target_directory: Directory = await ensure_torrent_directory(
                    file.username, base_directory, target_parts[:-1]
                )
                target_name: str = target_parts[-1]
                relative_display_path: str = "/".join(target_parts)
                local_size: int = candidate_path.stat().st_size

                if first_saved_id is None and saved_count == 0 and not active_uploads and not pending_uploads:
                    torrent_file: File = file
                    torrent_file.directory_id = cast(int, target_directory.id)
                    torrent_file.name = target_name
                    torrent_file.type = guess_type(target_name)[0] or "application/octet-stream"
                    torrent_file.size = local_size
                    torrent_file.modified_at = datetime.now(timezone.utc)
                    torrent_file.links = []
                else:
                    torrent_file = File(
                        directory_id=cast(int, target_directory.id),
                        name=target_name,
                        type=guess_type(target_name)[0] or "application/octet-stream",
                        size=local_size,
                        modified_at=datetime.now(timezone.utc),
                        links=[],
                        data_center=file.data_center,
                        username=file.username,
                    )

                scheduled_count += 1
                item = next(item for item in tray_items if int(item["file_index"]) == index)
                item.update({
                    "name": relative_display_path,
                    "transfer": local_size,
                    "total": local_size,
                    "download_transfer": local_size,
                    "download_total": local_size,
                    "upload_transfer": 0,
                    "upload_total": local_size,
                    "status": "waiting_upload",
                    "message": "Downloaded · waiting for upload",
                })
                write_log(
                    "INFO",
                    data_center,
                    "TORRENT",
                    file.username,
                    f"Uploading completed torrent file {scheduled_count}/{len(selected_order)}: {relative_display_path}",
                )
                # Enter the global FIFO immediately after the on-disk file is verified.
                # Only a bounded number of local coroutines wait for/acquire upload slots.
                ticket = await enqueue_ingestion_upload(relative_display_path, file.username)
                pending_uploads[index] = (
                    torrent_file,
                    candidate_path,
                    item,
                    relative_display_path,
                    local_size,
                    scheduled_count,
                    ticket,
                )

            start_pending_uploads()

            # Persist download and storage-upload bytes independently; item-level metrics
            # are computed by the backend and survive Redis status polling and page refreshes.
            aggregate_download: int = 0
            aggregate_upload: int = 0
            sample_time: float = monotonic()
            for item in tray_items:
                index = int(item["file_index"])
                size = file_sizes[index]
                item_status = str(item.get("status") or "queued")

                if item_status in {"downloaded", "waiting_upload", "uploading", "completed"}:
                    downloaded = size
                else:
                    downloaded = min(size, file_progress[index]) if index < len(file_progress) else 0
                if item_status == "completed":
                    uploaded = size
                elif item_status == "uploading":
                    uploaded = min(
                        size,
                        max(0, int(item.get("upload_transfer", item.get("transfer", 0) or 0))),
                    )
                else:
                    uploaded = 0
                item.update({
                    "download_transfer": downloaded,
                    "download_total": size,
                    "upload_transfer": uploaded,
                    "upload_total": size,
                })
                previous_at = float(item.get("_sample_at") or sample_time)
                elapsed = max(0.0, sample_time - previous_at)
                previous_download = max(0, int(item.get("_sample_download") or 0))
                previous_upload = max(0, int(item.get("_sample_upload") or 0))

                # A completed polling cycle does not imply bytes moved. If we blend a
                # zero-byte delta on every cycle, the 0.7 smoothing factor drives a
                # still-valid speed toward zero and produces absurdly large ETAs.
                # Keep the last measured speed until bytes move again or 4 seconds pass.
                try:
                    previous_download_speed = float(item.get("download_speed") or 0)
                except (TypeError, ValueError):
                    previous_download_speed = 0.0
                if not math.isfinite(previous_download_speed) or previous_download_speed < 0:
                    previous_download_speed = 0.0

                try:
                    previous_upload_speed = float(item.get("upload_speed") or 0)
                except (TypeError, ValueError):
                    previous_upload_speed = 0.0
                if not math.isfinite(previous_upload_speed) or previous_upload_speed < 0:
                    previous_upload_speed = 0.0

                try:
                    previous_download_moved_at = float(
                        item.get("_download_moved_at") or item.get("_sample_at") or sample_time
                    )
                except (TypeError, ValueError):
                    previous_download_moved_at = sample_time
                if not math.isfinite(previous_download_moved_at) or previous_download_moved_at > sample_time:
                    previous_download_moved_at = sample_time

                try:
                    previous_upload_moved_at = float(
                        item.get("_upload_moved_at") or item.get("_sample_at") or sample_time
                    )
                except (TypeError, ValueError):
                    previous_upload_moved_at = sample_time
                if not math.isfinite(previous_upload_moved_at) or previous_upload_moved_at > sample_time:
                    previous_upload_moved_at = sample_time

                if downloaded < previous_download:
                    download_speed = 0.0
                    download_moved_at = sample_time
                elif elapsed > 0 and downloaded > previous_download and item_status in {"downloading", "downloaded", "waiting_upload", "uploading", "completed"}:
                    measured_speed = (downloaded - previous_download) / elapsed
                    download_speed = measured_speed if previous_download_speed <= 0 else previous_download_speed * 0.7 + measured_speed * 0.3
                    download_moved_at = sample_time
                else:
                    download_moved_at = previous_download_moved_at
                    download_speed = 0.0 if sample_time - download_moved_at >= 4 else previous_download_speed

                if uploaded < previous_upload:
                    upload_speed = 0.0
                    upload_moved_at = sample_time
                elif elapsed > 0 and uploaded > previous_upload and item_status in {"uploading", "completed"}:
                    measured_speed = (uploaded - previous_upload) / elapsed
                    upload_speed = measured_speed if previous_upload_speed <= 0 else previous_upload_speed * 0.7 + measured_speed * 0.3
                    upload_moved_at = sample_time
                else:
                    upload_moved_at = previous_upload_moved_at
                    upload_speed = 0.0 if sample_time - upload_moved_at >= 4 else previous_upload_speed

                item["download_speed"] = download_speed if math.isfinite(download_speed) and download_speed > 0 else 0.0
                item["upload_speed"] = upload_speed if math.isfinite(upload_speed) and upload_speed > 0 else 0.0

                download_remaining = max(0, size - downloaded)
                upload_remaining = max(0, size - uploaded)
                download_eta = download_remaining / item["download_speed"] if download_remaining and item["download_speed"] > 0 else None
                upload_eta = upload_remaining / item["upload_speed"] if upload_remaining and item["upload_speed"] > 0 else None
                max_eta = 365 * 24 * 60 * 60
                item["download_eta"] = download_eta if download_eta is not None and math.isfinite(download_eta) and download_eta <= max_eta else None
                item["upload_eta"] = upload_eta if upload_eta is not None and math.isfinite(upload_eta) and upload_eta <= max_eta else None

                item["_download_moved_at"] = download_moved_at
                item["_upload_moved_at"] = upload_moved_at
                item["_sample_at"] = sample_time
                item["_sample_download"] = downloaded
                item["_sample_upload"] = uploaded
                aggregate_download += downloaded
                aggregate_upload += uploaded

            progress.ingestion_type = "torrent"
            progress.download_active = True
            progress.download_transfer = min(total_upload_bytes, aggregate_download)
            progress.download_total = total_upload_bytes
            progress.name = torrent_name
            progress.transfer = min(total_upload_bytes, aggregate_upload)
            progress.total = total_upload_bytes
            progress.torrent_items = [dict(item) for item in tray_items]

            peers = int(getattr(status, "num_peers", 0) or 0)
            seeds = int(getattr(status, "num_seeds", 0) or 0)
            seeds = int(getattr(status, "num_seeds", 0) or 0)
            if peers:
                progress.message = f"Downloading torrent files ({peers} peers, {seeds} seeds)"
            elif any(item.get("status") == "uploading" for item in tray_items):
                progress.message = "Uploading completed files"
            elif any(item.get("status") == "waiting_upload" for item in tray_items):
                progress.message = "Waiting for upload slot"
            else:
                progress.message = "Searching for peers"

            yield progress

            is_finished = bool(getattr(status, "is_finished", False))
            is_seeding = bool(getattr(status, "is_seeding", False))
            if is_finished or is_seeding:
                pending_downloads = [
                    index for index in selected_order
                    if index not in processed_indices and index not in active_uploads
                ]
                if pending_downloads and all(
                        index < len(file_progress) and file_progress[index] >= file_sizes[index]
                        for index in pending_downloads
                ):
                    # libtorrent can report completion just before its writer exposes
                    # a fully downloaded file on disk. Give the storage writer time to flush.
                    await sleep(0.25)
                elif pending_downloads:
                    raise RuntimeError("Torrent reported completion before every selected file finished downloading")

            await sleep(0.5)

        file.id = first_saved_id
        failed_count = sum(item.get("status") == "error" for item in tray_items)
        final_message = (
            f"Upload failed: {failed_count} of {len(selected_order)} torrent files failed; "
            f"{saved_count} completed"
            if failed_count else f"Upload Complete: {saved_count} torrent file(s)"
        )
        final_progress: Progress = Progress(torrent_name, final_message, total_upload_bytes)
        final_progress.transfer = sum(
            min(file_sizes[int(item["file_index"])], max(0, int(item.get("upload_transfer") or 0)))
            for item in tray_items
            if int(item["file_index"]) in file_sizes
        )
        final_progress.download_transfer = total_upload_bytes
        final_progress.download_total = total_upload_bytes
        final_progress.download_active = True
        final_progress.ingestion_type = "torrent"
        final_progress.torrent_items = [dict(item) for item in tray_items]
        write_log(
            "INFO" if not failed_count else "WARNING",
            data_center,
            "TORRENT",
            file.username,
            f"Torrent download and sequential upload finished: {saved_count} succeeded, {failed_count} failed, {total_upload_bytes} bytes selected",
        )
        yield final_progress

    except Exception as error:
        details: str = sanitize_sensitive_urls(f"{error}\n{format_exc()}")
        write_log("ERROR", data_center, "TORRENT", file.username, f"Torrent import failed: {details}")
        raise

    finally:
        active_tasks: list[Task[File]] = list(active_uploads.values())
        for task in active_tasks:
            task.cancel()

        if active_tasks:
            await gather(*active_tasks, return_exceptions=True)

        await gather(
            *(cancel_ingestion_upload(work[-1]) for work in pending_uploads.values()),
            return_exceptions=True,
        )

        if torrent_session is not None and handle is not None:
            try:
                try:
                    handle.pause()
                finally:
                    torrent_session.remove_torrent(handle)
            except Exception:
                pass

        rmtree(temp_dir, ignore_errors=True)
