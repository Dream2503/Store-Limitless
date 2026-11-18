"""Public readiness, upload status, and public file-stream endpoints."""

from __future__ import annotations

import asyncio
import json
import math
import platform
import re
import shutil
import unicodedata
from hashlib import sha256
from pathlib import Path
from time import monotonic
from mimetypes import guess_type
from typing import AsyncGenerator, cast, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from backend.database.models import File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import get_current_user
from backend.server.security import verify_public_stream_token, verify_public_zip_token
from backend.server.zip_stream import ZipStreamEntry, stream_zip64, zip64_stored_content_length
from core.stream import Stream
from core.utils.binaries import get_ffmpeg_path
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


# Track-aware playback is generated from the existing stored byte-range stream.
# Segment payloads are returned from FFmpeg stdout; no HLS or media files are written to disk.
_MEDIA_INFO_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_MEDIA_INFO_TTL: float = 300.0
_MEDIA_SEGMENT_SECONDS: int = 6
_MEDIA_MAX_SEGMENTS: int = 50000
_MEDIA_MAX_OUTPUT_BYTES: int = 128 * 1024 * 1024
_MEDIA_PROCESS_LIMIT = asyncio.Semaphore(3)
_BITMAP_SUBTITLE_CODECS = {
    "dvd_subtitle",
    "dvb_subtitle",
    "hdmv_pgs_subtitle",
    "xsub",
    "dvb_teletext",
    "arib_caption",
    "eia_608",
    "eia_708",
}


def _media_tool_paths() -> tuple[str, str]:
    """Ensure FFmpeg and FFprobe are installed, then return both executable paths."""

    try:
        ffmpeg_path: Path = get_ffmpeg_path()
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    # Prefer the FFprobe bundled beside FFmpeg so both tools come from the same installation.
    probe_name = "ffprobe.exe" if platform.system() == "Windows" else "ffprobe"
    adjacent_probe = ffmpeg_path.with_name(probe_name)
    ffprobe_executable: str | None = str(adjacent_probe) if adjacent_probe.is_file() else shutil.which("ffprobe")

    if ffprobe_executable is None:
        raise HTTPException(
            status_code=503,
            detail="FFprobe could not be found after installation. Install FFmpeg and FFprobe on the StoreLimitless server.",
        )

    return str(ffmpeg_path), ffprobe_executable

def _media_source_url(token: str) -> str:
    """Return the local byte-range endpoint used by FFmpeg to read the original stored chunks."""

    return f"http://127.0.0.1:8000/public/stream/{quote(token, safe='')}"


