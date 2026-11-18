"""Persistent post-upload video processing and prebuilt HLS asset storage."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import tempfile
from pathlib import Path
from time import monotonic
from typing import Any

import aiofiles
from psycopg.types.json import Jsonb

from backend.database.connection import POOL
from backend.database.models.file import File, is_video_file
from core.data_center import DataCenter
from core.stream import Stream
from core.utils.binaries import get_ffmpeg_path
from core.utils.progress import Progress, upload_growing_file

VIDEO_SEGMENT_SECONDS = 6
_VIDEO_BITMAP_SUBTITLE_CODECS = {
    "dvd_subtitle",
    "dvb_subtitle",
    "hdmv_pgs_subtitle",
    "xsub",
    "dvb_teletext",
    "arib_caption",
    "eia_608",
    "eia_708",
}
_JOB_POLL_SECONDS = 1.0
_MAX_PROCESSING_PROGRESS = 99.0


def get_processed_video(file_id: int) -> dict[str, Any] | None:
    """Return the durable streaming derivative metadata for one original file."""
    with POOL.connection() as connection:
        row = connection.execute(
            """
            SELECT id, data_center, pack_size, pack_links, media_info, assets, processed_at
            FROM processed_videos
            WHERE id = %s;
            """,
            (file_id,),
        ).fetchone()

    return dict(row) if row else None


def get_video_processing_status(file_id: int) -> dict[str, Any]:
    """Return the current processing job or an untracked state for legacy videos."""
    processed = get_processed_video(file_id)
    if processed is not None:
        media_info = processed.get("media_info") or {}
        duration = float(media_info.get("duration") or 0)
        return {
            "file_id": file_id,
            "status": "ready",
            "stage": "ready",
            "progress": 100.0,
            "message": "Streaming version is ready.",
            "processed_seconds": duration,
            "duration": duration,
            "completed_segments": sum(
                len(segments)
                for tracks in (processed.get("assets") or {}).values()
                if isinstance(tracks, dict)
                for segments in tracks.values()
                if isinstance(segments, list)
            ),
            "total_segments": sum(
                len(segments)
                for tracks in (processed.get("assets") or {}).values()
                if isinstance(tracks, dict)
                for segments in tracks.values()
                if isinstance(segments, list)
            ),
            "error": None,
        }

    with POOL.connection() as connection:
        row = connection.execute(
            """
            SELECT file_id, status, stage, progress, message, processed_seconds,
                   duration, bytes_downloaded, source_size, completed_segments,
                   total_segments, attempts, error, updated_at
            FROM video_processing_jobs
            WHERE file_id = %s;
            """,
            (file_id,),
        ).fetchone()

    if row is None:
        return {
            "file_id": file_id,
            "status": "untracked",
            "stage": "untracked",
            "progress": 0.0,
            "message": "This video has not entered the permanent processing pipeline yet.",
            "processed_seconds": 0.0,
            "duration": 0.0,
            "completed_segments": 0,
            "total_segments": 0,
            "bytes_downloaded": 0,
            "source_size": 0,
            "error": None,
        }

    result = dict(row)
    result["progress"] = max(0.0, min(100.0, float(result.get("progress") or 0)))
    result["processed_seconds"] = max(0.0, float(result.get("processed_seconds") or 0))
    result["duration"] = max(0.0, float(result.get("duration") or 0))
    result["completed_segments"] = max(0, int(result.get("completed_segments") or 0))
    result["total_segments"] = max(0, int(result.get("total_segments") or 0))
    result["bytes_downloaded"] = max(0, int(result.get("bytes_downloaded") or 0))
    result["source_size"] = max(0, int(result.get("source_size") or 0))
    result.pop("updated_at", None)
    return result


def retry_video_processing(file_id: int) -> bool:
    """Queue a failed job again without touching the original uploaded file."""
    with POOL.connection() as connection:
        try:
            processed = connection.execute(
                "SELECT 1 FROM processed_videos WHERE id = %s;",
                (file_id,),
            ).fetchone()
            if processed is not None:
                connection.commit()
                return False

            result = connection.execute(
                """
                UPDATE video_processing_jobs
                SET status = 'queued',
                    stage = 'queued',
                    progress = 0,
                    message = 'Waiting to retry video processing.',
                    processed_seconds = 0,
                    completed_segments = 0,
                    total_segments = 0,
                    error = NULL,
                    updated_at = NOW(),
                    started_at = NULL,
                    finished_at = NULL
                WHERE file_id = %s
                  AND status = 'failed'
                RETURNING file_id;
                """,
                (file_id,),
            ).fetchone()
            connection.commit()
            return result is not None
        except Exception:
            connection.rollback()
            raise


def _update_job(file_id: int, **updates: Any) -> None:
    """Persist an allow-listed progress snapshot for a background video job."""
    allowed = {
        "status", "stage", "progress", "message", "processed_seconds", "duration",
        "bytes_downloaded", "source_size", "completed_segments", "total_segments",
        "error", "started_at", "finished_at",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return

    set_finished_now = values.pop("finished_at", None) == "NOW()"
    assignments = [f"{key} = %s" for key in values]
    if set_finished_now:
        assignments.append("finished_at = NOW()")
    assignments.append("updated_at = NOW()")
    with POOL.connection() as connection:
        connection.execute(
            f"UPDATE video_processing_jobs SET {', '.join(assignments)} WHERE file_id = %s;",
            (*values.values(), file_id),
        )
        connection.commit()


def _requeue_stale_jobs() -> None:
    """Recover jobs whose worker stopped updating its lease during a crash or restart."""
    with POOL.connection() as connection:
        connection.execute(
            """
            UPDATE video_processing_jobs
            SET status = 'queued',
                stage = 'queued',
                message = 'Resuming after an interrupted processing attempt.',
                updated_at = NOW()
            WHERE status = 'processing'
              AND updated_at < NOW() - INTERVAL '10 minutes';
            """
        )
        connection.commit()


def _recover_interrupted_jobs() -> None:
    """Return jobs left in processing by a stopped API process to the durable queue."""
    with POOL.connection() as connection:
        connection.execute(
            """
            UPDATE video_processing_jobs
            SET status = 'queued',
                stage = 'queued',
                message = 'Resuming after server restart.',
                updated_at = NOW()
            WHERE status = 'processing';
            """
        )
        connection.commit()


def _claim_next_job() -> int | None:
    """Atomically claim the next queued job without blocking other workers."""
    with POOL.connection() as connection:
        try:
            row = connection.execute(
                """
                SELECT file_id
                FROM video_processing_jobs
                WHERE status = 'queued'
                ORDER BY created_at, file_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1;
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            file_id = int(row["file_id"])
            connection.execute(
                """
                UPDATE video_processing_jobs
                SET status = 'processing',
                    stage = 'downloading_source',
                    progress = 0,
                    message = 'Downloading the original from storage for processing.',
                    attempts = attempts + 1,
                    started_at = NOW(),
                    finished_at = NULL,
                    error = NULL,
                    updated_at = NOW()
                WHERE file_id = %s;
                """,
                (file_id,),
            )
            connection.commit()
            return file_id
        except Exception:
            connection.rollback()
            raise


