"""Persistent post-upload video processing and prebuilt HLS asset storage."""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from dataclasses import dataclass
from time import monotonic, time
from typing import Any

import aiofiles
from psycopg.types.json import Jsonb

from backend.database.connection import POOL
from backend.database.redis_server import Redis
from backend.database.models.file import File, is_video_file
from core.config import TRANSFER_PATH
from core.data_center import DataCenter, Database
from core.stream import Stream
from core.utils import sanitize_sensitive_urls, write_log
from core.utils.binaries import get_ffmpeg_path
from core.utils.upload_manager import (
    IngestionUploadTicket,
    cancel_ingestion_upload,
    enqueue_ingestion_upload,
    ingestion_upload_slot,
)
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

_JOB_QUEUE = "video:processing:queue"
_JOB_SEQUENCE = "video:processing:sequence"
_JOB_INDEX = "video:processing:index"
_JOB_PREFIX = "video:processing:job:"
_JOB_REQUEUE_INTERVAL = 30.0

_ENQUEUE_VIDEO_JOB = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
local sequence = redis.call('INCR', KEYS[3])
redis.call('HSET', KEYS[1],
    'file_id', ARGV[1], 'status', ARGV[2], 'stage', ARGV[3],
    'progress', '0', 'message', ARGV[4], 'processed_seconds', '0',
    'duration', '0', 'bytes_downloaded', '0', 'source_size', ARGV[6],
    'completed_segments', '0', 'total_segments', '0', 'attempts', '0',
    'error', '', 'created_at', ARGV[5], 'updated_at', ARGV[5],
    'started_at', '', 'finished_at', '')
redis.call('SADD', KEYS[4], ARGV[1])
if ARGV[2] == 'queued' then redis.call('ZADD', KEYS[2], sequence, ARGV[1]) end
return 1
"""

_CLAIM_VIDEO_JOB = """
local head = redis.call('ZRANGE', KEYS[1], 0, 0)
while #head > 0 do
    local file_id = head[1]
    local job_key = ARGV[1] .. file_id
    if redis.call('EXISTS', job_key) == 0 then
        redis.call('ZREM', KEYS[1], file_id)
    elseif redis.call('HGET', job_key, 'status') ~= 'queued' then
        redis.call('ZREM', KEYS[1], file_id)
    else
        local attempts = tonumber(redis.call('HGET', job_key, 'attempts')) or 0
        redis.call('HSET', job_key, 'status', 'processing', 'stage', 'downloading_source',
            'progress', '0', 'message', 'Downloading the original from storage for processing.',
            'attempts', attempts + 1, 'started_at', ARGV[2], 'finished_at', '',
            'error', '', 'updated_at', ARGV[2])
        redis.call('ZREM', KEYS[1], file_id)
        return file_id
    end
    head = redis.call('ZRANGE', KEYS[1], 0, 0)
end
return ''
"""

_RETRY_VIDEO_JOB = """
if redis.call('HGET', KEYS[1], 'status') ~= 'failed' then return 0 end
local sequence = redis.call('INCR', KEYS[3])
redis.call('HSET', KEYS[1], 'status', 'queued', 'stage', 'queued', 'progress', '0',
    'message', 'Waiting to retry video processing.', 'processed_seconds', '0',
    'completed_segments', '0', 'total_segments', '0', 'error', '',
    'updated_at', ARGV[2], 'started_at', '', 'finished_at', '')
redis.call('SADD', KEYS[4], ARGV[1])
redis.call('ZADD', KEYS[2], sequence, ARGV[1])
return 1
"""

_REQUEUE_STALE_VIDEO_JOB = """
if redis.call('HGET', KEYS[1], 'status') ~= 'processing' then return 0 end
if redis.call('HGET', KEYS[1], 'updated_at') ~= ARGV[2] then return 0 end
local sequence = redis.call('INCR', KEYS[3])
redis.call('HSET', KEYS[1], 'status', 'queued', 'stage', 'queued', 'progress', '0',
    'message', 'Resuming after an interrupted processing attempt.',
    'processed_seconds', '0', 'completed_segments', '0', 'total_segments', '0',
    'error', '', 'updated_at', ARGV[3], 'started_at', '', 'finished_at', '')
redis.call('ZADD', KEYS[2], sequence, ARGV[1])
return 1
"""

_MIGRATE_LEGACY_VIDEO_JOB = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
local file_id = ARGV[1]
local status = ARGV[2]
for index = 3, #ARGV, 2 do
    redis.call('HSET', KEYS[1], ARGV[index], ARGV[index + 1])
end
redis.call('SADD', KEYS[4], file_id)
if status == 'queued' then
    local sequence = redis.call('INCR', KEYS[3])
    redis.call('ZADD', KEYS[2], sequence, file_id)
end
return 1
"""


def _video_job_key(file_id: int) -> str:
    """Return the Redis hash key for one video's processing state."""

    return f"{_JOB_PREFIX}{int(file_id)}"


async def enqueue_video_processing_job(
    file_id: int,
    *,
    status: str = "queued",
    message: str = "Waiting for video processing.",
    source_size: int = 0,
    username: str = "system",
    name: str = "",
) -> bool:
    """Create video processing state in Redis and enqueue it when remote processing is needed."""

    if status not in {"queued", "preparing"}:
        raise ValueError(f"Unsupported initial video processing status: {status!r}")
    now = str(time())
    created = await Redis.redis.eval(
        _ENQUEUE_VIDEO_JOB, 4, _video_job_key(file_id), _JOB_QUEUE, _JOB_SEQUENCE, _JOB_INDEX,
        str(file_id), status, status, message, now, max(0, int(source_size or 0)),
    )
    if created:
        write_log(
            "INFO", Database, "VIDEO PROCESSING", username,
            f"Registered Redis video job file_id={file_id} name={name!r} status={status}.",
        )
    return bool(created)