async def _run_media_tool(command: list[str], *, timeout: float, output_limit: int, request: Request | None = None) -> bytes | None:
    """Run one bounded FFmpeg utility command and collect its in-memory output."""

    async with _MEDIA_PROCESS_LIMIT:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise HTTPException(status_code=503, detail="The media processing tools could not be started.") from error

        communicate_task = asyncio.create_task(process.communicate())
        disconnect_task = None

        async def wait_for_disconnect() -> None:
            """Stop waiting when the HTTP client closes its connection."""

            if request is None:
                return

            while not await request.is_disconnected():
                await asyncio.sleep(0.2)

        if request is not None:
            disconnect_task = asyncio.create_task(wait_for_disconnect())

        try:
            waiting_tasks = {communicate_task}
            if disconnect_task is not None:
                waiting_tasks.add(disconnect_task)

            done, _ = await asyncio.wait(
                waiting_tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                if process.returncode is None:
                    process.kill()
                await asyncio.gather(communicate_task, return_exceptions=True)
                raise HTTPException(status_code=504, detail="Preparing this media track timed out. Try again.")

            # If the client has gone away, stop expensive probing/transcoding instead of
            # returning a large response to a socket that is already closed. Prefer the
            # disconnect result even if FFmpeg finished in the same event-loop tick.
            if disconnect_task is not None and disconnect_task in done:
                if process.returncode is None:
                    process.kill()
                await asyncio.gather(communicate_task, return_exceptions=True)
                return None

            stdout, stderr = communicate_task.result()

        except asyncio.CancelledError:
            # Also reap FFmpeg if the ASGI request itself is cancelled during shutdown.
            if process.returncode is None:
                process.kill()
            await asyncio.gather(communicate_task, return_exceptions=True)
            raise
        finally:
            if disconnect_task is not None and not disconnect_task.done():
                disconnect_task.cancel()
                await asyncio.gather(disconnect_task, return_exceptions=True)

        if process.returncode != 0:
            # Do not return raw FFmpeg stderr because it can contain internal URLs or storage details.
            raise HTTPException(
                status_code=502,
                detail="FFmpeg could not process this media stream or selected track.",
            )

        if len(stdout) > output_limit:
            raise HTTPException(
                status_code=413,
                detail="The generated media segment or subtitle track exceeded the safe response size.",
            )

        return stdout


def _media_track_label(stream: dict[str, Any], kind: str, ordinal: int) -> str:
    """Build a stable, readable label for an embedded track."""

    tags = stream.get("tags") or {}
    title = str(tags.get("title") or tags.get("handler_name") or "").strip()
    language = str(tags.get("language") or "").strip()
    raw_label = title or (f"{language.upper()} {kind.title()} {ordinal + 1}" if language and language.lower() != "und" else f"{kind.title()} {ordinal + 1}")
    return re.sub(r"[\x00-\x1f\x7f]", " ", raw_label).strip()[:120] or f"{kind.title()} {ordinal + 1}"


def _hls_attribute(value: str) -> str:
    """Escape user-controlled track labels for an HLS quoted attribute."""

    clean = re.sub(r"[\x00-\x1f\x7f]", " ", value).replace("\\", "_").replace('"', "'")
    return f'"{clean[:120]}"'


async def _public_media_info(file: File, token: str, request: Request | None = None) -> dict[str, Any] | None:
    """Probe the original stored media and cache only its small stream metadata."""

    cache_key = f"{file.username}:{file.id}:{_file_etag(file)}"
    cached = _MEDIA_INFO_CACHE.get(cache_key)
    now = monotonic()

    if cached is not None and cached[0] > now:
        return cached[1]

    _, ffprobe_path = _media_tool_paths()
    output = await _run_media_tool(
        [
            ffprobe_path,
            "-v", "error",
            "-probesize", "10000000",
            "-analyzeduration", "10000000",
            "-show_streams",
            "-show_format",
            "-of", "json",
            _media_source_url(token),
        ],
        timeout=90,
        output_limit=8 * 1024 * 1024,
        request=request,
    )
    if output is None:
        return None

    try:
        probe = json.loads(output)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=502, detail="Could not read the media track metadata.") from error

    raw_streams = probe.get("streams") or []
    video_streams = [
        stream for stream in raw_streams
        if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic")
    ]
    if not video_streams:
        raise HTTPException(status_code=415, detail="This file does not contain a playable video track.")

    format_info = probe.get("format") or {}
    try:
        duration = float(format_info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0

    if not math.isfinite(duration) or duration <= 0:
        raise HTTPException(status_code=415, detail="This video does not expose a usable duration for track-aware playback.")

    def describe(stream: dict[str, Any], kind: str, ordinal: int) -> dict[str, Any]:
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        language = str(tags.get("language") or "").strip()
        codec = str(stream.get("codec_name") or "unknown").lower()
        return {
            "index": int(stream.get("index", -1)),
            "codec": codec,
            "label": _media_track_label(stream, kind, ordinal),
            "language": language if language.lower() != "und" else "",
            "default": bool(disposition.get("default")),
            "channels": stream.get("channels"),
            "width": stream.get("width"),
            "height": stream.get("height"),
        }

    audio_streams = [stream for stream in raw_streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in raw_streams if stream.get("codec_type") == "subtitle"]
    media_info: dict[str, Any] = {
        "duration": duration,
        "video": describe(video_streams[0], "video", 0),
        "audio": [describe(stream, "audio", ordinal) for ordinal, stream in enumerate(audio_streams)],
        "subtitles": [
            {
                **describe(stream, "subtitle", ordinal),
                "selectable": str(stream.get("codec_name") or "").lower() not in _BITMAP_SUBTITLE_CODECS,
            }
            for ordinal, stream in enumerate(subtitle_streams)
        ],
    }

    _MEDIA_INFO_CACHE[cache_key] = (monotonic() + _MEDIA_INFO_TTL, media_info)
    if len(_MEDIA_INFO_CACHE) > 256:
        for key, (expires_at, _) in list(_MEDIA_INFO_CACHE.items()):
            if expires_at <= monotonic():
                _MEDIA_INFO_CACHE.pop(key, None)
        while len(_MEDIA_INFO_CACHE) > 256:
            _MEDIA_INFO_CACHE.pop(next(iter(_MEDIA_INFO_CACHE)))

    return media_info


def _hls_media_playlist(token: str, media_info: dict[str, Any], *, kind: str, stream_index: int | None = None) -> str:
    """Describe fixed-duration on-demand segments; each request is generated in memory."""

    duration = float(media_info["duration"])
    segment_count = math.ceil(duration / _MEDIA_SEGMENT_SECONDS)
    if segment_count <= 0 or segment_count > _MEDIA_MAX_SEGMENTS:
        raise HTTPException(status_code=413, detail="This video's duration is too long for track-aware playback.")

    base = f"/public/hls/{quote(token, safe='')}"
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{_MEDIA_SEGMENT_SECONDS}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]

    for segment_index in range(segment_count):
        if segment_index > 0:
            # Each segment is produced by a short-lived FFmpeg process, so declare the
            # timestamp/transport-stream counter reset explicitly to the HLS client.
            lines.append("#EXT-X-DISCONTINUITY")

        segment_duration = min(_MEDIA_SEGMENT_SECONDS, duration - segment_index * _MEDIA_SEGMENT_SECONDS)
        if kind == "audio":
            url = f"{base}/segment/audio/{int(stream_index or 0)}/{segment_index}"
        else:
            video_index = int(media_info["video"]["index"])
            url = f"{base}/segment/video/{video_index}/{segment_index}"
        lines.extend([f"#EXTINF:{segment_duration:.3f},", url])

    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def _media_response_headers(request: Request) -> dict[str, str]:
    """Return headers for dynamically generated media, playlists, and subtitle responses."""

    return {
        "Cache-Control": "private, no-store, no-cache, must-revalidate, no-transform",
        "X-Content-Type-Options": "nosniff",
        "X-Accel-Buffering": "no",
        "Referrer-Policy": "no-referrer",
        **_public_cors_headers(request),
    }


