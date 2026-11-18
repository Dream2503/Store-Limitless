"""Redis connection and serialization helpers for cached users, directories, files, progress records, and JSON-based application state."""

from __future__ import annotations

from datetime import datetime
from time import monotonic, time
from json import dumps, loads
from pathlib import Path
from typing import Literal, cast, TYPE_CHECKING

import redis.asyncio

from backend.database.connection import POOL, DirectoryDict, FileDict
from core.utils import JsonObject, write_log
from core.utils.progress import Progress

if TYPE_CHECKING:
    from backend.database.models import User, Directory, File


def _metric_stage(progress: Progress) -> str:
    """Return a stable stage key so changing status text does not reset transfer speed."""

    if progress.ingestion_type:
        return progress.ingestion_type

    message = str(progress.message or "").casefold()
    if progress.download_active or "downloading and uploading" in message or "receiving and uploading" in message:
        return "concurrent"
    if message.startswith("downloading video"):
        return "downloading_video"
    if message.startswith("downloading audio"):
        return "downloading_audio"
    if message.startswith("merging"):
        return "merging"
    if message.startswith("downloading") or message.startswith("searching for peers"):
        return "downloading"
    if "upload" in message:
        return "uploading"
    return message.split("(", 1)[0].strip() or "starting"


def _file_progress_counts(items: list[JsonObject]) -> dict[str, int]:
    """Summarize ingestion file states for the persisted top-level job."""

    statuses = [str(item.get("status") or "queued").casefold() for item in items]
    return {
        "discovered": len(items),
        "pending": sum(status in {"queued", "pending", "available"} for status in statuses),
        "downloading": statuses.count("downloading"),
        "downloaded": sum(status in {"downloaded", "waiting_upload", "uploading", "completed"} for status in statuses),
        "waiting_upload": statuses.count("waiting_upload"),
        "uploading": statuses.count("uploading"),
        "completed": statuses.count("completed"),
        "failed": sum(status in {"error", "failed"} for status in statuses),
        "cancelled": sum(status in {"cancelled", "skipped"} for status in statuses),
    }