async def delete_video_processing_job(file_id: int) -> None:
    """Remove a video's processing state and pending queue entry from Redis."""

    await Redis.redis.zrem(_JOB_QUEUE, str(file_id))
    await Redis.redis.srem(_JOB_INDEX, str(file_id))
    await Redis.redis.delete(_video_job_key(file_id))



@dataclass(slots=True)
class LocalVideoPreparation:
    """Own the preserved local source and the completion barrier for an original upload."""

    file: File
    source_path: Path
    staging_root: Path
    original_saved: asyncio.Event
    task: asyncio.Task[None] | None = None
    stage: str = "probing"
    progress: float = 0.0
    message: str = "Preparing the local video source."
    processed_seconds: float = 0.0
    duration: float = 0.0
    bytes_downloaded: int = 0
    source_size: int = 0
    completed_segments: int = 0
    total_segments: int = 0


_LOCAL_VIDEO_PREPARATIONS: dict[int, LocalVideoPreparation] = {}


def has_local_video_preparation(file: File) -> bool:
    """Return whether this exact in-memory file object has an active local preprocessing task."""

    context = _LOCAL_VIDEO_PREPARATIONS.get(id(file))
    return context is not None and context.task is not None and not context.task.done()


def _preserve_video_source(source_path: Path, destination: Path) -> None:
    """Keep a stable local source even when the ingestion flow removes its own staging file."""

    if not source_path.is_file():
        raise FileNotFoundError(f"Video source is not available: {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source_path, destination)
    except OSError:
        # Some source directories may be mounted on another device. Copy only as a
        # fallback; the normal path uses a hard link and does not duplicate the bytes.
        shutil.copy2(source_path, destination)


async def start_local_video_preprocessing(file: File, source_path: Path) -> None:
    """Preserve the complete local source and begin FFmpeg work while original chunks upload."""

    if not is_video_file(file.name, file.type):
        return

    key = id(file)
    existing = _LOCAL_VIDEO_PREPARATIONS.get(key)
    if existing is not None and existing.task is not None and not existing.task.done():
        return

    staging_root = TRANSFER_PATH / "video-processing" / f"{key:x}-{os.urandom(6).hex()}"
    preserved_source = staging_root / "original"
    try:
        await asyncio.to_thread(_preserve_video_source, source_path, preserved_source)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    context = LocalVideoPreparation(file, preserved_source, staging_root, asyncio.Event())
    _LOCAL_VIDEO_PREPARATIONS[key] = context
    write_log(
        "INFO", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
        f"Local preparation started file_id={file.id} name={file.name!r} source={source_path.name!r}; "
        "FFmpeg can now run alongside the original upload.",
    )
    context.task = asyncio.create_task(
        _run_local_video_preparation(context),
        name=f"storelimitless-video-prep-{file.id or key}",
    )



async def notify_video_file_saved(file: File) -> None:
    """Publish preparation progress accumulated before the original file received its ID."""

    context = _LOCAL_VIDEO_PREPARATIONS.get(id(file))
    if context is None:
        return
    write_log(
        "INFO", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
        f"Original upload finalized file_id={file.id} name={file.name!r} size={file.size} "
        f"chunks={len(file.links)}; synchronizing locally accumulated progress.",
    )
    try:
        if file.id is not None:
            await _update_job(
                int(file.id), status="preparing", stage=context.stage, progress=context.progress,
                message=context.message, processed_seconds=context.processed_seconds,
                duration=context.duration, bytes_downloaded=context.bytes_downloaded,
                source_size=context.source_size, completed_segments=context.completed_segments,
                total_segments=context.total_segments,
            )
    finally:
        context.original_saved.set()


async def abort_local_video_preparation(file: File, reason: str) -> None:
    """Cancel local preparation only when the original never reached a durable database row."""

    context = _LOCAL_VIDEO_PREPARATIONS.get(id(file))
    if context is None:
        return

    if file.id is not None and not context.original_saved.is_set():
        # A caller can be cancelled after the file row commits but before Redis registration.
        # Confirm that the row really exists, then repair the job registration before releasing
        # the processor. An allocated ID alone does not prove the transaction committed.
        persisted_original = await asyncio.to_thread(_load_original, int(file.id))
        if persisted_original is not None:
            try:
                await enqueue_video_processing_job(
                    int(file.id),
                    status="preparing",
                    message="Preparing a local streaming copy alongside the original upload.",
                    source_size=file.size,
                    username=file.username,
                    name=file.name,
                )
                await notify_video_file_saved(file)
            except Exception as status_error:
                write_log(
                    "ERROR", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
                    f"Could not register local preparation after the upload task ended file_id={file.id}: {status_error}",
                )
                context.original_saved.set()

    if file.id is not None and context.original_saved.is_set():
        write_log(
            "WARNING", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
            f"Upload task ended after original was saved file_id={file.id} name={file.name!r}; "
            f"continuing local processing: {reason}",
        )
        return

    if context.task is not None and not context.task.done():
        context.task.cancel()
        await asyncio.gather(context.task, return_exceptions=True)
    _LOCAL_VIDEO_PREPARATIONS.pop(id(file), None)
    shutil.rmtree(context.staging_root, ignore_errors=True)
    write_log(
        "WARNING", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
        f"Cancelled local preparation because original upload failed before a database row existed "
        f"name={file.name!r}: {reason}",
    )

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



async def get_video_processing_status(file_id: int) -> dict[str, Any]:
    """Return completed derivative metadata or the current processing state from Redis."""

    processed = get_processed_video(file_id)
    if processed is not None:
        media_info = processed.get("media_info") or {}
        duration = float(media_info.get("duration") or 0)
        segment_count = sum(
            len(segments)
            for tracks in (processed.get("assets") or {}).values()
            if isinstance(tracks, dict)
            for segments in tracks.values()
            if isinstance(segments, list)
        )
        return {
            "file_id": file_id, "status": "ready", "stage": "ready", "progress": 100.0,
            "message": "Streaming version is ready.", "processed_seconds": duration,
            "duration": duration, "completed_segments": segment_count,
            "total_segments": segment_count, "error": None,
        }

    job = await Redis.redis.hgetall(_video_job_key(file_id))
    if not job:
        return {
            "file_id": file_id, "status": "untracked", "stage": "untracked", "progress": 0.0,
            "message": "This video has not entered the permanent processing pipeline yet.",
            "processed_seconds": 0.0, "duration": 0.0, "completed_segments": 0,
            "total_segments": 0, "bytes_downloaded": 0, "source_size": 0,
            "attempts": 0, "error": None,
        }

    try:
        progress = float(job.get("progress") or 0)
        processed_seconds = float(job.get("processed_seconds") or 0)
        duration_value = float(job.get("duration") or 0)
    except (TypeError, ValueError):
        progress, processed_seconds, duration_value = 0.0, 0.0, 0.0

    def integer_field(key: str) -> int:
        try:
            return max(0, int(float(job.get(key) or 0)))
        except (TypeError, ValueError):
            return 0

    return {
        "file_id": file_id, "status": job.get("status") or "queued",
        "stage": job.get("stage") or "queued",
        "progress": max(0.0, min(100.0, progress)),
        "message": job.get("message") or "Waiting for video processing.",
        "processed_seconds": max(0.0, processed_seconds), "duration": max(0.0, duration_value),
        "completed_segments": integer_field("completed_segments"),
        "total_segments": integer_field("total_segments"),
        "bytes_downloaded": integer_field("bytes_downloaded"),
        "source_size": integer_field("source_size"),
        "attempts": integer_field("attempts"), "error": job.get("error") or None,
    }


async def retry_video_processing(file_id: int) -> bool:
    """Atomically requeue a failed job in Redis without touching the original uploaded file."""

    if get_processed_video(file_id) is not None:
        return False
    result = await Redis.redis.eval(
        _RETRY_VIDEO_JOB, 4, _video_job_key(file_id), _JOB_QUEUE, _JOB_SEQUENCE, _JOB_INDEX,
        str(file_id), str(time()),
    )
    if result:
        write_log(
            "INFO", Database, "VIDEO PROCESSING", str(file_id),
            f"Video processing retry queued file_id={file_id}.",
        )
    return bool(result)



async def _update_for_original(original: File, fallback_file_id: int | None = None, **updates: Any) -> None:
    """Persist progress to Redis when the original has an ID and retain pre-ID snapshots locally."""

    context = _LOCAL_VIDEO_PREPARATIONS.get(id(original))
    if context is not None:
        if updates.get("status") == "processing":
            # Active local preparation must not be mistaken for abandoned worker work.
            updates["status"] = "preparing"
        for key in (
            "stage", "progress", "message", "processed_seconds", "duration",
            "bytes_downloaded", "source_size", "completed_segments", "total_segments",
        ):
            if key in updates:
                setattr(context, key, updates[key])

        # File.save() assigns its database ID before its awaited Redis registration.
        # Keep progress only in this context until notify_video_file_saved publishes
        # the initialized job hash and opens the original-upload barrier.
        if not context.original_saved.is_set():
            return

    file_id = original.id if original.id is not None else fallback_file_id
    if file_id is None:
        return
    await _update_job(int(file_id), **updates)

async def _update_job(file_id: int, **updates: Any) -> None:
    """Persist an allow-listed video progress snapshot to a Redis hash."""

    allowed = {
        "status", "stage", "progress", "message", "processed_seconds", "duration",
        "bytes_downloaded", "source_size", "completed_segments", "total_segments",
        "error", "started_at", "finished_at",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return

    now = time()
    mapping: dict[str, str] = {"updated_at": str(now)}
    for key, value in values.items():
        if key in {"started_at", "finished_at"}:
            if value == "NOW()":
                value = now
            elif value is None:
                value = ""
        elif value is None:
            value = ""
        mapping[key] = str(value)

    job_key = _video_job_key(file_id)
    await Redis.redis.hset(job_key, mapping=mapping)
    await Redis.redis.sadd(_JOB_INDEX, str(file_id))
    status = mapping.get("status")
    if status == "queued":
        sequence = await Redis.redis.incr(_JOB_SEQUENCE)
        await Redis.redis.zadd(_JOB_QUEUE, {str(file_id): sequence})
    elif status in {"preparing", "processing", "ready", "failed"}:
        await Redis.redis.zrem(_JOB_QUEUE, str(file_id))


async def _requeue_stale_jobs() -> None:
    """Requeue abandoned processing jobs whose Redis progress lease is stale."""

    now = time()
    for value in await Redis.redis.smembers(_JOB_INDEX):
        job = await Redis.redis.hgetall(_video_job_key(int(value)))
        if not job or job.get("status") != "processing":
            continue
        updated_at = job.get("updated_at", "")
        try:
            stale = now - float(updated_at) > 600
        except (TypeError, ValueError):
            stale = True
        if not stale:
            continue
        requeued = await Redis.redis.eval(
            _REQUEUE_STALE_VIDEO_JOB, 3, _video_job_key(int(value)),
            _JOB_QUEUE, _JOB_SEQUENCE, str(value), updated_at, str(now),
        )
        if requeued:
            write_log(
                "WARNING", Database, "VIDEO PROCESSING", str(value),
                f"Requeued stale Redis video job file_id={value}.",
            )


async def _recover_interrupted_jobs(startup_time: float) -> None:
    """Recover jobs left mid-processing before this worker started and repair missing queue entries."""

    for value in await Redis.redis.smembers(_JOB_INDEX):
        file_id = int(value)
        key = _video_job_key(file_id)
        job = await Redis.redis.hgetall(key)
        if not job:
            await Redis.redis.srem(_JOB_INDEX, str(file_id))
            continue
        status = job.get("status") or "queued"
        try:
            updated_at = float(job.get("updated_at") or 0)
        except (TypeError, ValueError):
            updated_at = 0.0

        if status in {"processing", "preparing"} and updated_at <= startup_time:
            now = str(time())
            await Redis.redis.hset(key, mapping={
                "status": "queued", "stage": "queued", "progress": "0",
                "message": "Resuming after server restart; source will be recovered from the stored original.",
                "processed_seconds": "0", "completed_segments": "0", "total_segments": "0",
                "error": "", "updated_at": now, "started_at": "", "finished_at": "",
            })
            sequence = await Redis.redis.incr(_JOB_SEQUENCE)
            await Redis.redis.zadd(_JOB_QUEUE, {str(file_id): sequence})
            write_log(
                "INFO", Database, "VIDEO PROCESSING", str(file_id),
                f"Recovered interrupted Redis video job file_id={file_id}.",
            )
        elif status == "queued" and await Redis.redis.zscore(_JOB_QUEUE, str(file_id)) is None:
            sequence = await Redis.redis.incr(_JOB_SEQUENCE)
            await Redis.redis.zadd(_JOB_QUEUE, {str(file_id): sequence})


async def _recover_nvenc_smoke_test_failures() -> None:
    """Retry jobs rejected by the previous undersized NVENC startup-test frame."""

    for value in await Redis.redis.smembers(_JOB_INDEX):
        file_id = int(value)
        key = _video_job_key(file_id)
        job = await Redis.redis.hgetall(key)
        if job.get("status") != "failed":
            continue
        error = str(job.get("error") or "")
        if "Frame Dimension less than the minimum supported value" not in error:
            continue
        if get_processed_video(file_id) is not None:
            continue

        await Redis.redis.hset(key, mapping={"attempts": "0"})
        if await retry_video_processing(file_id):
            write_log(
                "WARNING", Database, "VIDEO PROCESSING", str(file_id),
                "Automatically requeued after fixing the undersized NVENC startup-test frame.",
            )


async def _claim_next_job() -> int | None:
    """Atomically claim the next queued video job from Redis."""

    value = await Redis.redis.eval(
        _CLAIM_VIDEO_JOB, 1, _JOB_QUEUE, _JOB_PREFIX, str(time()),
    )
    return int(value) if value else None

async def _migrate_legacy_video_jobs() -> None:
    """Move pre-existing PostgreSQL processing jobs into Redis, then remove the old table."""

    with POOL.connection() as connection:
        table = connection.execute(
            "SELECT to_regclass('public.video_processing_jobs') AS table_name;"
        ).fetchone()
        if not table or table.get("table_name") is None:
            return
        rows = connection.execute(
            """
            SELECT file_id, status, stage, progress, message, processed_seconds,
                   duration, bytes_downloaded, source_size, completed_segments,
                   total_segments, attempts, error, created_at, updated_at,
                   started_at, finished_at
            FROM video_processing_jobs
            ORDER BY created_at, file_id;
            """
        ).fetchall()

    migrated = 0
    for row in rows:
        file_id = int(row["file_id"])
        status = str(row.get("status") or "queued")
        if status not in {"queued", "preparing", "processing", "ready", "failed"}:
            status = "queued"

        def as_timestamp(value: Any) -> str:
            if value is None:
                return str(time())
            if hasattr(value, "timestamp"):
                return str(float(value.timestamp()))
            try:
                return str(float(value))
            except (TypeError, ValueError):
                return str(time())

        mapping = {
            "file_id": str(file_id), "status": status,
            "stage": str(row.get("stage") or status),
            "progress": str(row.get("progress") or 0),
            "message": str(row.get("message") or "Waiting for video processing."),
            "processed_seconds": str(row.get("processed_seconds") or 0),
            "duration": str(row.get("duration") or 0),
            "bytes_downloaded": str(row.get("bytes_downloaded") or 0),
            "source_size": str(row.get("source_size") or 0),
            "completed_segments": str(row.get("completed_segments") or 0),
            "total_segments": str(row.get("total_segments") or 0),
            "attempts": str(row.get("attempts") or 0),
            "error": str(row.get("error") or ""),
            "created_at": as_timestamp(row.get("created_at")),
            "updated_at": as_timestamp(row.get("updated_at")),
            "started_at": as_timestamp(row.get("started_at")) if row.get("started_at") is not None else "",
            "finished_at": as_timestamp(row.get("finished_at")) if row.get("finished_at") is not None else "",
        }
        arguments: list[str] = [str(file_id), status]
        for key, value in mapping.items():
            arguments.extend((key, value))
        migrated += int(await Redis.redis.eval(
            _MIGRATE_LEGACY_VIDEO_JOB, 4, _video_job_key(file_id), _JOB_QUEUE,
            _JOB_SEQUENCE, _JOB_INDEX, *arguments,
        ))

    with POOL.connection() as connection:
        connection.execute("DROP TABLE IF EXISTS video_processing_jobs;")
        connection.commit()
    write_log(
        "INFO", Database, "VIDEO PROCESSING", "system",
        f"Migrated {migrated} legacy video job(s) to Redis and removed the PostgreSQL job table.",
    )

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





_GPU_PIPELINE_READY: set[str] = set()
_GPU_PIPELINE_LOCK = asyncio.Lock()


async def ensure_gpu_video_pipeline(ffmpeg_path: str) -> None:
    """Verify FFmpeg can decode, filter, and encode video through NVIDIA CUDA/NVENC."""

    if ffmpeg_path in _GPU_PIPELINE_READY:
        return

    async with _GPU_PIPELINE_LOCK:
        if ffmpeg_path in _GPU_PIPELINE_READY:
            return

        try:
            encoder_process = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-hide_banner", "-encoders",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            encoder_stdout, encoder_stderr = await asyncio.wait_for(encoder_process.communicate(), timeout=15)
            encoder_output = (
                encoder_stdout.decode("utf-8", errors="replace")
                + encoder_stderr.decode("utf-8", errors="replace")
            )
            if encoder_process.returncode != 0 or "h264_nvenc" not in encoder_output:
                raise RuntimeError("This FFmpeg build does not expose the h264_nvenc hardware encoder.")

            hwaccel_process = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-hide_banner", "-hwaccels",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            hwaccel_stdout, hwaccel_stderr = await asyncio.wait_for(hwaccel_process.communicate(), timeout=15)
            hwaccel_output = (
                hwaccel_stdout.decode("utf-8", errors="replace")
                + hwaccel_stderr.decode("utf-8", errors="replace")
            )
            if hwaccel_process.returncode != 0 or not any(
                line.strip().lower() == "cuda" for line in hwaccel_output.splitlines()
            ):
                raise RuntimeError("This FFmpeg build does not expose CUDA hardware acceleration.")

            # Exercise CUDA upload, CUDA scaling/pixel-format conversion, and NVENC itself.
            # Checking encoder names alone is insufficient when the NVIDIA driver/device is
            # unavailable in the runtime (common in CPU-only cloud instances).
            test_process = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=640x360:r=1",
                "-vf", "format=nv12,hwupload_cuda,scale_cuda=format=nv12",
                "-frames:v", "1",
                "-an",
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "22",
                "-b:v", "0",
                "-f", "null", "-",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, test_stderr = await asyncio.wait_for(test_process.communicate(), timeout=30)
            except asyncio.TimeoutError:
                if test_process.returncode is None:
                    test_process.kill()
                await test_process.communicate()
                raise RuntimeError("The CUDA/NVENC startup test timed out after 30 seconds.")

            if test_process.returncode != 0:
                detail = sanitize_sensitive_urls(
                    test_stderr.decode("utf-8", errors="replace").strip()
                )[-1200:]
                raise RuntimeError(
                    "NVIDIA GPU video processing is required, but the CUDA/NVENC startup test failed. "
                    "Use a runtime with an available NVIDIA GPU, compatible drivers, and FFmpeg built with "
                    "CUDA, scale_cuda, hwupload_cuda, and h264_nvenc."
                    + (f" FFmpeg detail: {detail}" if detail else "")
                )

        except asyncio.CancelledError:
            raise
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(
                "Could not verify the NVIDIA GPU video-processing pipeline. Ensure an NVIDIA GPU and its "
                "drivers are available and that the installed FFmpeg supports CUDA/NVENC."
            ) from error

        _GPU_PIPELINE_READY.add(ffmpeg_path)
        write_log(
            "INFO", Database, "VIDEO PROCESSING", "system",
            f"GPU pipeline verified for FFmpeg={ffmpeg_path!r}: CUDA decode/filter path and h264_nvenc encoding are available.",
        )


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
        original: File,
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
                await _update_for_original(
                    original,
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

    write_log(
        "WARNING", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
        f"Reading original file_id={file.id} name={file.name!r} back from remote storage because no local processing source survived.",
    )
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
                await _update_job(
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
    write_log(
        "INFO", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
        f"Recovery source read complete file_id={file.id} bytes={downloaded}.",
    )
    await _update_job(
        int(file.id),
        stage="probing",
        progress=15.0,
        message="Original downloaded. Inspecting video and audio tracks.",
        bytes_downloaded=downloaded,
        source_size=file.size,
    )


async def _process_video(
        file_id: int | None,
        *,
        original_override: File | None = None,
        source_path_override: Path | None = None,
        original_saved_event: asyncio.Event | None = None,
) -> None:
    """Build HLS assets from local staging when possible and queue derivative upload after original finalization."""

    original = original_override
    if original is None:
        if file_id is None:
            raise RuntimeError("A file ID is required for remote-source recovery.")
        original = _load_original(file_id)

    if original is None:
        write_log("WARNING", Database, "VIDEO PROCESSING", str(file_id or "unknown"), "Original database row disappeared before processing.")
        return

    current_file_id = int(original.id) if original.id is not None else file_id
    if original.deleted_at is not None:
        if current_file_id is not None:
            await _update_for_original(
                original, current_file_id,
                status="failed",
                stage="failed",
                message="This video is in the trash and was not processed.",
                error="The original file is deleted.",
                finished_at="NOW()",
            )
        return

    if source_path_override is None and (not original.links or original.size <= 0):
        raise RuntimeError("The original file is missing storage links or has an invalid size.")
    if not is_video_file(original.name, original.type):
        if current_file_id is not None:
            await _update_for_original(
                original, current_file_id,
                status="failed",
                stage="failed",
                message="This file is not a video.",
                error="Not a video file.",
                finished_at="NOW()",
            )
        return

    ffmpeg_path, ffprobe_path = _media_tool_paths()
    data_center = DataCenter(original.data_center)
    if data_center is None:
        raise RuntimeError(f"Unsupported data center: {original.data_center}")

    await ensure_gpu_video_pipeline(str(ffmpeg_path))
    file_label = f"file_id={original.id} name={original.name!r}"
    write_log(
        "INFO", data_center, "VIDEO PROCESSING", original.username,
        f"Starting stream preparation {file_label} source_mode={'local' if source_path_override is not None else 'remote-recovery'}.",
    )

    with tempfile.TemporaryDirectory(prefix=f"storelimitless-video-{original.id or 'pending'}-") as temporary:
        root = Path(temporary)
        output_root = root / "hls"
        output_root.mkdir(parents=True, exist_ok=True)

        if source_path_override is not None:
            source_path = source_path_override
            source_size = source_path.stat().st_size
            await _update_for_original(
                original, current_file_id,
                status="preparing",
                stage="probing",
                progress=10.0,
                message="Using the preserved local source. Inspecting video and audio tracks.",
                bytes_downloaded=source_size,
                source_size=source_size,
            )
            write_log(
                "INFO", data_center, "VIDEO PROCESSING", original.username,
                f"Using preserved local source {file_label} source_size={source_size}; no remote original download required.",
            )
        else:
            source_path = root / "original"
            await _download_original(original, source_path)

        media_info = await _probe_media(source_path, ffprobe_path)
        duration = float(media_info["duration"])
        audio_tracks = list(media_info.get("audio") or [])
        if math.ceil(duration / VIDEO_SEGMENT_SECONDS) > 50000:
            raise RuntimeError("This video's duration exceeds the supported HLS segment limit.")

        write_log(
            "INFO", data_center, "VIDEO PROCESSING", original.username,
            f"Probe complete {file_label} duration={duration:.3f}s video_codec={media_info['video'].get('codec')!r} "
            f"audio_tracks={len(audio_tracks)} subtitle_tracks={len(media_info.get('subtitles') or [])}.",
        )
        expected_segments_per_track = math.ceil(duration / VIDEO_SEGMENT_SECONDS)
        total_segments = expected_segments_per_track * (1 + len(audio_tracks))
        await _update_for_original(
            original, current_file_id,
            status="preparing" if original_saved_event is not None and not original_saved_event.is_set() else "processing",
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
            command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
            if kind == "video":
                # Keep decoded frames in VRAM and normalize to NV12 with CUDA before NVENC.
                command.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
            command.extend([
                "-i", str(source_path),
                "-map", f"0:{stream_index}",
                "-sn", "-dn",
            ])
            if kind == "video":
                command.extend([
                    "-an",
                    "-vf", "scale_cuda=w=trunc(iw/2)*2:h=trunc(ih/2)*2:format=nv12",
                    "-c:v", "h264_nvenc",
                    "-preset", "p5",
                    "-tune", "hq",
                    "-rc", "vbr",
                    "-cq", "22",
                    "-b:v", "0",
                    "-g", "120",
                    "-keyint_min", "120",
                    "-sc_threshold", "0",
                    "-force_key_frames", f"expr:gte(t,n_forced*{VIDEO_SEGMENT_SECONDS})",
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
            write_log(
                "INFO", data_center, "VIDEO PROCESSING", original.username,
                f"FFmpeg started {file_label} track={label!r} index={stream_index} duration={duration:.3f}s.",
            )
            await _run_ffmpeg_track(
                command,
                duration=duration,
                track_label=label,
                progress_base=20.0,
                progress_span=55.0,
                total_tracks=len(track_specs),
                track_number=track_number,
                original=original,
            )
            segments = _parse_hls_segments(playlist_path)
            media_info.setdefault("_processed_assets", {}).setdefault(kind, {})[str(stream_index)] = []
            for segment_path, segment_duration in segments:
                media_info["_processed_assets"][kind][str(stream_index)].append({
                    "path": str(segment_path),
                    "duration": segment_duration,
                })
            await _update_for_original(
                original, current_file_id,
                status="preparing" if original_saved_event is not None and not original_saved_event.is_set() else "processing",
                progress=round(min(75.0, 20.0 + 55.0 * track_number / max(1, len(track_specs))), 2),
                message=f"Prepared {track_number} of {len(track_specs)} media tracks.",
                completed_segments=min(total_segments, sum(
                    len(items)
                    for tracks in media_info["_processed_assets"].values()
                    for items in tracks.values()
                )),
            )
            write_log(
                "INFO", data_center, "VIDEO PROCESSING", original.username,
                f"FFmpeg completed {file_label} track={label!r} generated_segments={len(segments)}.",
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
                    await _update_for_original(
                        original, current_file_id,
                        status="preparing" if original_saved_event is not None and not original_saved_event.is_set() else "processing",
                        stage="packaging",
                        progress=round(packaging_progress, 2),
                        message=f"Packaging streamable segments ({current_asset}/{asset_count}).",
                        completed_segments=current_asset,
                        total_segments=asset_count,
                    )

        if pack_offset <= 0 or not pack_path.is_file():
            raise RuntimeError("No streamable media segments were generated.")

        if original_saved_event is not None and not original_saved_event.is_set():
            write_log(
                "INFO", data_center, "VIDEO PROCESSING", original.username,
                f"Processing finished before original upload {file_label}; holding derivative locally until original finalization.",
            )
            await _update_for_original(
                original, current_file_id,
                status="preparing",
                stage="waiting_for_original_upload",
                progress=80.0,
                message="Stream preparation is complete. Waiting for the original upload to finish before storing the streaming copy.",
                completed_segments=asset_count,
                total_segments=asset_count,
            )
            await original_saved_event.wait()

        if original.id is None:
            raise RuntimeError("The original database record was not committed before derivative upload.")
        file_id = int(original.id)

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

        await _update_job(
            file_id,
            status="preparing",
            stage="waiting_for_upload",
            progress=80.0,
            message="Stream preparation complete. Waiting for the shared upload queue.",
            completed_segments=asset_count,
            total_segments=asset_count,
        )
        upload_job_status = "preparing" if original_saved_event is not None else "processing"
        ticket: IngestionUploadTicket = await enqueue_ingestion_upload(packed_file.name, original.username)
        write_log(
            "INFO", data_center, "VIDEO PROCESSING", original.username,
            f"Queued processed derivative file_id={file_id} size={pack_offset} behind all active original/file uploads.",
        )
        try:
            async with ingestion_upload_slot(ticket):
                await _update_job(
                    file_id,
                    status=upload_job_status,
                    stage="storing",
                    progress=80.0,
                    message="Uploading the processed streaming copy to the data center.",
                    completed_segments=asset_count,
                    total_segments=asset_count,
                )
                write_log(
                    "INFO", data_center, "VIDEO PROCESSING", original.username,
                    f"Processed derivative upload started file_id={file_id} packed_size={pack_offset}.",
                )
                async for progress in upload_growing_file(packed_file, pack_path, data_center, upload_progress):
                    transferred = min(pack_offset, max(0, int(progress.transfer or 0)))
                    percentage = 80.0 + 18.0 * transferred / max(1, pack_offset)
                    await _update_job(
                        file_id,
                        status=upload_job_status,
                        stage="storing",
                        progress=round(min(98.0, percentage), 2),
                        message=f"Uploading processed stream ({transferred * 100 / max(1, pack_offset):.1f}%).",
                    )
        finally:
            if not ticket.claimed:
                await cancel_ingestion_upload(ticket)

        expected_parts = math.ceil(pack_offset / int(data_center.MAX_SIZE))
        if packed_file.size != pack_offset or len(packed_file.links) != expected_parts:
            raise RuntimeError("The uploaded streaming package failed its size or chunk-index validation.")

        # Validate a provider reference before publishing the stream as ready.
        first_chunk = await data_center.download(packed_file.links[0])
        if len(first_chunk) != min(int(data_center.MAX_SIZE), pack_offset):
            raise RuntimeError("The processed stream could not be verified on the data center.")
        write_log(
            "INFO", data_center, "VIDEO PROCESSING", original.username,
            f"Derivative storage verified file_id={file_id} packed_size={pack_offset} chunks={len(packed_file.links)}.",
        )

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
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        await _update_job(
            file_id,
            status="ready",
            stage="ready",
            progress=100,
            message="Streaming version is ready.",
            processed_seconds=duration,
            completed_segments=total_segments,
            total_segments=total_segments,
            error=None,
            finished_at="NOW()",
        )
        write_log(
            "INFO", data_center, "VIDEO PROCESSING", original.username,
            f"Video is ready file_id={file_id} name={original.name!r} duration={duration:.3f}s segments={asset_count}.",
        )


async def _run_local_video_preparation(context: LocalVideoPreparation) -> None:
    """Prepare locally while original chunks upload, then wait for original finalization before derivative upload."""

    file = context.file
    try:
        await _process_video(
            None,
            original_override=file,
            source_path_override=context.source_path,
            original_saved_event=context.original_saved,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        detail = " ".join(sanitize_sensitive_urls(str(error)).split())[:1800] or type(error).__name__
        # If the original upload is still active, wait for its terminal outcome before
        # deciding whether a durable processing job exists to mark failed.
        if not context.original_saved.is_set():
            await context.original_saved.wait()
        if file.id is not None:
            try:
                await _update_job(
                    int(file.id),
                    status="failed",
                    stage="failed",
                    message="Video processing failed.",
                    error=detail,
                    finished_at="NOW()",
                )
            except Exception as status_error:
                write_log(
                    "ERROR", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
                    f"Could not persist failure file_id={file.id}: {status_error}",
                )
        write_log(
            "ERROR", DataCenter(file.data_center) or Database, "VIDEO PROCESSING", file.username,
            f"Local stream preparation failed file_id={file.id} name={file.name!r}: {detail}",
        )
    finally:
        _LOCAL_VIDEO_PREPARATIONS.pop(id(file), None)
        shutil.rmtree(context.staging_root, ignore_errors=True)


async def _process_job(file_id: int) -> None:
    """Run one queued job and record a useful failure without damaging the original."""
    try:
        write_log("INFO", Database, "VIDEO PROCESSING", str(file_id), f"Persistent worker claimed file_id={file_id}.")
        await _process_video(file_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        detail = " ".join(str(error).split())[:1800] or type(error).__name__
        try:
            await _update_job(
                file_id,
                status="failed",
                stage="failed",
                message="Video processing failed.",
                error=detail,
                finished_at="NOW()",
            )
        except Exception as status_error:
            print(f"Could not persist video processing failure for file {file_id}: {status_error}")
        write_log("ERROR", Database, "VIDEO PROCESSING", str(file_id), f"Persistent processing failed file_id={file_id}: {detail}")



# >>> TEMPORARY LEGACY VIDEO BACKFILL PIPELINE
# This whole section and its worker hook are temporary. Remove them together when the
# user asks to remove the one-time legacy-video backfill.
# Backfilled jobs use the same mandatory CUDA/NVENC path as newly uploaded videos.
_TEMPORARY_BACKFILL_SCAN_INTERVAL_SECONDS = 5.0
_TEMPORARY_BACKFILL_MAX_ATTEMPTS = 3
_TEMPORARY_BACKFILL_COMPLETE_KEY = "video:processing:temporary-backfill:complete"
_TEMPORARY_BACKFILL_FAILURE_PREFIX = "video:processing:temporary-backfill:failed:"


async def _temporary_backfill_unprocessed_video() -> bool:
    """TEMPORARY: enqueue one legacy video lacking a processed HLS record."""

    if await Redis.redis.exists(_TEMPORARY_BACKFILL_COMPLETE_KEY):
        return False

    with POOL.connection() as connection:
        rows = connection.execute(
            """
            SELECT f.id, f.name, f.type, f.size, f.username
            FROM files AS f
            LEFT JOIN processed_videos AS pv ON pv.id = f.id
            WHERE pv.id IS NULL
              AND f.deleted_at IS NULL
              AND (
                  LOWER(COALESCE(f.type, '')) LIKE 'video/%'
                  OR LOWER(COALESCE(f.name, '')) ~ E'\\\\.(3g2|3gp|asf|avi|divx|flv|m2ts|m2v|m4v|mkv|mov|mpe|mpeg|mpg|mts|mxf|ogv|rm|rmvb|ts|vob|webm|wmv)$'
              )
            ORDER BY f.id ASC;
            """
        ).fetchall()

    pending_job_exists = False
    exhausted_file_ids: list[int] = []

    # Queue only one legacy file at a time. The existing worker and shared upload FIFO
    # remain responsible for processing and derivative uploads, respectively.
    for row in rows:
        file_id = int(row["id"])
        job_key = _video_job_key(file_id)
        job = await Redis.redis.hgetall(job_key)

        if not job:
            created = await enqueue_video_processing_job(
                file_id,
                status="queued",
                message="Queued by the temporary legacy video backfill.",
                source_size=int(row.get("size") or 0),
                username=str(row.get("username") or "system"),
                name=str(row.get("name") or ""),
            )
            if created:
                await Redis.redis.delete(_TEMPORARY_BACKFILL_COMPLETE_KEY)
                write_log(
                    "INFO", Database, "TEMP VIDEO BACKFILL", str(row.get("username") or "system"),
                    f"Queued legacy video file_id={file_id} name={str(row.get('name') or '')!r}; "
                    "waiting for the normal Redis processing worker.",
                )
                return True
            continue

        status = str(job.get("status") or "queued").casefold()
        try:
            attempts = max(0, int(job.get("attempts") or 0))
        except (TypeError, ValueError):
            attempts = 0

        if status == "queued":
            # Repair a missing sorted-set entry without creating a duplicate job.
            if await Redis.redis.zscore(_JOB_QUEUE, str(file_id)) is None:
                sequence = await Redis.redis.incr(_JOB_SEQUENCE)
                await Redis.redis.zadd(_JOB_QUEUE, {str(file_id): sequence})
                write_log(
                    "WARNING", Database, "TEMP VIDEO BACKFILL", str(row.get("username") or "system"),
                    f"Repaired missing Redis queue entry for legacy video file_id={file_id}.",
                )
                return True
            pending_job_exists = True
            continue

        if status in {"preparing", "processing"}:
            pending_job_exists = True
            continue

        if status == "failed":
            if attempts < _TEMPORARY_BACKFILL_MAX_ATTEMPTS:
                if await retry_video_processing(file_id):
                    write_log(
                        "WARNING", Database, "TEMP VIDEO BACKFILL", str(row.get("username") or "system"),
                        f"Retrying failed legacy video file_id={file_id} attempt={attempts + 1}/"
                        f"{_TEMPORARY_BACKFILL_MAX_ATTEMPTS}.",
                    )
                    return True
            else:
                exhausted_file_ids.append(file_id)
                failure_key = f"{_TEMPORARY_BACKFILL_FAILURE_PREFIX}{file_id}"
                first_report = await Redis.redis.set(failure_key, "1", nx=True)
                if first_report:
                    write_log(
                        "ERROR", Database, "TEMP VIDEO BACKFILL", str(row.get("username") or "system"),
                        f"Skipping legacy video after {_TEMPORARY_BACKFILL_MAX_ATTEMPTS} processing attempts "
                        f"file_id={file_id} name={str(row.get('name') or '')!r}; inspect the VIDEO PROCESSING error log.",
                    )
            continue

        # A Redis 'ready' state without a durable processed_videos row is inconsistent.
        # Reset it so the original can be recovered and the derivative can be rebuilt.
        await _update_job(
            file_id,
            status="queued",
            stage="queued",
            progress=0,
            message="Rebuilding missing processed-video metadata during temporary backfill.",
            processed_seconds=0,
            completed_segments=0,
            total_segments=0,
            error=None,
            started_at=None,
            finished_at=None,
        )
        write_log(
            "WARNING", Database, "TEMP VIDEO BACKFILL", str(row.get("username") or "system"),
            f"Requeued legacy video with missing processed metadata file_id={file_id} previous_status={status!r}.",
        )
        return True

    if pending_job_exists:
        return False

    # No unprocessed video can be queued or retried. Persist this marker so a completed
    # one-time backfill does not rescan the whole library on every worker poll/restart.
    completed = await Redis.redis.set(_TEMPORARY_BACKFILL_COMPLETE_KEY, str(time()), nx=True)
    if completed:
        if exhausted_file_ids:
            remaining = len(exhausted_file_ids)
            preview = ", ".join(str(value) for value in exhausted_file_ids[:30])
            suffix = "…" if remaining > 30 else ""
            write_log(
                "WARNING", Database, "TEMP VIDEO BACKFILL", "system",
                f"Backfill reached its stopping point: {remaining} video(s) remain unprocessed after "
                f"{_TEMPORARY_BACKFILL_MAX_ATTEMPTS} attempts. file_ids=[{preview}{suffix}]. "
                "Other eligible legacy videos have been processed.",
            )
        else:
            write_log(
                "INFO", Database, "TEMP VIDEO BACKFILL", "system",
                f"Backfill complete: all {len(rows)} discovered legacy video(s) have processed HLS records.",
            )
    return False


# <<< TEMPORARY LEGACY VIDEO BACKFILL PIPELINE


async def run_video_processing_worker() -> None:
    """Recover and process Redis-backed video jobs, one at a time."""

    recovered = False
    startup_time = time()
    next_stale_check = startup_time
    # >>> TEMPORARY LEGACY VIDEO BACKFILL HOOK
    next_backfill_scan = startup_time
    # <<< TEMPORARY LEGACY VIDEO BACKFILL HOOK
    while True:
        try:
            if not recovered:
                await _migrate_legacy_video_jobs()
                await _recover_interrupted_jobs(startup_time)
                await _recover_nvenc_smoke_test_failures()
                recovered = True
            now = time()
            if now >= next_stale_check:
                await _requeue_stale_jobs()
                next_stale_check = now + _JOB_REQUEUE_INTERVAL
            file_id = await _claim_next_job()
            if file_id is None:
                # >>> TEMPORARY LEGACY VIDEO BACKFILL HOOK
                now = time()
                if now >= next_backfill_scan:
                    await _temporary_backfill_unprocessed_video()
                    next_backfill_scan = time() + _TEMPORARY_BACKFILL_SCAN_INTERVAL_SECONDS
                # <<< TEMPORARY LEGACY VIDEO BACKFILL HOOK
                await asyncio.sleep(_JOB_POLL_SECONDS)
                continue
            await _process_job(file_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Keep the worker alive if Redis, the database, or the storage provider is briefly unavailable.
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