@router.get("/tracks/{token}")
async def public_media_tracks(token: str, request: Request) -> JSONResponse:
    """List selectable audio and text subtitle tracks embedded in the original file."""

    file = await _owned_public_file(token, request)
    media_info = await _public_media_info(file, token, request)
    if media_info is None:
        return Response(status_code=204, headers=_media_response_headers(request))
    return JSONResponse(
        {**media_info, "segment_duration": _MEDIA_SEGMENT_SECONDS},
        headers=_media_response_headers(request),
    )


@router.get("/hls/{token}/master.m3u8")
async def public_hls_master(token: str, request: Request) -> Response:
    """Create an HLS master playlist without writing a playlist or media segment to disk."""

    file = await _owned_public_file(token, request)
    media_info = await _public_media_info(file, token, request)
    if media_info is None:
        return Response(status_code=204, headers=_media_response_headers(request))
    if not media_info.get("video"):
        raise HTTPException(status_code=415, detail="This file has no video track.")

    base = f"/public/hls/{quote(token, safe='')}"
    audio_tracks = media_info["audio"]
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]

    if audio_tracks:
        default_index = next((index for index, track in enumerate(audio_tracks) if track["default"]), 0)
        for index, track in enumerate(audio_tracks):
            audio_playlist_url = f"{base}/audio/{track['index']}.m3u8"
            attributes = [
                'TYPE=AUDIO',
                'GROUP-ID="audio"',
                f"NAME={_hls_attribute(track['label'])}",
                f"DEFAULT={'YES' if index == default_index else 'NO'}",
                f"AUTOSELECT={'YES' if index == default_index else 'NO'}",
                f"URI={_hls_attribute(audio_playlist_url)}",
            ]
            if track["language"] and re.fullmatch(r"[A-Za-z0-9-]{1,35}", track["language"]):
                attributes.insert(3, f"LANGUAGE={_hls_attribute(track['language'])}")
            lines.append("#EXT-X-MEDIA:" + ",".join(attributes))
        lines.extend(['#EXT-X-STREAM-INF:BANDWIDTH=8000000,AUDIO="audio"', f"{base}/video.m3u8"])
    else:
        lines.extend(["#EXT-X-STREAM-INF:BANDWIDTH=8000000", f"{base}/video.m3u8"])

    return Response(
        content="\n".join(lines) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers=_media_response_headers(request),
    )