def _load_original(file_id: int) -> File | None:
    """Read original-file metadata from PostgreSQL rather than a possibly stale cache."""
    with POOL.connection() as connection:
        row = connection.execute(
            """
            SELECT id, directory_id, name, type, size, modified_at, deleted_at,
                   data_center, links, username
            FROM files
            WHERE id = %s;
            """,
            (file_id,),
        ).fetchone()
    return File.model_validate(dict(row)) if row else None


def _media_tool_paths() -> tuple[str, str]:
    """Resolve FFmpeg and FFprobe from the same installed or bundled directory."""
    ffmpeg_path = get_ffmpeg_path()
    probe_name = "ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"
    ffprobe_path = ffmpeg_path.with_name(probe_name)
    if not ffprobe_path.is_file():
        from shutil import which

        located = which("ffprobe")
        if located is None:
            raise RuntimeError("FFprobe could not be found. Install FFmpeg and FFprobe on the server.")
        return str(ffmpeg_path), located
    return str(ffmpeg_path), str(ffprobe_path)


async def _probe_media(source_path: Path, ffprobe_path: str) -> dict[str, Any]:
    """Inspect the downloaded original and return FFprobe's stream and format metadata."""
    process = await asyncio.create_subprocess_exec(
        ffprobe_path,
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(source_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("FFprobe timed out while inspecting the original video.")

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-1500:]
        raise RuntimeError(detail or "FFprobe could not inspect the original video.")

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("FFprobe returned invalid media metadata.") from error

    raw_streams = payload.get("streams") or []
    video_streams = [
        stream for stream in raw_streams
        if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic")
    ]
    if not video_streams:
        raise RuntimeError("The uploaded file does not contain a playable video track.")

    try:
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration) or duration <= 0:
        durations = []
        for stream in raw_streams:
            try:
                value = float(stream.get("duration") or 0)
                if math.isfinite(value) and value > 0:
                    durations.append(value)
            except (TypeError, ValueError):
                continue
        duration = max(durations, default=0.0)
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("The video duration could not be determined.")

    audio_streams = [stream for stream in raw_streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in raw_streams if stream.get("codec_type") == "subtitle"]

    def describe(stream: dict[str, Any], kind: str, ordinal: int) -> dict[str, Any]:
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        language = str(tags.get("language") or "").strip()
        codec = str(stream.get("codec_name") or "unknown").lower()
        title = str(tags.get("title") or tags.get("handler_name") or "").strip()
        label = title or (
            f"{language.upper()} {kind.title()} {ordinal + 1}"
            if language and language.lower() != "und"
            else f"{kind.title()} {ordinal + 1}"
        )
        record: dict[str, Any] = {
            "index": int(stream.get("index", -1)),
            "codec": codec,
            "language": language,
            "label": label[:120],
            "default": bool(disposition.get("default")),
        }
        if kind == "subtitle":
            record["selectable"] = codec not in _VIDEO_BITMAP_SUBTITLE_CODECS
        return record

    return {
        "duration": duration,
        "video": describe(video_streams[0], "video", 0),
        "audio": [describe(stream, "audio", index) for index, stream in enumerate(audio_streams)],
        "subtitles": [describe(stream, "subtitle", index) for index, stream in enumerate(subtitle_streams)],
    }


