from __future__ import annotations

from datetime import datetime
from json import dumps, loads
from pathlib import Path
from typing import Literal, cast, TYPE_CHECKING

import redis.asyncio

from backend.database.connection import CONNECTION, DirectoryDict, FileDict
from core.utils.progress import Progress

if TYPE_CHECKING:
    from backend.database.models import User, Directory, File


class Redis:
    ValueTypes = Literal["Progress", "Directory", "File", "User"]
    redis: redis.asyncio.Redis = redis.asyncio.Redis.from_url("redis://localhost:6379", decode_responses=True)

    @classmethod
    async def exists(cls, key: str, type: ValueTypes) -> bool:
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
        from backend.database.models import User, Directory, File

        match type:
            case "Progress":
                data: dict[str, str] = await cls.redis.hgetall(key)

                if not data:
                    raise KeyError(key)

                progress: Progress = Progress(data["message"], int(data["total"]))
                progress.transfer = int(data["transfer"])
                return progress

            case "Directory":
                if " " in key:
                    did: str
                    key, did = key.rsplit(" ", 1)
                    value: str | None = await cls.redis.hget(key, did)

                    if value is None:
                        raise KeyError(did)

                    data: list[str] = [value]
                    single: bool = True

                else:
                    data: list[str] = list((await cls.redis.hgetall(key)).values())
                    single: bool = False

                    if not data:
                        return []

                directories: list[Directory] = []
                value: str

                for value in data:
                    directory: dict[str, int | str | None] = loads(value)
                    directories.append(Directory(
                        id=cast(int, directory["id"]),
                        path=Path(cast(str, directory["path"])),
                        modified_at=datetime.fromisoformat(cast(str, directory["modified_at"])),
                        deleted_at=datetime.fromisoformat(cast(str, directory["deleted_at"])) if directory["deleted_at"] else None,
                        username=cast(str, directory["username"])
                    ))

                return directories[0] if single else directories

            case "File":
                if " " in key:
                    fid: str
                    key, fid = key.rsplit(" ", 1)
                    value: str | None = await cls.redis.hget(key, fid)

                    if value is None:
                        raise KeyError(fid)

                    data: list[str] = [value]
                    single: bool = True

                else:
                    data: list[str] = list((await cls.redis.hgetall(key)).values())
                    single: bool = False

                    if not data:
                        return []

                files: list[File] = []
                value: str

                for value in data:
                    file: dict[str, int | str | list[str] | None] = loads(value)
                    files.append(File(
                        id=cast(int, file["id"]),
                        directory_id=cast(int, file["directory_id"]),
                        name=cast(str, file["name"]),
                        type=cast(str, file["type"]),
                        size=cast(int, file["size"]),
                        modified_at=datetime.fromisoformat(cast(str, file["modified_at"])),
                        deleted_at=datetime.fromisoformat(cast(str, file["deleted_at"])) if file["deleted_at"] else None,
                        data_center=cast(str, file["data_center"]),
                        links=cast(list[str], file["links"]),
                        username=cast(str, file["username"])
                    ))

                return files[0] if single else files

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
        from backend.database.models import User, Directory, File

        if isinstance(value, Progress):
            await cls.redis.hset(key, mapping={"message": value.message, "transfer": value.transfer, "total": value.total})

        elif isinstance(value, User):
            directories: list[DirectoryDict] = CONNECTION.execute(
                """
                SELECT id, path, modified_at, deleted_at, username
                FROM directories
                WHERE username = %s;
                """,
                (value.username,),
            ).fetchall()

            files: list[FileDict] = CONNECTION.execute(
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