@router.get("/hls/{token}/video.m3u8")
async def public_hls_video_playlist(token: str, request: Request) -> Response:
    """Create the video-only HLS playlist for dynamic, in-memory segments."""

    file = await _owned_public_file(token, request)
    media_info = await _public_media_info(file, token, request)
    if media_info is None:
        return Response(status_code=204, headers=_media_response_headers(request))
    return Response(
        content=_hls_media_playlist(token, media_info, kind="video"),
        media_type="application/vnd.apple.mpegurl",
        headers=_media_response_headers(request),
    )


@router.get("/hls/{token}/audio/{track}.m3u8")
async def public_hls_audio_playlist(
    token: str,
    track: int,
    request: Request,
) -> Response:
    """Create a separate audio rendition playlist for the selected embedded stream."""

    file = await _owned_public_file(token, request)
    media_info = await _public_media_info(file, token, request)
    if media_info is None:
        return Response(status_code=204, headers=_media_response_headers(request))
    if not any(int(item["index"]) == track for item in media_info["audio"]):
        raise HTTPException(status_code=404, detail="Audio track not found.")

    return Response(
        content=_hls_media_playlist(token, media_info, kind="audio", stream_index=track),
        media_type="application/vnd.apple.mpegurl",
        headers=_media_response_headers(request),
    )


@router.get("/hls/{token}/audio.m3u8")
async def public_hls_audio_playlist_legacy(
    token: str,
    request: Request,
    track: int | None = Query(default=None),
    tr: str | None = Query(default=None),
) -> Response:
    """Support older HLS manifests that pass the selected audio track in the query string."""

    file = await _owned_public_file(token, request)
    media_info = await _public_media_info(file, token, request)
    if media_info is None:
        return Response(status_code=204, headers=_media_response_headers(request))

    audio_tracks = media_info["audio"]
    if not audio_tracks:
        raise HTTPException(status_code=404, detail="Audio track not found.")

    # Older manifests were generated with either ?track=<index> or the shorter ?tr
    # parameter. A bare ?tr has no index, so use the marked default track or first track.
    selected_index = track
    if selected_index is None and tr:
        try:
            selected_index = int(tr)
        except ValueError:
            selected_index = None

    selected_track = None
    if selected_index is not None:
        selected_track = next(
            (item for item in audio_tracks if int(item["index"]) == selected_index),
            None,
        )
        if selected_track is None and 0 <= selected_index < len(audio_tracks):
            selected_track = audio_tracks[selected_index]

    if selected_track is None:
        selected_track = next(
            (item for item in audio_tracks if item["default"]),
            audio_tracks[0],
        )

    return Response(
        content=_hls_media_playlist(
            token,
            media_info,
            kind="audio",
            stream_index=int(selected_track["index"]),
        ),
        media_type="application/vnd.apple.mpegurl",
        headers=_media_response_headers(request),
    )