def _parse_hls_segments(playlist_path: Path) -> list[tuple[Path, float]]:
    """Read an FFmpeg-generated VOD playlist and validate each referenced local segment."""
    segments: list[tuple[Path, float]] = []
    pending_duration: float | None = None
    root = playlist_path.parent.resolve()

    for line in playlist_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except (TypeError, ValueError):
                pending_duration = None
            continue
        if not line or line.startswith("#") or pending_duration is None:
            continue

        segment_path = (playlist_path.parent / line).resolve()
        if segment_path.parent != root or not segment_path.is_file():
            raise RuntimeError("FFmpeg generated an invalid or missing HLS segment.")
        duration = pending_duration
        if not math.isfinite(duration) or duration <= 0:
            duration = float(VIDEO_SEGMENT_SECONDS)
        segments.append((segment_path, duration))
        pending_duration = None

    if not segments:
        raise RuntimeError(f"FFmpeg did not generate any segments for {playlist_path.name}.")
    return segments


def _append_segment_to_pack(pack_path: Path, segment_path: Path) -> tuple[int, int]:
    """Append one complete media fragment and return its offset and exact byte size."""
    offset = pack_path.stat().st_size if pack_path.exists() else 0
    size = segment_path.stat().st_size
    with segment_path.open("rb") as source, pack_path.open("ab") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    if pack_path.stat().st_size != offset + size:
        raise OSError(f"Could not package media segment {segment_path.name}.")
    return offset, size


def _time_from_progress(value: str) -> float:
    """Parse FFmpeg's HH:MM:SS.microseconds progress timestamp."""
    try:
        hours, minutes, seconds = value.strip().split(":")
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return max(0.0, result) if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