class Redis:
    """Manage the shared asynchronous Redis client and serialize application model objects to and from Redis keys and hashes."""

    def __new__(cls, *args: object, **kwargs: object) -> Redis:
        """Prevent instantiation because Redis exposes class-level operations only."""

        raise TypeError("Redis cannot be instantiated")

    ValueTypes = Literal["Progress", "Directory", "File", "User"]
    redis: redis.asyncio.Redis

    @classmethod
    async def initialize(cls) -> None:
        """Create the asynchronous Redis client and verify connectivity with PING. Existing Redis data is intentionally preserved."""

        from core.data_center import Database

        cls.redis = redis.asyncio.Redis.from_url("redis://localhost:6379", decode_responses=True)
        try:
            await cls.redis.ping()
        except Exception as error:
            write_log("ERROR", Database, "REDIS", "server", f"Connection check failed error={type(error).__name__}: {str(error)[:250]}")
            raise

        write_log("INFO", Database, "REDIS", "server", "Connected and PING succeeded")

    @classmethod
    async def close(cls) -> None:
        """Close the asynchronous Redis client connection pool."""

        await cls.redis.aclose()
        from core.data_center import Database
        write_log("INFO", Database, "REDIS", "server", "Connection pool closed")

    @classmethod
    async def get_json(cls, key: str) -> object | None:
        """Read an arbitrary JSON-serializable value stored under a Redis key."""

        value: str | None = await cls.redis.get(key)
        return loads(value) if value is not None else None

    @classmethod
    async def set_json(cls, key: str, value: object, *, ex: int | None = None, nx: bool = False) -> bool:
        """Store JSON data, optionally with an expiry or SET-NX semantics."""

        result = await cls.redis.set(key, dumps(value, default=str), ex=ex, nx=nx)
        return bool(result)

    @classmethod
    async def delete_key(cls, key: str) -> None:
        """Delete a raw Redis key regardless of its stored value type."""

        await cls.redis.delete(key)

    @classmethod
    async def exists(cls, key: str, type: ValueTypes) -> bool:
        """Check whether a supported Redis key or a specific hash entry exists."""

        match type:
            case "Progress" | "User":
                return bool(await cls.redis.exists(key))

            case "Directory" | "File":
                if " " in key:
                    key, id = key.rsplit(" ", 1)
                    return bool(await cls.redis.hexists(key, id))

                return bool(await cls.redis.exists(key))

    @classmethod
    async def get(cls, key: str, type: ValueTypes) -> Progress | Directory | list[Directory] | File | list[File] | User:
        """Load a supported value from Redis, returning one record or a collection for hash-backed models."""

        from backend.database.models import User, Directory, File

        match type:
            case "Progress":
                data: dict[str, str] = await cls.redis.hgetall(key)

                if not data:
                    raise KeyError(key)

                progress: Progress = Progress(data["name"], data["message"], int(data["total"]))
                progress.transfer = int(data["transfer"])
                progress.download_transfer = int(data.get("download_transfer", 0) or 0)
                progress.download_total = int(data.get("download_total", 0) or 0)
                progress.download_active = str(data.get("download_active", "0")).lower() in {"1", "true", "yes"}
                progress.download_speed = max(0.0, float(data.get("download_speed", 0) or 0))
                progress.upload_speed = max(0.0, float(data.get("upload_speed", 0) or 0))
                progress.download_eta = None if float(data.get("download_eta", -1) or -1) < 0 else float(data["download_eta"])
                progress.upload_eta = None if float(data.get("upload_eta", -1) or -1) < 0 else float(data["upload_eta"])
                progress.ingestion_type = str(data.get("ingestion_type", "") or "")
                progress.last_updated_at = max(0.0, float(data.get("last_updated_at", 0) or 0))
                try:
                    raw_counts: object = loads(data.get("file_counts", "{}"))
                    progress.file_counts = cast("dict[str, int]", raw_counts) if isinstance(raw_counts, dict) else {}
                except (TypeError, ValueError):
                    progress.file_counts = {}
                raw_torrent_items: str = data.get("torrent_items", "[]")

                try:
                    torrent_items: object = loads(raw_torrent_items)
                    progress.torrent_items = cast("list[JsonObject]", torrent_items) if isinstance(torrent_items, list) else []

                except (TypeError, ValueError):
                    progress.torrent_items = []
                return progress

            case "Directory" | "File":
                single: bool = " " in key
                values: list[str]

                if single:
                    key, item_id = key.rsplit(" ", 1)
                    value: str | None = await cls.redis.hget(key, item_id)

                    if value is None:
                        raise KeyError(item_id)

                    values = [value]

                else:
                    values = list((await cls.redis.hgetall(key)).values())

                    if not values:
                        return []

                records: list[JsonObject] = [cast("JsonObject", loads(value)) for value in values]

                if type == "Directory":
                    items: list[Directory] | list[File] = [
                        Directory(
                            id=cast("int", record["id"]),
                            path=Path(cast("str", record["path"])),
                            modified_at=datetime.fromisoformat(cast("str", record["modified_at"])),
                            deleted_at=datetime.fromisoformat(cast("str", record["deleted_at"])) if record["deleted_at"] else None,
                            username=cast("str", record["username"]),
                        )
                        for record in records
                    ]

                else:
                    items = [
                        File(
                            id=cast("int", record["id"]),
                            directory_id=cast("int", record["directory_id"]),
                            name=cast("str", record["name"]),
                            type=cast("str", record["type"]),
                            size=cast("int", record["size"]),
                            modified_at=datetime.fromisoformat(cast("str", record["modified_at"])),
                            deleted_at=datetime.fromisoformat(cast("str", record["deleted_at"])) if record["deleted_at"] else None,
                            data_center=cast("str", record["data_center"]),
                            links=cast("list[str]", record["links"]),
                            username=cast("str", record["username"]),
                        )
                        for record in records
                    ]

                return items[0] if single else items

            case "User":
                data: dict[str, str] = await cls.redis.hgetall(key)

                if not data:
                    raise KeyError(key)

                return User(
                    username=data["username"],
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    created_at=datetime.fromisoformat(data["created_at"]),
                )

    @classmethod
    async def delete(cls, key: str, type: ValueTypes) -> None:
        """Delete a Redis key or a single Directory/File hash entry according to the supplied value type."""

        match type:
            case "Progress" | "User":
                await cls.redis.delete(key)

            case "Directory" | "File":
                if " " in key:
                    key: str
                    id: str
                    key, id = key.rsplit(" ", 1)
                    await cls.redis.hdel(key, id)

                else:
                    await cls.redis.delete(key)

    @classmethod
    async def set(cls, key: str, value: Progress | User | Directory | File) -> None:
        """
        Serialize a Progress, User, Directory, or File object into Redis. Saving a User also refreshes the user’s directory and file cache hashes from
        PostgreSQL.
        """

        from backend.database.models import User, Directory, File

        if isinstance(value, Progress):
            # Derive rates from backend-observed byte deltas. Redis keeps the last
            # sample so a browser refresh never has to estimate the authoritative rate.
            previous: dict[str, str] = await cls.redis.hgetall(key)
            now_monotonic = monotonic()
            now_wall = time()
            stage = _metric_stage(value)
            old_stage = str(previous.get("metric_stage") or "")
            old_sample_at = float(previous.get("metric_sample_at", 0) or 0)
            same_stage = old_stage == stage and old_sample_at > 0 and now_monotonic >= old_sample_at
            elapsed = now_monotonic - old_sample_at if same_stage else 0.0

            if value.ingestion_type or value.download_active:
                download_counter = max(0, int(value.download_transfer or 0))
                download_total = max(0, int(value.download_total or 0))
                upload_counter = max(0, int(value.transfer or 0))
                upload_total = max(0, int(value.total or 0))
            elif stage.startswith("downloading"):
                download_counter = max(0, int(value.transfer or 0))
                download_total = max(0, int(value.total or 0))
                upload_counter = 0
                upload_total = 0
            elif stage == "uploading":
                download_counter = 0
                download_total = 0
                upload_counter = max(0, int(value.transfer or 0))
                upload_total = max(0, int(value.total or 0))
            else:
                download_counter = download_total = upload_counter = upload_total = 0

            previous_download = int(previous.get("metric_download_counter", 0) or 0)
            previous_upload = int(previous.get("metric_upload_counter", 0) or 0)
            old_download_speed = max(0.0, float(previous.get("download_speed", 0) or 0))
            old_upload_speed = max(0.0, float(previous.get("upload_speed", 0) or 0))
            previous_download_moved = float(previous.get("metric_download_moved_at", 0) or 0)
            previous_upload_moved = float(previous.get("metric_upload_moved_at", 0) or 0)

            if not same_stage or download_counter < previous_download:
                download_speed = 0.0
                download_moved_at = now_monotonic
            elif elapsed > 0 and download_counter > previous_download:
                measured = (download_counter - previous_download) / elapsed
                download_speed = measured if old_download_speed <= 0 else old_download_speed * 0.7 + measured * 0.3
                download_moved_at = now_monotonic
            else:
                download_moved_at = previous_download_moved or now_monotonic
                download_speed = 0.0 if now_monotonic - download_moved_at >= 4 else old_download_speed

            if not same_stage or upload_counter < previous_upload:
                upload_speed = 0.0
                upload_moved_at = now_monotonic
            elif elapsed > 0 and upload_counter > previous_upload:
                measured = (upload_counter - previous_upload) / elapsed
                upload_speed = measured if old_upload_speed <= 0 else old_upload_speed * 0.7 + measured * 0.3
                upload_moved_at = now_monotonic
            else:
                upload_moved_at = previous_upload_moved or now_monotonic
                upload_speed = 0.0 if now_monotonic - upload_moved_at >= 4 else old_upload_speed

            value.download_speed = download_speed
            value.upload_speed = upload_speed
            value.download_eta = (
                max(0.0, (download_total - download_counter) / download_speed)
                if download_total > download_counter and download_speed > 0 else None
            )
            value.upload_eta = (
                max(0.0, (upload_total - upload_counter) / upload_speed)
                if upload_total > upload_counter and upload_speed > 0 else None
            )
            value.last_updated_at = now_wall
            items = [item for item in getattr(value, "torrent_items", []) if isinstance(item, dict)]
            value.file_counts = _file_progress_counts(items) if value.ingestion_type else {}
            await cls.redis.hset(key, mapping={
                "name": value.name,
                "message": value.message,
                "transfer": value.transfer,
                "total": value.total,
                "download_transfer": int(getattr(value, "download_transfer", 0) or 0),
                "download_total": int(getattr(value, "download_total", 0) or 0),
                "download_active": int(bool(getattr(value, "download_active", False))),
                "download_speed": value.download_speed,
                "upload_speed": value.upload_speed,
                "download_eta": -1 if value.download_eta is None else value.download_eta,
                "upload_eta": -1 if value.upload_eta is None else value.upload_eta,
                "ingestion_type": value.ingestion_type,
                "file_counts": dumps(value.file_counts),
                "last_updated_at": value.last_updated_at,
                "metric_stage": stage,
                "metric_sample_at": now_monotonic,
                "metric_download_counter": download_counter,
                "metric_upload_counter": upload_counter,
                "metric_download_moved_at": download_moved_at,
                "metric_upload_moved_at": upload_moved_at,
                "torrent_items": dumps(getattr(value, "torrent_items", [])),
            })

        elif isinstance(value, User):
            with POOL.connection() as connection:
                directories: list[DirectoryDict] = connection.execute(
                    """
                    SELECT id, path, modified_at, deleted_at, username
                    FROM directories
                    WHERE username = %s;
                    """,
                    (value.username,),
                ).fetchall()

                files: list[FileDict] = connection.execute(
                    """
                    SELECT id,
                           directory_id,
                           name,
                           type,
                           size,
                           modified_at,
                           data_center,
                           links,
                           deleted_at,
                           username
                    FROM files
                    WHERE username = %s;
                    """,
                    (value.username,),
                ).fetchall()

            await cls.redis.hset(key, mapping={
                "username": value.username,
                "password": value.password,
                "first_name": value.first_name,
                "last_name": value.last_name,
                "created_at": value.created_at.isoformat(),
            })

            await cls.redis.delete(f"{key}:directories", f"{key}:files", )

            if directories:
                await cls.redis.hset(
                    f"{key}:directories",
                    mapping={str(directory["id"]): dumps(directory, default=str) for directory in directories}
                )

            if files:
                await cls.redis.hset(f"{key}:files", mapping={str(file["id"]): dumps(file, default=str) for file in files})

        elif isinstance(value, Directory):
            if value.id is None:
                raise ValueError("Directory has no ID")

            await cls.redis.hset(
                key,
                str(value.id),
                dumps(
                    {
                        "id": value.id,
                        "path": str(value.path),
                        "modified_at": value.modified_at,
                        "deleted_at": value.deleted_at,
                        "username": value.username,
                    },
                    default=str,
                ),
            )

        elif isinstance(value, File):
            if value.id is None:
                raise ValueError("File has no ID")

            await cls.redis.hset(
                key,
                str(value.id),
                dumps(
                    {
                        "id": value.id,
                        "directory_id": value.directory_id,
                        "name": value.name,
                        "type": value.type,
                        "size": value.size,
                        "modified_at": value.modified_at,
                        "deleted_at": value.deleted_at,
                        "data_center": value.data_center,
                        "links": value.links,
                        "username": value.username,
                    },
                    default=str,
                ),
            )
