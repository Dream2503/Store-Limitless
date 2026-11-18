"""Coordinate file uploads and remote-link downloads through storage providers."""

from __future__ import annotations

from asyncio import Task
from contextlib import aclosing
from pathlib import Path
from traceback import format_exc
from typing import AsyncGenerator
from urllib.parse import ParseResult, parse_qs, urlparse

from backend.database.models import File, User
from core.data_center import DataCenter
from core.utils import sanitize_sensitive_urls, write_log
from core.utils.download_ import download_processed_link, download_unprocessed_link
from core.utils.download_utils import download_google_drive_folder, is_google_drive_folder_url
from core.utils.progress import Progress, upload_growing_file
from core.utils.upload_manager import (
    IngestionUploadTicket,
    cancel_ingestion_upload,
    enqueue_ingestion_upload,
    ingestion_upload_slot,
)


async def file_upload(
        file: File,
        file_path: Path,
        upload_task: Task[None] | None = None,
        intermediate: bool = False,
        track_download: bool = False,
        download_total: int = 0,
        queue_slot_held: bool = False,
) -> AsyncGenerator[Progress, None]:
    """Upload one file through the shared FIFO, optionally leaving its database save to the caller."""

    data_center: type[DataCenter] | None = DataCenter(file.data_center)

    if data_center is None:
        raise ValueError(f"Unsupported data center: {file.data_center}")

    ticket: IngestionUploadTicket | None = None
    if not queue_slot_held:
        ticket = await enqueue_ingestion_upload(file.name, file.username)

    async def upload_and_finalize() -> AsyncGenerator[Progress, None]:
        """Upload the original and persist it when this caller owns finalization."""

        write_log(
            "INFO", data_center, "UPLOAD", file.username,
            f"Starting original upload name={file.name!r} size={max(0, int(file.size or 0))} "
            f"data_center={file.data_center!r} intermediate={intermediate}.",
        )


        try:
            write_log("INFO", data_center, "UPLOAD", file.username, f"Found local upload source {file_path.name!r}")
            progress: Progress = Progress(file.name, "Uploading File", 0)

            async with aclosing(upload_growing_file(
                    file,
                    file_path,
                    data_center,
                    progress,
                    upload_task,
                    track_download=track_download,
                    download_total=download_total,
            )) as upload_progress_stream:
                async for progress_value in upload_progress_stream:
                    yield progress_value

            if not intermediate:
                await file.save()
                write_log(
                    "INFO", data_center, "UPLOAD", file.username,
                    f"Original upload completed file_id={file.id} name={file.name!r} "
                    f"size={file.size} chunks={len(file.links)}.",
                )
                progress.message = f"Upload Complete of id={file.id}"
                yield progress
                file_path.unlink(missing_ok=True)

        except Exception as error:
            write_log(
                "ERROR", data_center, "UPLOAD", file.username,
                f"Original upload failed file_id={file.id} name={file.name!r}: "
                f"{sanitize_sensitive_urls(str(error) + chr(10) + format_exc())}",
            )
            raise

    try:
        if queue_slot_held:
            async for progress in upload_and_finalize():
                yield progress
        else:
            if ticket is None:
                raise RuntimeError("The shared upload queue ticket was not initialized.")
            async with ingestion_upload_slot(ticket):
                async for progress in upload_and_finalize():
                    yield progress
    finally:
        if ticket is not None and not ticket.claimed:
            await cancel_ingestion_upload(ticket)


async def link_upload(file: File, link: str, selected_torrent_files: set[int] | None = None) -> AsyncGenerator[Progress, None]:
    """Import a direct URL, YouTube link, or selected torrent files into the provider."""

    from core.utils.download_utils import download_torrent

    user: User | None = await User.get(file.username)

    if user is None:
        raise ValueError(f"User not found: {file.username}")

    data_center: type[DataCenter] | None = DataCenter(file.data_center)

    if data_center is None:
        raise ValueError(f"Unsupported data center: {file.data_center}")

    progress: Progress = Progress("link", "Uploading File", 0)
    safe_link: str = sanitize_sensitive_urls(link)
    parsed: ParseResult = urlparse(link)
    source_host: str = parsed.hostname or "unknown host"
    write_log("INFO", data_center, "DOWNLOAD", user.username, f"Got link from host: {source_host} ({safe_link})")

    try:
        write_log("INFO", data_center, "DOWNLOAD", user.username, f"Starting download from `{safe_link}`")
        parsed: ParseResult = urlparse(link)
        host: str = (parsed.hostname or "").lower().removeprefix("www.")
        is_torrent: bool = parsed.scheme.lower() == "magnet"

        if is_google_drive_folder_url(link):
            async with aclosing(download_google_drive_folder(file, link)) as folder_stream:
                async for progress_value in folder_stream:
                    yield progress_value

            # Folder imports create one database file row per child, not a placeholder
            # record for the submitted folder URL.
            return

        if is_torrent:
            exact_topics: list[str | bytes] = parse_qs(parsed.query).get("xt", [])
            has_info_hash: bool = any(topic.lower().startswith(("urn:btih:", "urn:btmh:")) for topic in exact_topics)

            if not link.lower().startswith("magnet:?") or not has_info_hash:
                raise ValueError("Expected a valid torrent magnet URI with an xt info-hash topic")

            # Closing the parent link generator must close the torrent generator too,
            # otherwise its finally block may not pause/remove the libtorrent handle.
            async with aclosing(download_torrent(file, link, selected_torrent_files)) as torrent_stream:
                async for progress_value in torrent_stream:
                    yield progress_value

            write_log("INFO", data_center, "DOWNLOAD", user.username, f"Torrent download and upload complete ({sanitize_sensitive_urls(link)})")
            return

        else:
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Expected a valid HTTP(S) download URL or magnet URI")

            if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
                downloader = download_processed_link

            else:
                downloader = download_unprocessed_link

        async with aclosing(downloader(file, link)) as download_stream:
            async for progress_value in download_stream:
                yield progress_value

        await file.save()
        write_log("INFO", data_center, "DOWNLOAD", user.username, f"Download and upload complete `{file.name}`")
        progress.message = f"Upload Complete of id={file.id}"
        yield progress

    except Exception as error:
        details: str = sanitize_sensitive_urls(f"{error}\n{format_exc()}")
        write_log("ERROR", data_center, "DOWNLOAD", user.username, f"Link upload failed: {details}")
        raise
