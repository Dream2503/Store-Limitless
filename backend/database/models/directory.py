from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import CONNECTION
from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import write_log


class Directory(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    path: Path
    modified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None
    username: str

    def __repr__(self) -> str:
        return (
            f"Directory(id={self.id}, "
            f"path={self.path!r}, "
            f"modified_at={self.modified_at}, "
            f"deleted_at={self.deleted_at}, "
            f"username={self.username!r})"
        )

    async def save(self) -> None:
        if await Directory.get(path=self.path, username=self.username) is not None:
            parent: Path = self.path.parent
            name: str = self.path.name
            i: int = 1

            while await Directory.get(path=parent / name, username=self.username) is not None:
                name = f"{self.path.name}({i})"
                i += 1

            self.path = parent / name

        try:
            self.id = cast(dict[str, int], CONNECTION.execute(
                """
                INSERT INTO directories (path, modified_at, deleted_at, username)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (str(self.path), self.modified_at, self.deleted_at, self.username),
            ).fetchone())["id"]
            CONNECTION.commit()
            await Redis.set(f"user:{self.username}:directories", self)

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "INSERT DIRECTORY", self.username, f"Failed to insert directory: {e}")
            raise

    @classmethod
    async def get(
            cls,
            username: str,
            *,
            did: int | None = None,
            path: Path | None = None,
            include_trashed: bool = False,
            trashed_only: bool = False
    ) -> "Directory | None":
        if did is not None:
            try:
                directory: Directory = cast(Directory, await Redis.get(f"user:{username}:directories {did}", "Directory"))

            except KeyError:
                return None

            if directory.deleted_at is not None and not include_trashed:
                return None

            if directory.deleted_at is None and trashed_only:
                return None

            return directory

        elif path is not None and username is not None:
            directories: list[Directory] = cast(list[Directory], await Redis.get(f"user:{username}:directories", "Directory"))

            for directory in directories:
                if directory.path != path:
                    continue

                if directory.deleted_at is not None and not include_trashed:
                    continue

                if directory.deleted_at is None and trashed_only:
                    continue

                return directory

        return None

    @classmethod
    async def get_all(
            cls,
            username: str,
            *,
            directory: Path | None = None,
            include_trashed: bool = False,
            trashed_only: bool = False
    ) -> list["Directory"]:
        directories: list[Directory] = cast(list[Directory], await Redis.get(f"user:{username}:directories", "Directory"))
        result: list[Directory] = []

        for data in directories:
            if data.deleted_at is not None and not include_trashed:
                continue

            if data.deleted_at is None and trashed_only:
                continue

            if directory is not None:
                if data.path.parent != directory:
                    continue

            result.append(data)

        result.sort(key=lambda directory: str(directory.path))
        return result

    async def update(self) -> None:
        if self.id is None:
            raise ValueError("Directory has no ID")

        try:
            CONNECTION.execute(
                """
                UPDATE directories
                SET path        = %s,
                    modified_at = %s,
                    deleted_at  = %s
                WHERE id = %s;
                """,
                (
                    str(self.path),
                    self.modified_at,
                    self.deleted_at,
                    self.id,
                ),
            )
            CONNECTION.commit()
            await Redis.set(f"user:{self.username}:directories", self)

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "UPDATE DIRECTORY", self.username, f"Failed to update directory: {e}")
            raise

    async def move_to_trash(self) -> None:
        if self.id is None:
            raise ValueError("Directory has no ID")

        self.modified_at = datetime.now(timezone.utc)
        self.deleted_at = self.modified_at
        await self.update()

    async def delete(self) -> None:
        if self.id is None:
            raise ValueError("Directory has no ID")

        try:
            CONNECTION.execute(
                """
                DELETE
                FROM directories
                WHERE id = %s
                  AND username = %s;
                """,
                (self.id, self.username),
            )
            CONNECTION.commit()

            await Redis.delete(f"user:{self.username}:directories {self.id}", "Directory")

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "DELETE DIRECTORY", str(self.id), f"Failed to delete directory: {e}")
            raise

    async def restore(self) -> None:
        if self.id is None:
            raise ValueError("Directory has no ID")

        self.deleted_at = None
        self.modified_at = datetime.now(timezone.utc)
        await self.update()