async def _run_ffmpeg_track(
        command: list[str],
        *,
        duration: float,
        track_label: str,
        progress_base: float,
        progress_span: float,
        total_tracks: int,
        track_number: int,
        file_id: int,
) -> None:
    """Run one full-length HLS encode and publish FFmpeg's real media-time progress."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.create_task(process.stderr.read())
    latest_values: dict[str, str] = {}
    last_report = 0.0

    try:
        while True:
            raw_line = await process.stdout.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            latest_values[key] = value

            if key != "progress":
                continue

            media_seconds = min(duration, _time_from_progress(latest_values.get("out_time", "")))
            fraction = min(1.0, media_seconds / duration) if duration > 0 else 0.0
            overall = min(
                _MAX_PROCESSING_PROGRESS,
                progress_base + ((track_number - 1 + fraction) / max(1, total_tracks)) * progress_span,
            )
            now = monotonic()
            if now - last_report >= 0.4 or value == "end":
                _update_job(
                    file_id,
                    status="processing",
                    stage="processing",
                    progress=round(overall, 2),
                    message=f"Preparing {track_label} ({track_number}/{total_tracks}).",
                    processed_seconds=media_seconds,
                    duration=duration,
                )
                last_report = now
            latest_values.clear()

        return_code = await process.wait()
        stderr = await stderr_task
        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-2000:]
            raise RuntimeError(detail or f"FFmpeg failed while preparing {track_label.lower()}.")
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if not stderr_task.done():
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        raise


async def _download_original(file: File, destination: Path) -> None:
    """Stream the existing 10 MB storage chunks into a temporary local source."""
    if not file.links or file.size <= 0:
        raise RuntimeError("The original video has no valid stored chunks.")

    stream = await Stream.prepare(file)
    downloaded = 0
    last_report_bytes = 0
    last_report_time = monotonic()
    async with aiofiles.open(destination, "wb") as output:
        async for chunk in stream.stream():
            await output.write(chunk)
            downloaded += len(chunk)
            now = monotonic()
            if downloaded - last_report_bytes >= 4 * 1024 * 1024 or now - last_report_time >= 1.5:
                percent = min(15.0, 15.0 * downloaded / max(1, file.size))
                _update_job(
                    int(file.id),
                    stage="downloading_source",
                    progress=round(percent, 2),
                    message="Downloading the original video from storage.",
                    bytes_downloaded=downloaded,
                    source_size=file.size,
                )
                last_report_bytes = downloaded
                last_report_time = now
    if downloaded != file.size:
        raise RuntimeError(f"The downloaded original was incomplete ({downloaded} of {file.size} bytes).")
    _update_job(
        int(file.id),
        stage="probing",
        progress=15.0,
        message="Original downloaded. Inspecting video and audio tracks.",
        bytes_downloaded=downloaded,
        source_size=file.size,
    )


async def _process_video(file_id: int) -> None:
    """Build, package, upload, and register a streaming-compatible HLS derivative."""
    original = _load_original(file_id)
    if original is None or original.id is None:
        return
    if original.deleted_at is not None:
        _update_job(
            file_id,
            status="failed",
            stage="failed",
            message="This video is in the trash and was not processed.",
            error="The original file is deleted.",
            finished_at="NOW()",
        )
        return
    if not original.links or original.size <= 0:
        raise RuntimeError("The original file is missing storage links or has an invalid size.")
    if not is_video_file(original.name, original.type):
        _update_job(file_id, status="failed", stage="failed", message="This file is not a video.", error="Not a video file.")
        return

    ffmpeg_path, ffprobe_path = _media_tool_paths()
    data_center = DataCenter(original.data_center)
    if data_center is None:
        raise RuntimeError(f"Unsupported data center: {original.data_center}")

    with tempfile.TemporaryDirectory(prefix=f"storelimitless-video-{file_id}-") as temporary:
        root = Path(temporary)
        source_path = root / "original"
        output_root = root / "hls"
        output_root.mkdir(parents=True, exist_ok=True)

        await _download_original(original, source_path)
        media_info = await _probe_media(source_path, ffprobe_path)
        duration = float(media_info["duration"])
        audio_tracks = list(media_info.get("audio") or [])
        if math.ceil(duration / VIDEO_SEGMENT_SECONDS) > 50000:
            raise RuntimeError("This video's duration exceeds the supported HLS segment limit.")

        expected_segments_per_track = math.ceil(duration / VIDEO_SEGMENT_SECONDS)
        total_segments = expected_segments_per_track * (1 + len(audio_tracks))
        _update_job(
            file_id,
            stage="processing",
            progress=18.0,
            message="Preparing streamable video and audio segments.",
            duration=duration,
            processed_seconds=0.0,
            total_segments=total_segments,
            completed_segments=0,
        )

        track_specs: list[tuple[str, int, Path]] = []
        video_index = int(media_info["video"]["index"])
        video_directory = output_root / "video"
        video_directory.mkdir()
        track_specs.append(("video", video_index, video_directory))
        for track in audio_tracks:
            audio_index = int(track["index"])
            track_directory = output_root / f"audio-{audio_index}"
            track_directory.mkdir()
            track_specs.append(("audio", audio_index, track_directory))

        for track_number, (kind, stream_index, track_directory) in enumerate(track_specs, start=1):
            label = "video" if kind == "video" else f"audio track {track_number - 1}"
            segment_pattern = track_directory / "segment_%06d.ts"
            playlist_path = track_directory / "index.m3u8"
            command = [
                ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(source_path),
                "-map", f"0:{stream_index}",
                "-sn", "-dn",
            ]
            if kind == "video":
                command.extend([
                    "-an",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-tune", "zerolatency",
                    "-crf", "22",
                    "-pix_fmt", "yuv420p",
                    "-g", "120",
                    "-keyint_min", "120",
                    "-sc_threshold", "0",
                    "-force_key_frames", f"expr:gte(t,n_forced*{VIDEO_SEGMENT_SECONDS})",
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
                "-f", "hls",
                "-hls_time", str(VIDEO_SEGMENT_SECONDS),
                "-hls_playlist_type", "vod",
                "-hls_flags", "independent_segments",
                "-hls_segment_filename", str(segment_pattern),
                "-progress", "pipe:1",
                "-nostats",
                str(playlist_path),
            ])
            await _run_ffmpeg_track(
                command,
                duration=duration,
                track_label=label,
                progress_base=20.0,
                progress_span=55.0,
                total_tracks=len(track_specs),
                track_number=track_number,
                file_id=file_id,
            )
            segments = _parse_hls_segments(playlist_path)
            media_info.setdefault("_processed_assets", {}).setdefault(kind, {})[str(stream_index)] = []
            for segment_path, segment_duration in segments:
                media_info["_processed_assets"][kind][str(stream_index)].append({
                    "path": str(segment_path),
                    "duration": segment_duration,
                })
            _update_job(
                file_id,
                progress=round(min(75.0, 20.0 + 55.0 * track_number / max(1, len(track_specs))), 2),
                message=f"Prepared {track_number} of {len(track_specs)} media tracks.",
                completed_segments=min(total_segments, sum(
                    len(items)
                    for tracks in media_info["_processed_assets"].values()
                    for items in tracks.values()
                )),
            )

        pack_path = root / "streaming-pack.bin"
        asset_index: dict[str, dict[str, list[dict[str, Any]]]] = {"video": {}, "audio": {}}
        pack_offset = 0
        all_assets = media_info.pop("_processed_assets")
        current_asset = 0
        asset_count = sum(len(items) for tracks in all_assets.values() for items in tracks.values())

        for kind in ("video", "audio"):
            tracks = all_assets.get(kind, {})
            for stream_index_text in sorted(tracks, key=lambda value: int(value)):
                output_segments = tracks[stream_index_text]
                asset_index[kind][stream_index_text] = []
                for segment in output_segments:
                    path = Path(segment["path"])
                    offset, segment_size = await asyncio.to_thread(_append_segment_to_pack, pack_path, path)
                    asset_index[kind][stream_index_text].append({
                        "offset": offset,
                        "size": segment_size,
                        "duration": float(segment["duration"]),
                    })
                    pack_offset = offset + segment_size
                    current_asset += 1
                    packaging_progress = 75.0 + (5.0 * current_asset / max(1, asset_count))
                    _update_job(
                        file_id,
                        stage="packaging",
                        progress=round(packaging_progress, 2),
                        message=f"Packaging streamable segments ({current_asset}/{asset_count}).",
                        completed_segments=current_asset,
                        total_segments=asset_count,
                    )

        if pack_offset <= 0 or not pack_path.is_file():
            raise RuntimeError("No streamable media segments were generated.")

        _update_job(
            file_id,
            stage="storing",
            progress=80.0,
            message="Uploading the processed streaming copy to the data center.",
            completed_segments=asset_count,
            total_segments=asset_count,
        )
        packed_file = File(
            directory_id=int(original.directory_id),
            name=f"processed-video-{file_id}.bin",
            type="application/octet-stream",
            size=0,
            modified_at=original.modified_at,
            links=[],
            data_center=original.data_center,
            username=original.username,
        )
        upload_progress = Progress(packed_file.name, "Uploading processed streaming copy", pack_offset)
        async with aiofiles.open(pack_path, "rb") as verify_pack:
            if len(await verify_pack.read(1)) != 1:
                raise RuntimeError("The streaming package could not be read back from staging.")

        async for progress in upload_growing_file(packed_file, pack_path, data_center, upload_progress):
            transferred = min(pack_offset, max(0, int(progress.transfer or 0)))
            percentage = 80.0 + 18.0 * transferred / max(1, pack_offset)
            _update_job(
                file_id,
                stage="storing",
                progress=round(min(98.0, percentage), 2),
                message=f"Uploading processed stream ({transferred * 100 / max(1, pack_offset):.1f}%).",
            )

        expected_parts = math.ceil(pack_offset / int(data_center.MAX_SIZE))
        if packed_file.size != pack_offset or len(packed_file.links) != expected_parts:
            raise RuntimeError("The uploaded streaming package failed its size or chunk-index validation.")

        # Validate a provider reference before publishing the stream as ready.
        first_chunk = await data_center.download(packed_file.links[0])
        if len(first_chunk) != min(int(data_center.MAX_SIZE), pack_offset):
            raise RuntimeError("The processed stream could not be verified on the data center.")

        with POOL.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO processed_videos (id, data_center, pack_size, pack_links, media_info, assets, processed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        data_center = EXCLUDED.data_center,
                        pack_size = EXCLUDED.pack_size,
                        pack_links = EXCLUDED.pack_links,
                        media_info = EXCLUDED.media_info,
                        assets = EXCLUDED.assets,
                        processed_at = NOW();
                    """,
                    (
                        file_id,
                        original.data_center,
                        pack_offset,
                        packed_file.links,
                        Jsonb(media_info),
                        Jsonb(asset_index),
                    ),
                )
                connection.execute(
                    """
                    UPDATE video_processing_jobs
                    SET status = 'ready',
                        stage = 'ready',
                        progress = 100,
                        message = 'Streaming version is ready.',
                        processed_seconds = duration,
                        completed_segments = total_segments,
                        error = NULL,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE file_id = %s;
                    """,
                    (file_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


async def _process_job(file_id: int) -> None:
    """Run one queued job and record a useful failure without damaging the original."""
    try:
        await _process_video(file_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        detail = " ".join(str(error).split())[:1800] or type(error).__name__
        try:
            _update_job(
                file_id,
                status="failed",
                stage="failed",
                message="Video processing failed.",
                error=detail,
                finished_at="NOW()",
            )
        except Exception as status_error:
            print(f"Could not persist video processing failure for file {file_id}: {status_error}")
        print(f"Video processing failed for file {file_id}: {detail}")


async def run_video_processing_worker() -> None:
    """Poll the persistent queue and process one video at a time with bounded resource use."""
    _recover_interrupted_jobs()
    while True:
        try:
            _requeue_stale_jobs()
            file_id = _claim_next_job()
            if file_id is None:
                await asyncio.sleep(_JOB_POLL_SECONDS)
                continue
            await _process_job(file_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Video processing worker encountered an error: {error}")
            await asyncio.sleep(2)


async def read_processed_segment(
        original: File,
        processed: dict[str, Any],
        kind: str,
        stream_index: int,
        segment_index: int,
) -> bytes:
    """Read one prebuilt HLS segment by byte range from the packed remote derivative."""
    if kind not in {"video", "audio"}:
        raise ValueError("Only video and audio segments are stored in the processed package.")
    assets = processed.get("assets") or {}
    tracks = assets.get(kind) or {}
    segments = tracks.get(str(stream_index)) or []
    if segment_index < 0 or segment_index >= len(segments):
        raise IndexError("Processed media segment does not exist.")

    asset = segments[segment_index]
    offset = int(asset["offset"])
    size = int(asset["size"])
    pack_size = int(processed["pack_size"])
    if offset < 0 or size <= 0 or offset + size > pack_size:
        raise OSError("Processed media index contains an invalid byte range.")

    packed_file = File(
        id=-int(original.id or 1),
        directory_id=original.directory_id,
        name=f"processed-video-{original.id}.bin",
        type="application/octet-stream",
        size=pack_size,
        modified_at=original.modified_at,
        links=list(processed["pack_links"]),
        data_center=str(processed["data_center"]),
        username=original.username,
    )
    stream = await Stream.prepare(packed_file, f"bytes={offset}-{offset + size - 1}")
    result = bytearray()
    async for chunk in stream.stream():
        result.extend(chunk)
    if len(result) != size:
        raise OSError("Stored media segment was incomplete.")
    return bytes(result)
