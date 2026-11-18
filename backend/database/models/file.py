from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import CONNECTION
from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import write_log


class File(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    directory_id: int
    name: str
    type: str
    size: int
    modified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None
    data_center: str
    links: list[str]
    username: str

    def __repr__(self) -> str:
        return (
            f"File(id={self.id}, "
            f"directory_id={self.directory_id}, "
            f"name={self.name!r}, "
            f"type={self.type}, "
            f"size={self.size}, "
            f"modified_at={self.modified_at}, "
            f"deleted_at={self.deleted_at}, "
            f"data_center={self.data_center!r}, "
            f"links={self.links!r}, "
            f"username={self.username!r})"
        )

    async def save(self) -> None:
        if await self.get(did=self.directory_id, name=self.name, username=self.username):
            path: Path = Path(self.name)
            stem, extension = path.stem, path.suffix
            i = 1

            while await self.get(did=self.directory_id, name=f"{stem}({i}){extension}", username=self.username):
                i += 1

            self.name = f"{stem}({i}){extension}"

        try:
            self.id = cast(dict[str, int], CONNECTION.execute(
                """
                INSERT INTO files (directory_id, name, type, size, modified_at, data_center, links, deleted_at, username)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    self.directory_id,
                    self.name,
                    self.type,
                    self.size,
                    self.modified_at,
                    self.data_center,
                    self.links,
                    self.deleted_at,
                    self.username,
                ),
            ).fetchone())["id"]
            CONNECTION.commit()
            await Redis.set(f"user:{self.username}:files", self)

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "INSERT FILE", self.username, f"Failed to insert file: {e}")
            raise

    @classmethod
    async def get(
            cls,
            username: str,
            *,
            fid: int | None = None,
            did: int | None = None,
            name: str | None = None,
            include_trashed: bool = False,
            trashed_only: bool = False
    ) -> "File | None":
        if fid is not None:
            try:
                file: File = cast(File, await Redis.get(f"user:{username}:files {fid}", "File"))

            except KeyError:
                return None

            if file.deleted_at is not None and not include_trashed:
                return None

            if file.deleted_at is None and trashed_only:
                return None

            return file

        elif did is not None and name is not None:
            files: list[File] = cast(list[File], await Redis.get(f"user:{username}:files", "File"))

            for file in files:
                if file.directory_id != did or file.name != name:
                    continue

                if file.deleted_at is not None and not include_trashed:
                    continue

                if file.deleted_at is None and trashed_only:
                    continue

                return file

        return None

    @classmethod
    async def get_all(
            cls,
            username: str,
            *,
            directory_id: int | None = None,
            include_trashed: bool = False,
            trashed_only: bool = False
    ) -> list["File"]:
        files: list[File] = cast(list[File], await Redis.get(f"user:{username}:files", "File"))
        result: list[File] = []

        for file in files:
            if file.deleted_at is not None and not include_trashed:
                continue

            if file.deleted_at is None and trashed_only:
                continue

            if directory_id is not None and file.directory_id != directory_id:
                continue

            result.append(file)

        result.sort(key=lambda file: file.name)
        return result

    async def update(self) -> None:
        if self.id is None:
            raise ValueError("File has no ID")

        try:
            CONNECTION.execute(
                """
                UPDATE files
                SET directory_id = %s,
                    name         = %s,
                    type         = %s,
                    size         = %s,
                    modified_at  = %s,
                    deleted_at   = %s,
                    data_center  = %s,
                    links        = %s
                WHERE id = %s;
                """,
                (
                    self.directory_id,
                    self.name,
                    self.type,
                    self.size,
                    self.modified_at,
                    self.deleted_at,
                    self.data_center,
                    self.links,
                    self.id,
                ),
            )
            CONNECTION.commit()
            await Redis.set(f"user:{self.username}:files", self)

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "UPDATE FILE", self.username, f"Failed to update file: {e}")
            raise

    async def move_to_trash(self) -> None:
        if self.id is None:
            raise ValueError("File has no ID")

        self.modified_at = datetime.now(timezone.utc)
        self.deleted_at = self.modified_at
        await self.update()

    async def delete(self) -> None:
        if self.id is None:
            raise ValueError("File has no ID")

        try:
            CONNECTION.execute(
                """
                DELETE
                FROM files
                WHERE id = %s;
                """,
                (self.id,),
            )
            CONNECTION.commit()
            await Redis.delete(f"user:{self.username}:files {self.id}", "File")

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "DELETE FILE", str(self.id), f"Failed to delete file: {e}")
            raise

    async def restore(self) -> None:
        if self.id is None:
            raise ValueError("File has no ID")

        self.deleted_at = None
        self.modified_at = datetime.now(timezone.utc)
        await self.update()

    @classmethod
    async def purge_expired(cls, username: str, older_than_days: int = 30) -> None:
        try:
            files: list[File] = cast(list[File], await Redis.get(f"user:{username}:files", "File"))
            cutoff: datetime = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            expired: list[File] = [file for file in files if file.deleted_at is not None and file.deleted_at < cutoff]

            if not expired:
                return

            CONNECTION.execute(
                """
                DELETE
                FROM files
                WHERE username = %s
                  AND id = ANY(%s);
                """,
                (
                    username,
                    [file.id for file in expired],
                ),
            )
            CONNECTION.commit()

            for file in expired:
                await Redis.delete(f"user:{username}:files {file.id}", "File")

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "PURGE FILE TRASH", username, f"Failed to purge file trash: {e}")
            raise
