"""
File model and persistence operations. File metadata is stored in PostgreSQL and mirrored in Redis for lookup, listing, trash, and restore operations.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import POOL
from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import write_log


_VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".asf", ".avi", ".divx", ".flv", ".m2ts", ".m2v", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpe", ".mpeg", ".mpg", ".mts", ".mxf", ".ogv",
    ".rm", ".rmvb", ".ts", ".vob", ".webm", ".wmv",
}


def is_video_file(name: str, mime_type: str = "") -> bool:
    """Return whether a file's stored MIME type or extension identifies video media."""

    if str(mime_type or "").split(";", 1)[0].strip().lower().startswith("video/"):
        return True

    return Path(str(name or "")).suffix.lower() in _VIDEO_EXTENSIONS


class File(BaseModel):
    """Represent a stored file, including its parent directory, content metadata, data-center links, owner, and trash state."""
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
        """Return a readable representation of the file metadata."""

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
        """
        Insert the file metadata, choosing a numbered name when the target directory already contains the same name. Cache the saved file in Redis.
        """

        if await self.get(self.username, did=self.directory_id, name=self.name):
            path: Path = Path(self.name)
            stem, extension = path.stem, path.suffix
            i = 1

            while await self.get(self.username, did=self.directory_id, name=f"{stem}({i}){extension}"):
                i += 1

            self.name = f"{stem}({i}){extension}"

        with POOL.connection() as connection:
            try:
                self.id = cast(dict[str, int], connection.execute(
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

                # Queue registration shares the original-file transaction, so all
                # ingestion paths converge here after the provider upload has finished.
                if self.id is not None and is_video_file(self.name, self.type):
                    connection.execute(
                        """
                        INSERT INTO video_processing_jobs (file_id)
                        VALUES (%s)
                        ON CONFLICT (file_id) DO NOTHING;
                        """,
                        (self.id,),
                    )

                connection.commit()
                await Redis.set(f"user:{self.username}:files", self)

            except Exception as e:
                connection.rollback()
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
            trash: bool = False
    ) -> "File | None":
        """Retrieve one file by ID or by directory ID and name, respecting the requested trash state. Return None when no matching file exists."""

        if fid is not None:
            try:
                file: File = cast(File, await Redis.get(f"user:{username}:files {fid}", "File"))

            except KeyError:
                return None

            if trash != (file.deleted_at is not None):
                return None

            return file

        elif did is not None and name is not None:
            files: list[File] = cast(list[File], await Redis.get(f"user:{username}:files", "File"))

            for file in files:
                if file.directory_id != did or file.name != name or trash != (file.deleted_at is not None):
                    continue

                return file

        return None

    @classmethod
    async def get_all(cls, username: str, *, directory_id: int | None = None, file_name: str | None = None, trash: bool = False) -> list["File"]:
        """Return the user’s files, optionally filtered by directory, case-insensitive name substring, and trash state."""

        files: list[File] = cast(list[File], await Redis.get(f"user:{username}:files", "File"))
        query: str | None = file_name.strip().casefold() if file_name else None
        result: list[File] = []

        for file in files:
            if trash != (file.deleted_at is not None):
                continue

            if directory_id is not None and file.directory_id != directory_id:
                continue

            if query is not None and query not in file.name.casefold():
                continue

            result.append(file)

        result.sort(key=lambda file: file.name.casefold())
        return result

    async def update(self) -> None:
        """Persist the file’s mutable metadata to PostgreSQL and update its Redis cache entry. Raise ValueError if the file has no ID."""

        if self.id is None:
            raise ValueError("File has no ID")

        with POOL.connection() as connection:
            try:
                connection.execute(
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
                connection.commit()
                await Redis.set(f"user:{self.username}:files", self)

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "UPDATE FILE", self.username, f"Failed to update file: {e}")
                raise

    async def move_to_trash(self) -> None:
        """Mark the file as deleted by setting deleted_at and modified_at to the current UTC time, then persist the change."""

        if self.id is None:
            raise ValueError("File has no ID")

        self.modified_at = datetime.now(timezone.utc)
        self.deleted_at = self.modified_at
        await self.update()

    async def delete(self) -> None:
        """Permanently delete the file record from PostgreSQL and remove its individual Redis cache entry."""

        if self.id is None:
            raise ValueError("File has no ID")

        with POOL.connection() as connection:
            try:
                connection.execute(
                    """
                    DELETE
                    FROM files
                    WHERE id = %s;
                    """,
                    (self.id,),
                )
                connection.commit()
                await Redis.delete(f"user:{self.username}:files {self.id}", "File")

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "DELETE FILE", str(self.id), f"Failed to delete file: {e}")
                raise

    async def restore(self) -> None:
        """Restore a trashed file by clearing deleted_at, updating modified_at, and persisting the change."""

        if self.id is None:
            raise ValueError("File has no ID")

        self.deleted_at = None
        self.modified_at = datetime.now(timezone.utc)
        await self.update()

    @classmethod
    async def purge_expired(cls, username: str, older_than_days: int = 30) -> None:
        """Permanently delete files that have remained in the user’s trash longer than older_than_days, removing their Redis entries too."""

        files: list[File] = cast(list[File], await Redis.get(f"user:{username}:files", "File"))
        cutoff: datetime = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        expired: list[File] = [file for file in files if file.deleted_at is not None and file.deleted_at < cutoff]

        if not expired:
            return

        with POOL.connection() as connection:
            try:

                connection.execute(
                    """
                    DELETE
                    FROM files
                    WHERE username = %s
                      AND id = ANY (%s);
                    """,
                    (
                        username,
                        [file.id for file in expired],
                    ),
                )
                connection.commit()

                for file in expired:
                    await Redis.delete(f"user:{username}:files {file.id}", "File")

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "PURGE FILE TRASH", username, f"Failed to purge file trash: {e}")
                raise