@router.get("/hls/{token}/segment/{kind}/{track}/{index}")
async def public_hls_segment(
    token: str,
    kind: str,
    track: int,
    index: int,
    request: Request,
) -> Response:
    """Generate one MPEG-TS transport segment on demand and return it directly from memory."""

    if kind not in {"video", "audio", "subtitle"}:
        raise HTTPException(status_code=404, detail="Media segment not found.")

    file = await _owned_public_file(token, request)
    media_info = await _public_media_info(file, token, request)
    if media_info is None:
        return Response(status_code=204, headers=_media_response_headers(request))
    duration = float(media_info["duration"])
    segment_count = math.ceil(duration / _MEDIA_SEGMENT_SECONDS)
    if index < 0 or index >= segment_count:
        raise HTTPException(status_code=404, detail="Media segment not found.")

    if kind == "video":
        stream_index = int(media_info["video"]["index"])
        if track != stream_index:
            raise HTTPException(status_code=404, detail="Video track not found.")
    elif kind == "audio":
        audio_track = next((item for item in media_info["audio"] if int(item["index"]) == track), None)
        if audio_track is None:
            raise HTTPException(status_code=404, detail="Audio track not found.")
        stream_index = int(audio_track["index"])
    else:
        subtitle_track = next((item for item in media_info["subtitles"] if int(item["index"]) == track), None)
        if subtitle_track is None:
            raise HTTPException(status_code=404, detail="Subtitle track not found.")
        if not subtitle_track["selectable"]:
            raise HTTPException(status_code=415, detail="This bitmap subtitle format cannot be converted to selectable text captions.")
        stream_index = int(subtitle_track["index"])

    start = index * _MEDIA_SEGMENT_SECONDS
    segment_duration = min(_MEDIA_SEGMENT_SECONDS, duration - start)
    if kind == "subtitle":
        return await public_hls_subtitles(
            token,
            stream_index,
            request,
            start_seconds=start,
            duration_seconds=segment_duration,
        )

    ffmpeg_path, _ = _media_tool_paths()
    command = [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error", "-nostdin",
        "-rw_timeout", "300000000",
        "-ss", f"{start:.6f}",
        "-i", _media_source_url(token),
        "-t", f"{segment_duration:.6f}",
        "-map", f"0:{stream_index}",
        "-sn", "-dn",
    ]

    if kind == "video":
        # A standalone encode ensures every generated segment starts on a keyframe,
        # even when the source's keyframe positions do not line up with 6-second boundaries.
        command.extend([
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-g", "48",
            "-keyint_min", "48",
            "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(t,n_forced*{_MEDIA_SEGMENT_SECONDS})",
            "-threads", "2",
        ])
    else:
        command.extend([
            "-vn",
            "-c:a", "aac",
            "-b:a", "160k",
            "-ac", "2",
            "-threads", "2",
        ])

    command.extend([
        "-output_ts_offset", f"{start:.6f}",
        "-mpegts_flags", "+resend_headers",
        "-f", "mpegts",
        "pipe:1",
    ])

    segment = await _run_media_tool(
        command,
        timeout=max(120.0, segment_duration * 30.0),
        output_limit=_MEDIA_MAX_OUTPUT_BYTES,
        request=request,
    )
    if segment is None:
        return Response(status_code=204, headers=_media_response_headers(request))
    if not segment:
        raise HTTPException(status_code=502, detail="FFmpeg produced an empty media segment.")

    return Response(content=segment, media_type="video/mp2t", headers=_media_response_headers(request))


