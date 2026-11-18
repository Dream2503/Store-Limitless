"""Public readiness, upload status, and public file-stream endpoints."""

from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from pathlib import Path
from mimetypes import guess_type
from typing import AsyncGenerator, cast, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from backend.database.models import Directory, File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import get_current_user
from backend.server.security import verify_public_stream_token, verify_public_zip_token
from backend.server.zip_stream import ZipStreamEntry, stream_zip64, zip64_stored_content_length
from core.stream import Stream
from core.utils.discord_ import Discord
from core.utils.progress import Progress
from .upload_state import get_upload_job_metadata

router: APIRouter = APIRouter()


@router.get("/ready")
async def ready() -> JSONResponse:
    """Report whether the Discord-backed data center is ready."""

    return JSONResponse({"ready": Discord.ready})


@router.get("/upload/{job_id}/status")
async def upload_status(job_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Return progress only to the account that owns the upload job."""

    metadata: dict[str, Any] | None = await get_upload_job_metadata(job_id)

    if metadata is None or metadata.get("username") != user.username:
        raise HTTPException(status_code=404, detail="Upload job not found")

    try:
        progress: Progress = cast(Progress, await Redis.get(job_id, "Progress"))

    except KeyError:
        raise HTTPException(status_code=404, detail="Upload job not found")

    message_lower: str = progress.message.lower()
    completed: bool = message_lower.startswith("upload complete") or message_lower.startswith("google drive folder import complete")
    failed: bool = message_lower.startswith("upload failed")
    cancelled: bool = bool(metadata.get("cancelled")) or message_lower.startswith("upload cancelled")
    file_id: int | None = None

    if completed and "id=" in progress.message:
        try:
            file_id = int(progress.message.split("id=")[-1].split()[0])

        except (ValueError, IndexError):
            file_id = None

    name: str | None | Any = progress.name if progress.name not in {"update_name_from_backend", "link", ""} else metadata.get("name")
    return JSONResponse({
        "status": "cancelled" if cancelled else "completed" if completed else "error" if failed else "uploading",
        "message": " ".join(progress.message.split()[:2]) if completed else progress.message,
        "name": name,
        "transfer": progress.transfer,
        "total": max(progress.total, int(metadata.get("total") or 0)),
        "download_transfer": max(0, int(getattr(progress, "download_transfer", 0) or 0)),
        "download_total": max(0, int(getattr(progress, "download_total", 0) or 0)),
        "download_active": bool(getattr(progress, "download_active", False)),
        "download_speed": max(0.0, float(getattr(progress, "download_speed", 0) or 0)),
        "upload_speed": max(0.0, float(getattr(progress, "upload_speed", 0) or 0)),
        "download_eta": getattr(progress, "download_eta", None),
        "upload_eta": getattr(progress, "upload_eta", None),
        "ingestion_type": str(getattr(progress, "ingestion_type", "") or ""),
        "file_counts": getattr(progress, "file_counts", {}) or {},
        "last_updated_at": max(0.0, float(getattr(progress, "last_updated_at", 0) or 0)),
        "torrent_items": [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in (getattr(progress, "torrent_items", []) or [])
            if isinstance(item, dict)
        ],
        "file_id": file_id,
    })


def _file_etag(file: File) -> str:
    """Create a stable strong validator for a stored file representation."""

    modified_at = getattr(file, "modified_at", None)
    source = f"{file.id}:{file.size}:{modified_at.isoformat() if modified_at else ''}"
    return f'"{sha256(source.encode("utf-8")).hexdigest()}"'


def _public_cors_headers(request: Request) -> dict[str, str]:
    """Attach CORS headers to public token-authorized media responses.

    Fetch requests can become redirect-tainted when the stable Vercel API host
    redirects to the active Cloudflare tunnel. Browsers may then send Origin: null
    to the final host, so matching only the frontend origin omits the CORS header.
    These routes are authorized by their signed token rather than browser cookies,
    so wildcard CORS is safe and keeps direct byte-range streaming available.
    """

    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, Range, If-Range, If-None-Match, If-Modified-Since, Accept, Origin, X-Requested-With",
        "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length, Content-Disposition, ETag",
        "Vary": "Origin",
    }


def _content_disposition(filename: str, attachment: bool) -> str:
    """Build a safe ASCII fallback plus an RFC 5987 UTF-8 filename."""

    fallback = filename.encode("ascii", "ignore").decode("ascii")
    fallback = fallback.replace('"', "'").replace("\\", "_").replace("/", "_") or "download"
    disposition = "attachment" if attachment else "inline"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"



async def _stream_while_connected(stream: Stream, request: Request) -> AsyncGenerator[bytes, None]:
    """Stop reading remote storage chunks when the HTTP client disconnects."""

    iterator = stream.stream()
    try:
        while True:
            if await request.is_disconnected():
                return

            try:
                chunk = await anext(iterator)
            except StopAsyncIteration:
                return

            # A disconnect may occur while the next remote storage chunk is loading.
            # Do not send that chunk after the consumer has already left.
            if await request.is_disconnected():
                return
            if chunk:
                yield chunk
    finally:
        await iterator.aclose()


async def _file_response(file: File, request: Request, *, attachment: bool, head_only: bool = False) -> Response:
    """Build a byte-range response with validators suitable for interrupted downloads."""

    cors_headers: dict[str, str] = _public_cors_headers(request)
    range_header = request.headers.get("range")
    if_range = request.headers.get("if-range")

    if not file.links and int(file.size) == 0:
        content_type = file.type or guess_type(file.name)[0] or "application/octet-stream"
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": content_type,
            "Content-Disposition": _content_disposition(file.name, attachment),
            "Cache-Control": "private, no-cache, no-transform",
            "ETag": _file_etag(file),
            "Content-Length": "0",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
            **cors_headers,
        }
        return Response(content=b"", status_code=200, headers=headers, media_type=content_type)

    try:
        stream = await Stream.prepare(file, range_header)

    except ValueError:
        size = max(0, int(file.size))
        etag = _file_etag(file)

        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes", "ETag": etag, **cors_headers})

    except OSError as error:
        raise HTTPException(status_code=502, detail=f"File storage metadata failure: {error}", headers=cors_headers) from error

    size = max(0, int(file.size))
    etag = _file_etag(file)

    if range_header and if_range and if_range.strip() != etag:
        range_header = None
        stream = Stream(file, None)

    content_type = file.type or guess_type(file.name)[0] or "application/octet-stream"
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "Content-Disposition": _content_disposition(file.name, attachment),
        "Cache-Control": "private, no-cache, no-transform",
        "ETag": etag,
        "Content-Length": str(size),
        "X-Content-Type-Options": "nosniff",
        "X-Accel-Buffering": "no",
        **cors_headers,
    }

    start = stream.byte_range.start
    end = stream.byte_range.end
    length = end - start + 1
    partial = range_header is not None
    headers["Content-Length"] = str(length)

    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    if head_only:
        return Response(status_code=206 if partial else 200, headers=headers, media_type=content_type)

    return StreamingResponse(_stream_while_connected(stream, request), status_code=206 if partial else 200, headers=headers, media_type=content_type)


async def _owned_public_file(token: str, request: Request) -> File:
    """Resolve a signed token to its original owner's file record."""

    try:
        payload: dict[str, str | int] = verify_public_stream_token(token)

    except ValueError as error:
        raise HTTPException(status_code=404, detail="Invalid public download link", headers=_public_cors_headers(request)) from error

    file = await File.get(cast(str, payload["username"]), fid=int(payload["file_id"]))

    if file is None or file.username != payload["username"]:
        raise HTTPException(status_code=404, detail="File not found", headers=_public_cors_headers(request))

    return file


@router.get("/metadata/{token}")
async def public_file_metadata(token: str, request: Request) -> JSONResponse:
    """Return only the metadata needed to render a public file view page."""

    file = await _owned_public_file(token, request)
    return JSONResponse({
        "id": file.id,
        "name": file.name,
        "type": file.type,
        "size": max(0, int(file.size or 0)),
    })


@router.get("/stream/{token}")
async def public_stream(token: str, request: Request) -> Response:
    """Stream a file with single-range and validator support for seeking/resuming."""

    file = await _owned_public_file(token, request)
    return await _file_response(file, request, attachment=False)


@router.get("/download/{token}")
async def public_download(token: str, request: Request) -> Response:
    """Download an attachment with byte ranges, validators, and resumable transfers."""

    file = await _owned_public_file(token, request)
    return await _file_response(file, request, attachment=True)


@router.head("/download/{token}")
async def public_download_head(token: str, request: Request) -> Response:
    """Expose file length and range validators for download managers before transfer."""

    file = await _owned_public_file(token, request)
    return await _file_response(file, request, attachment=True, head_only=True)


def _safe_zip_download_filename(value: str | None) -> str:
    """Normalize a user-supplied archive filename for a safe Content-Disposition header."""

    filename = unicodedata.normalize("NFC", str(value or "")).strip()
    # Accept a filename, not a path. Strip directories and replace characters that
    # are unsafe or invalid in common filesystems and HTTP header values.
    filename = filename.replace("\\", "/").split("/")[-1]
    filename = re.sub(r'[\x00-\x1f\x7f<>:"|?*]+', "_", filename).strip(" .")
    if not filename or filename in {".", ".."}:
        filename = "StoreLimitless-files"
    if not filename.lower().endswith(".zip"):
        filename += ".zip"
    # Keep Content-Disposition comfortably within common header limits.
    if len(filename) > 150:
        stem = filename[:-4] if filename.lower().endswith(".zip") else filename
        filename = f"{stem[:146]}.zip"
    return filename


@router.get("/download-zip/{token}")
async def public_zip_download(
        token: str,
        request: Request,
        filename: str = Query(default="StoreLimitless-files.zip", max_length=180),
) -> Response:
    """Stream selected files and folder trees into a ZIP without server-side archive staging."""

    try:
        payload = verify_public_zip_token(token)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Invalid or expired ZIP download link", headers=_public_cors_headers(request)) from error

    username = cast(str, payload["username"])
    file_ids = cast(list[int], payload.get("file_ids", []))
    folder_ids = cast(list[int], payload.get("folder_ids", []))

    all_directories = await Directory.get_all(username)
    # The UI intentionally hides these internal marker records; ZIP archives must
    # not expose them either when a whole directory tree is downloaded.
    all_files = [
        file for file in await File.get_all(username)
        if file.name != ".__folder__"
    ]
    directories_by_id = {
        int(directory.id): directory
        for directory in all_directories
        if directory.id is not None and directory.username == username
    }
    directory_path_by_id = {
        int(directory.id): Path(directory.path)
        for directory in all_directories
        if directory.id is not None and directory.username == username
    }

    selected_files: list[File] = []
    for file_id in file_ids:
        file = await File.get(username, fid=file_id)
        if file is None or file.username != username or file.name == ".__folder__":
            raise HTTPException(
                status_code=404,
                detail="One or more selected files are no longer available",
                headers=_public_cors_headers(request),
            )
        selected_files.append(file)

    selected_folders: list[Directory] = []
    for folder_id in folder_ids:
        folder = directories_by_id.get(folder_id)
        if folder is None or folder.username != username:
            raise HTTPException(
                status_code=404,
                detail="One or more selected folders are no longer available",
                headers=_public_cors_headers(request),
            )
        if Path(folder.path) == Path("/home"):
            raise HTTPException(
                status_code=400,
                detail="The home root cannot be downloaded as a folder",
                headers=_public_cors_headers(request),
            )
        selected_folders.append(folder)

    # If a selected folder contains another selected folder, only archive the parent
    # once. This also prevents a selected file inside a selected folder being added twice.
    selected_folders.sort(key=lambda folder: (len(Path(folder.path).parts), str(folder.path).casefold()))
    folder_roots: list[Directory] = []
    folder_root_paths: list[Path] = []
    for folder in selected_folders:
        folder_path = Path(folder.path)
        if any(folder_path == root or folder_path.is_relative_to(root) for root in folder_root_paths):
            continue
        folder_roots.append(folder)
        folder_root_paths.append(folder_path)

    async def stream_file(file: File):
        try:
            source = await Stream.prepare(file)
            async for chunk in source.stream():
                yield chunk
        except (OSError, ValueError) as error:
            # The ZIP has already started by this point; a storage failure ends the stream.
            raise OSError(f'Cannot read "{file.name}": {error}') from error

    entries: list[ZipStreamEntry] = []
    archive_identity: list[dict[str, object]] = []
    used_root_names: set[str] = set()

    def record_file_identity(entry_name: str, file: File) -> None:
        """Track metadata that must stay stable across byte-range resume requests."""

        archive_identity.append({
            "kind": "file",
            "name": entry_name,
            "id": file.id,
            "size": max(0, int(file.size or 0)),
            "modified_at": file.modified_at.isoformat(),
            "data_center": file.data_center,
            "links": file.links,
        })

    def unique_root_name(value: str) -> str:
        base = str(value or "").strip().strip(". ") or "folder"
        candidate = base
        suffix = 1
        while candidate.casefold() in used_root_names:
            candidate = f"{base} ({suffix})"
            suffix += 1
        used_root_names.add(candidate.casefold())
        return candidate

    for folder in folder_roots:
        root_path = Path(folder.path)
        root_name = unique_root_name(root_path.name)
        entries.append(ZipStreamEntry(name=f"{root_name}/", source=None, size=0))
        archive_identity.append({
            "kind": "directory",
            "id": folder.id,
            "path": str(root_path),
            "modified_at": folder.modified_at.isoformat(),
            "entry_name": f"{root_name}/",
        })

        descendant_directories = sorted(
            (
                directory for directory in all_directories
                if directory.id != folder.id and directory.username == username
                and Path(directory.path).is_relative_to(root_path)
            ),
            key=lambda directory: (len(Path(directory.path).parts), str(directory.path).casefold()),
        )
        for directory in descendant_directories:
            relative = Path(directory.path).relative_to(root_path)
            if relative.parts:
                directory_entry_name = f"{root_name}/{relative.as_posix().rstrip('/')}/"
                entries.append(ZipStreamEntry(
                    name=directory_entry_name,
                    source=None,
                    size=0,
                ))
                archive_identity.append({
                    "kind": "directory",
                    "id": directory.id,
                    "path": str(directory.path),
                    "modified_at": directory.modified_at.isoformat(),
                    "entry_name": directory_entry_name,
                })

        for file in all_files:
            parent_path = directory_path_by_id.get(int(file.directory_id))
            if parent_path is None or file.username != username or not parent_path.is_relative_to(root_path):
                continue

            relative_parent = parent_path.relative_to(root_path)
            file_name = Path(str(file.name).replace("\\", "/")).name or "download"
            relative_parts = [root_name, *relative_parent.parts, file_name]
            entry_name = "/".join(relative_parts)
            record_file_identity(entry_name, file)
            if not file.links and int(file.size or 0) == 0:
                entries.append(ZipStreamEntry(name=entry_name, source=None, size=0))
            else:
                entries.append(ZipStreamEntry(
                    name=entry_name,
                    source=stream_file(file),
                    size=max(0, int(file.size or 0)),
                ))

    for file in selected_files:
        parent_path = directory_path_by_id.get(int(file.directory_id))
        if parent_path is not None and any(parent_path == root or parent_path.is_relative_to(root) for root in folder_root_paths):
            continue

        # Explicit file selections live at the archive root. Stored file names are
        # leaf names; the ZIP writer additionally rejects unsafe relative paths.
        file_name = Path(str(file.name).replace("\\", "/")).name or "download"
        record_file_identity(file_name, file)
        if not file.links and int(file.size or 0) == 0:
            entries.append(ZipStreamEntry(name=file_name, source=None, size=0))
        else:
            entries.append(ZipStreamEntry(
                name=file_name,
                source=stream_file(file),
                size=max(0, int(file.size or 0)),
            ))

    if not entries:
        raise HTTPException(
            status_code=422,
            detail="The selected items do not contain any active files or folders",
            headers=_public_cors_headers(request),
        )
    if len(entries) > 10000:
        raise HTTPException(
            status_code=413,
            detail="This selection contains too many ZIP entries (maximum 10,000)",
            headers=_public_cors_headers(request),
        )

    archive_length = zip64_stored_content_length(entries)
    archive_etag = '"' + sha256(
        json.dumps(archive_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest() + '"'

    range_header = request.headers.get("range")
    if_range = request.headers.get("if-range")
    if range_header and if_range and if_range.strip() != archive_etag:
        # If the selected archive changed while paused, ignore Range and let the client
        # safely truncate and replace the old partial archive from byte zero.
        range_header = None

    range_start = 0
    range_end = archive_length - 1
    partial = False
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or "," in range_header:
            return Response(status_code=416, headers={
                "Content-Range": f"bytes */{archive_length}",
                "Accept-Ranges": "bytes",
                "ETag": archive_etag,
                "Content-Length": "0",
                **_public_cors_headers(request),
            })

        first, last = match.groups()
        if first:
            range_start = int(first)
            range_end = int(last) if last else archive_length - 1
        else:
            suffix_length = int(last or 0)
            if suffix_length <= 0:
                return Response(status_code=416, headers={
                    "Content-Range": f"bytes */{archive_length}",
                    "Accept-Ranges": "bytes",
                    "ETag": archive_etag,
                    "Content-Length": "0",
                    **_public_cors_headers(request),
                })
            range_start = max(0, archive_length - suffix_length)
            range_end = archive_length - 1

        if range_start >= archive_length or range_end < range_start:
            return Response(status_code=416, headers={
                "Content-Range": f"bytes */{archive_length}",
                "Accept-Ranges": "bytes",
                "ETag": archive_etag,
                "Content-Length": "0",
                **_public_cors_headers(request),
            })

        range_end = min(range_end, archive_length - 1)
        partial = True

    archive_filename = _safe_zip_download_filename(filename)
    response_length = range_end - range_start + 1
    headers = {
        "Content-Disposition": _content_disposition(archive_filename, attachment=True),
        "Cache-Control": "private, no-store, no-cache, must-revalidate, no-transform",
        "Accept-Ranges": "bytes",
        "ETag": archive_etag,
        "X-Content-Type-Options": "nosniff",
        "X-Accel-Buffering": "no",
        "Referrer-Policy": "no-referrer",
        "Content-Length": str(response_length),
        **_public_cors_headers(request),
    }
    if partial:
        headers["Content-Range"] = f"bytes {range_start}-{range_end}/{archive_length}"

    async def stream_zip_range():
        """Replay the deterministic archive and emit only the requested byte range."""

        position = 0
        async for chunk in stream_zip64(entries):
            chunk_end = position + len(chunk)
            if chunk_end <= range_start:
                position = chunk_end
                continue

            local_start = max(0, range_start - position)
            local_end = min(len(chunk), range_end - position + 1)
            if local_start < local_end:
                yield chunk[local_start:local_end]

            position = chunk_end
            if position > range_end:
                return

    body = stream_zip_range() if partial else stream_zip64(entries)
    return StreamingResponse(
        body,
        status_code=206 if partial else 200,
        headers=headers,
        media_type="application/zip",
    )
