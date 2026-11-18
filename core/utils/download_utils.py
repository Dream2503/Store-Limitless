"""Download URL, media, and torrent links into StoreLimitless storage."""

from __future__ import annotations

from asyncio import sleep
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from mimetypes import guess_type
from pathlib import Path, PurePosixPath
from shutil import rmtree
from time import monotonic
from traceback import format_exc
from typing import Any, AsyncGenerator, Callable, cast
from uuid import uuid4

import gdown
import libtorrent
from requests import Response
from yt_dlp import YoutubeDL

from backend.database.models import Directory, File
from core.config import TRANSFER_PATH
from core.data_center import DataCenter
from core.utils import sanitize_sensitive_urls, write_log
from core.utils.progress import Progress


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
    """Download a Google Drive file to ``output`` using gdown."""

    downloaded: str | None = cast(str | None, gdown.download(link, output=str(output), quiet=True))

    if downloaded is None or not output.is_file():
        raise OSError("Google Drive download failed")


def stream_response(response: Response, output: Path) -> None:
    """Stream a remote HTTP response to disk and close the response."""

    try:
        with output.open("wb", buffering=0) as destination:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    destination.write(chunk)

    finally:
        response.close()


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


async def download_torrent(file: File, link: str, selected_indices: set[int] | None = None) -> AsyncGenerator[Progress, None]:
    """Download selected magnet files, then upload them sequentially."""

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

    try:
        write_log("INFO", data_center, "TORRENT", file.username, f"Starting magnet download: {safe_link}")
        download_result: dict[str, Any] = {}

        async for progress_value in download_torrent_to_disk(
                link, temp_dir, progress, file.name or "Torrent", download_result, selected_indices
        ):
            yield progress_value

        if not download_result:
            raise RuntimeError("Torrent downloader did not return its completed metadata")

        torrent_files: Any = download_result["torrent_files"]
        torrent_name_raw: str = str(download_result["torrent_name_raw"])
        torrent_name: str = str(download_result["torrent_name"])
        downloadable_files: list[tuple[int, Path, tuple[str, ...]]] = get_downloadable_torrent_files(
            torrent_files,
            temp_dir,
            torrent_name_raw,
            torrent_name,
            selected_indices,
        )

        if not downloadable_files:
            raise OSError("Torrent completed but did not contain any uploadable files")

        total_upload_bytes: int = sum(path.stat().st_size for _, path, _ in downloadable_files)
        saved_count: int = 0
        first_saved_id: int | None = None
        tray_items: list[dict[str, Any]] = [
            {
                "file_index": torrent_file_index,
                "name": "/".join(target_parts),
                "transfer": path.stat().st_size,
                "total": path.stat().st_size,
                "status": "downloaded",
                "message": "Downloaded · waiting to upload",
            }
            for torrent_file_index, path, target_parts in downloadable_files
        ]
        progress.torrent_items = [dict(item) for item in tray_items]

        for item_number, (torrent_file_index, local_path, target_parts) in enumerate(downloadable_files, start=1):
            target_directory: Directory = await ensure_torrent_directory(file.username, base_directory, target_parts[:-1])
            target_name: str = target_parts[-1]
            local_size: int = local_path.stat().st_size

            if saved_count == 0:
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

            relative_display_path: str = "/".join(target_parts)
            write_log(
                "INFO",
                data_center,
                "TORRENT",
                file.username,
                f"Uploading torrent file {item_number}/{len(downloadable_files)}: {relative_display_path}",
            )
            tray_items[item_number - 1].update({"file_index": torrent_file_index, "transfer": 0, "status": "uploading", "message": "Uploading"})

            async for upload_progress in file_upload(torrent_file, local_path, intermediate=True):
                upload_progress.name = relative_display_path
                upload_progress.message = f"Uploading file {item_number}/{len(downloadable_files)}"
                upload_progress.transfer = min(local_size, max(0, int(upload_progress.transfer or 0)))
                upload_progress.total = local_size
                tray_items[item_number - 1].update({
                    "transfer": upload_progress.transfer,
                    "total": local_size,
                    "status": "uploading",
                    "message": f"Uploading · {upload_progress.transfer * 100 / local_size:.1f}%" if local_size else "Uploading",
                })
                upload_progress.torrent_items = [dict(item) for item in tray_items]
                yield upload_progress

            await torrent_file.save()
            tray_items[item_number - 1].update({"transfer": local_size, "total": local_size, "status": "completed", "message": "Completed"})

            if first_saved_id is None:
                first_saved_id = torrent_file.id

            saved_count += 1

        file.id = first_saved_id
        final_progress: Progress = Progress(torrent_name, f"Upload Complete: {saved_count} torrent file(s)", total_upload_bytes)
        final_progress.transfer = total_upload_bytes
        final_progress.torrent_items = [dict(item) for item in tray_items]
        write_log(
            "INFO",
            data_center,
            "TORRENT",
            file.username,
            f"Torrent download and sequential upload complete: {saved_count} file(s), {total_upload_bytes} bytes",
        )
        yield final_progress

    except Exception as error:
        details: str = sanitize_sensitive_urls(f"{error}\n{format_exc()}")
        write_log("ERROR", data_center, "TORRENT", file.username, f"Torrent import failed: {details}")
        raise

    finally:
        rmtree(temp_dir, ignore_errors=True)