@router.get("/hls/{token}/subtitles/{stream_index}.srt")
async def public_hls_subtitles(
    token: str,
    stream_index: int,
    request: Request,
    start_seconds: float = Query(default=0, ge=0),
    duration_seconds: float = Query(default=_MEDIA_SEGMENT_SECONDS, gt=0, le=120),
) -> Response:
    """Stream one extracted WebVTT track as FFmpeg produces cues; no sidecar file is stored."""

    file = await _owned_public_file(token, request)
    media_info = await _public_media_info(file, token, request)
    if media_info is None:
        return Response(status_code=204, headers=_media_response_headers(request))
    subtitle = next(
        (item for item in media_info["subtitles"] if int(item["index"]) == stream_index),
        None,
    )
    if subtitle is None:
        raise HTTPException(status_code=404, detail="Subtitle track not found.")
    if not subtitle["selectable"]:
        raise HTTPException(status_code=415, detail="This bitmap subtitle format cannot be converted to selectable text captions.")

    media_duration = float(media_info["duration"])
    if start_seconds >= media_duration:
        return Response(
            content="WEBVTT\n\n",
            media_type="text/vtt; charset=utf-8",
            headers=_media_response_headers(request),
        )

    segment_duration = min(duration_seconds, media_duration - start_seconds)
    ffmpeg_path, _ = _media_tool_paths()
    command = [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error", "-nostdin",
        # Keep subtitle packet timestamps aligned to the original media timeline
        # while seeking to a short, bounded window. -start_at_zero removes any container
        # start-time offset but retains the requested seek position.
        "-copyts", "-start_at_zero",
        "-rw_timeout", "300000000",
        # Input-side seek speeds up demuxing. The output-side seek below is also
        # required: FFmpeg's input -ss alone does not reliably discard earlier
        # subtitle packets, which shifts cues into the wrong playback window.
        "-ss", f"{start_seconds:.6f}",
        "-i", _media_source_url(token),
        "-ss", f"{start_seconds:.6f}",
        "-t", f"{segment_duration:.6f}",
        "-map", f"0:{stream_index}",
        "-vn", "-an", "-dn",
        "-c:s", "webvtt",
        "-flush_packets", "1",
        "-f", "webvtt",
        "pipe:1",
    ]

    await _MEDIA_PROCESS_LIMIT.acquire()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        _MEDIA_PROCESS_LIMIT.release()
        raise HTTPException(
            status_code=503,
            detail="The subtitle processor could not be started. Check the FFmpeg installation.",
            headers=_public_cors_headers(request),
        ) from error

    if process.stdout is None or process.stderr is None:
        if process.returncode is None:
            process.kill()
        await process.wait()
        _MEDIA_PROCESS_LIMIT.release()
        raise HTTPException(status_code=503, detail="The subtitle processor did not expose its output stream.")

    started_at = monotonic()
    output_limit = 32 * 1024 * 1024

    async def stream_subtitles() -> AsyncGenerator[bytes, None]:
        """Forward WebVTT bytes promptly and stop FFmpeg on timeout or disconnect."""

        stderr_task = asyncio.create_task(process.stderr.read())
        total_bytes = 0
        try:
            while True:
                if await request.is_disconnected():
                    return

                remaining = 300.0 - (monotonic() - started_at)
                if remaining <= 0:
                    raise TimeoutError("Subtitle extraction timed out after 300 seconds.")

                try:
                    chunk = await asyncio.wait_for(process.stdout.read(64 * 1024), timeout=remaining)
                except asyncio.TimeoutError as error:
                    raise TimeoutError("Subtitle extraction timed out after 300 seconds.") from error

                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > output_limit:
                    raise RuntimeError("Subtitle output exceeded the 32 MiB safety limit.")

                yield chunk

            remaining = max(1.0, 300.0 - (monotonic() - started_at))
            return_code = await asyncio.wait_for(process.wait(), timeout=remaining)
            await asyncio.wait_for(stderr_task, timeout=5.0)
            if return_code != 0:
                raise RuntimeError("FFmpeg could not finish extracting the selected subtitle track.")
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.gather(process.wait(), return_exceptions=True)
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            _MEDIA_PROCESS_LIMIT.release()

    return StreamingResponse(
        stream_subtitles(),
        media_type="text/vtt; charset=utf-8",
        headers=_media_response_headers(request),
    )

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
    """Stream a multi-file ZIP directly from remote storage with no server-side archive staging."""

    try:
        payload = verify_public_zip_token(token)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Invalid or expired ZIP download link", headers=_public_cors_headers(request)) from error

    username = cast(str, payload["username"])
    file_ids = cast(list[int], payload["file_ids"])
    files: list[File] = []
    for file_id in file_ids:
        file = await File.get(username, fid=file_id)
        if file is None or file.username != username:
            raise HTTPException(status_code=404, detail="One or more selected files are no longer available", headers=_public_cors_headers(request))
        files.append(file)

    # Start the HTTP response before contacting remote storage. Stream.prepare() verifies
    # the final storage chunk, so doing it for every selected file here makes the browser
    # wait for all those remote requests before its download even begins. Prepare each file
    # lazily as the ZIP writer reaches it, and forward source bytes as soon as available.
    async def stream_file(file: File):
        try:
            source = await Stream.prepare(file)
            async for chunk in source.stream():
                yield chunk
        except (OSError, ValueError) as error:
            # The ZIP has already started by this point; a storage failure ends the stream.
            raise OSError(f'Cannot read "{file.name}": {error}') from error

    entries: list[ZipStreamEntry] = []
    for file in files:
        if not file.links and int(file.size or 0) == 0:
            entries.append(ZipStreamEntry(name=file.name, source=None, size=0))
        else:
            entries.append(ZipStreamEntry(name=file.name, source=stream_file(file), size=max(0, int(file.size or 0))))

    archive_filename = _safe_zip_download_filename(filename)
    headers = {
        "Content-Disposition": _content_disposition(archive_filename, attachment=True),
        "Cache-Control": "private, no-store, no-cache, must-revalidate, no-transform",
        "X-Content-Type-Options": "nosniff",
        "X-Accel-Buffering": "no",
        "Referrer-Policy": "no-referrer",
        # STORE-mode ZIP output has a predictable exact size, so browser download
        # managers can show the total without buffering or pre-compressing the files.
        "Content-Length": str(zip64_stored_content_length(entries)),
        **_public_cors_headers(request),
    }
    return StreamingResponse(stream_zip64(entries), status_code=200, headers=headers, media_type="application/zip")
