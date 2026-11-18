"""
Directory model and persistence operations. A directory is represented by a path owned by a user; directory metadata is stored in PostgreSQL and
cached in Redis.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import POOL
from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import write_log


class Directory(BaseModel):
    """Represent a user-owned directory, including its path, modification time, and optional trash timestamp."""

    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    path: Path
    modified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None
    username: str

    def __repr__(self) -> str:
        """Return a readable representation of the directory metadata."""

        return (
            f"Directory(id={self.id}, "
            f"path={self.path!r}, "
            f"modified_at={self.modified_at}, "
            f"deleted_at={self.deleted_at}, "
            f"username={self.username!r})"
        )

    async def save(self) -> None:
        """Insert the directory and cache it in Redis. If its path already exists for the user, append a numbered suffix to make the path unique."""

        if await Directory.get(self.username, path=self.path) is not None:
            parent: Path = self.path.parent
            name: str = self.path.name
            i: int = 1

            while await Directory.get(self.username, path=parent / name) is not None:
                name = f"{self.path.name}({i})"
                i += 1

            self.path = parent / name

        with POOL.connection() as connection:
            try:
                self.id = cast(dict[str, int], connection.execute(
                    """
                    INSERT INTO directories (path, modified_at, deleted_at, username)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (str(self.path), self.modified_at, self.deleted_at, self.username),
                ).fetchone())["id"]
                connection.commit()
                await Redis.set(f"user:{self.username}:directories", self)

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "INSERT DIRECTORY", self.username, f"Failed to insert directory: {e}")
                raise

    @classmethod
    async def get(
            cls,
            username: str,
            *,
            did: int | None = None,
            path: Path | None = None,
            trash: bool = False
    ) -> "Directory | None":
        """Retrieve a directory by ID or path and filter by the requested trash state. Return None when no matching directory exists."""

        if did is not None:
            try:
                directory: Directory = cast(Directory, await Redis.get(f"user:{username}:directories {did}", "Directory"))

            except KeyError:
                return None

            if trash != (directory.deleted_at is not None):
                return None

            return directory

        elif path is not None:
            directories: list[Directory] = cast(list[Directory], await Redis.get(f"user:{username}:directories", "Directory"))

            for directory in directories:
                if directory.path != path or trash != (directory.deleted_at is not None):
                    continue

                return directory

        return None

    @classmethod
    async def get_all(
            cls,
            username: str,
            *,
            directory: Path | None = None,
            directory_name: str | None = None,
            trash: bool = False
    ) -> list["Directory"]:
        """Return a user’s directories, optionally filtered to direct children of a path, by case-insensitive name substring, and by trash state."""

        directories: list[Directory] = cast(list[Directory], await Redis.get(f"user:{username}:directories", "Directory"))
        query: str | None = directory_name.strip().casefold() if directory_name else None
        result: list[Directory] = []

        for data in directories:
            if trash != (data.deleted_at is not None):
                continue

            if directory is not None and data.path.parent != directory:
                continue

            if query is not None and query not in data.path.name.casefold():
                continue

            result.append(data)

        result.sort(key=lambda item: str(item.path).casefold())
        return result

    async def update(self) -> None:
        """Persist the directory path and state to PostgreSQL and update its Redis cache entry. Raise ValueError if the directory has no ID."""

        if self.id is None:
            raise ValueError("Directory has no ID")

        with POOL.connection() as connection:
            try:
                connection.execute(
                    """
                    UPDATE directories
                    SET path        = %s,
                        modified_at = %s,
                        deleted_at  = %s
                    WHERE id = %s;
                    """,
                    (str(self.path), self.modified_at, self.deleted_at, self.id),
                )
                connection.commit()
                await Redis.set(f"user:{self.username}:directories", self)

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "UPDATE DIRECTORY", self.username, f"Failed to update directory: {e}")
                raise

    async def move_to_trash(self) -> None:
        """Mark the directory as deleted by setting deleted_at and modified_at to the current UTC time, then persist the change."""

        if self.id is None:
            raise ValueError("Directory has no ID")

        self.modified_at = datetime.now(timezone.utc)
        self.deleted_at = self.modified_at
        await self.update()

    async def delete(self) -> None:
        """Delete the directory and any remaining file rows that reference it, then clear associated Redis entries."""

        if self.id is None:
            raise ValueError("Directory has no ID")

        with POOL.connection() as connection:
            try:
                owned_directory: dict[str, int] | None = connection.execute(
                    """
                    SELECT id
                    FROM directories
                    WHERE id = %s
                      AND username = %s
                        FOR UPDATE;
                    """,
                    (self.id, self.username),
                ).fetchone()

                if owned_directory is None:
                    connection.commit()
                    await Redis.delete(f"user:{self.username}:directories {self.id}", "Directory")
                    return

                removed_files: list[dict[str, int | str]] = connection.execute(
                    """
                    DELETE
                    FROM files
                    WHERE directory_id = %s
                    RETURNING id, username;
                    """,
                    (self.id,),
                ).fetchall()
                connection.execute(
                    """
                    DELETE
                    FROM directories
                    WHERE id = %s
                      AND username = %s;
                    """,
                    (self.id, self.username),
                )
                connection.commit()

                for removed_file in removed_files:
                    await Redis.delete(f"user:{removed_file['username']}:files {removed_file['id']}", "File")

                await Redis.delete(f"user:{self.username}:directories {self.id}", "Directory")

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "DELETE DIRECTORY", str(self.id), f"Failed to delete directory: {e}")
                raise

    async def restore(self) -> None:
        """Restore a trashed directory by clearing deleted_at, updating modified_at, and persisting the change."""

        if self.id is None:
            raise ValueError("Directory has no ID")

        self.deleted_at = None
        self.modified_at = datetime.now(timezone.utc)
        await self.update()
