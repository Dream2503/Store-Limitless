"""Redis connection and serialization helpers for cached users, directories, files, progress records, and JSON-based application state."""

from __future__ import annotations

from datetime import datetime
from json import dumps, loads
from pathlib import Path
from typing import Any, Literal, cast, TYPE_CHECKING

import redis.asyncio

from backend.database.connection import POOL, DirectoryDict, FileDict
from core.utils.progress import Progress

if TYPE_CHECKING:
    from backend.database.models import User, Directory, File


class Redis:
    """Manage the shared asynchronous Redis client and serialize application model objects to and from Redis keys and hashes."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Redis:
        """Prevent instantiation because Redis exposes class-level operations only."""

        raise TypeError("Redis cannot be instantiated")

    ValueTypes = Literal["Progress", "Directory", "File", "User"]
    redis: redis.asyncio.Redis

    @classmethod
    async def initialize(cls) -> None:
        """Create the asynchronous Redis client and verify connectivity with PING. Existing Redis data is intentionally preserved."""

        cls.redis = redis.asyncio.Redis.from_url("redis://localhost:6379", decode_responses=True)
        await cls.redis.ping()

    @classmethod
    async def close(cls) -> None:
        """Close the asynchronous Redis client connection pool."""

        await cls.redis.aclose()

    @classmethod
    async def get_json(cls, key: str) -> Any | None:
        """Read an arbitrary JSON-serializable value stored under a Redis key."""

        value: str | None = await cls.redis.get(key)
        return loads(value) if value is not None else None

    @classmethod
    async def set_json(cls, key: str, value: Any, *, ex: int | None = None, nx: bool = False) -> bool:
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
                raw_torrent_items: str = data.get("torrent_items", "[]")

                try:
                    torrent_items: Any = loads(raw_torrent_items)
                    progress.torrent_items = torrent_items if isinstance(torrent_items, list) else []

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

                records: list[dict[str, Any]] = [loads(value) for value in values]

                if type == "Directory":
                    items: list[Directory] | list[File] = [
                        Directory(
                            id=cast(int, record["id"]),
                            path=Path(cast(str, record["path"])),
                            modified_at=datetime.fromisoformat(cast(str, record["modified_at"])),
                            deleted_at=datetime.fromisoformat(cast(str, record["deleted_at"])) if record["deleted_at"] else None,
                            username=cast(str, record["username"]),
                        )
                        for record in records
                    ]

                else:
                    items = [
                        File(
                            id=cast(int, record["id"]),
                            directory_id=cast(int, record["directory_id"]),
                            name=cast(str, record["name"]),
                            type=cast(str, record["type"]),
                            size=cast(int, record["size"]),
                            modified_at=datetime.fromisoformat(cast(str, record["modified_at"])),
                            deleted_at=datetime.fromisoformat(cast(str, record["deleted_at"])) if record["deleted_at"] else None,
                            data_center=cast(str, record["data_center"]),
                            links=cast(list[str], record["links"]),
                            username=cast(str, record["username"]),
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
            await cls.redis.hset(key, mapping={
                "name": value.name,
                "message": value.message,
                "transfer": value.transfer,
                "total": value.total,
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
