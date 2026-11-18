from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from psycopg import Cursor
from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import CONNECTION, FileDict
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

    def save(self) -> None:
        if self.get(did=self.directory_id, name=self.name, username=self.username):
            path: Path = Path(self.name)
            stem, extension = path.stem, path.suffix
            i = 1

            while self.get(did=self.directory_id, name=f"{stem}({i}){extension}", username=self.username):
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

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "INSERT FILE", self.username, f"Failed to insert file: {e}")
            raise

    @classmethod
    def get(cls, *,
            fid: int | None = None,
            did: int | None = None,
            name: str | None = None,
            username: str | None = None,
            include_trashed: bool = False,
            trashed_only: bool = False) -> "File | None":
        if trashed_only:
            trash_clause: str = "AND deleted_at IS NOT NULL"

        elif not include_trashed:
            trash_clause: str = "AND deleted_at IS NULL"

        else:
            trash_clause: str = ""

        if fid is not None:
            cursor: Cursor[FileDict] = CONNECTION.execute(
                f"""
                SELECT id, directory_id, name, type, size, modified_at, data_center, links, deleted_at, username
                FROM files
                WHERE id = %s 
                  {trash_clause};
                """,
                (fid,),
            )

        elif did is not None and name is not None and username is not None:
            cursor: Cursor[FileDict] = CONNECTION.execute(
                f"""
                SELECT id, directory_id, name, type, size, modified_at, data_center, links, deleted_at, username
                FROM files
                WHERE directory_id = %s 
                  AND name = %s 
                  AND username = %s 
                  {trash_clause};
                """,
                (
                    did,
                    name,
                    username
                ),
            )

        else:
            return None

        row: FileDict | None = cursor.fetchone()

        if row is None:
            return None

        return cls(**row)

    @classmethod
    def get_all(cls, username: str, *, directory_id: int | None = None, include_trashed: bool = False, trashed_only: bool = False, ) -> list["File"]:
        if trashed_only:
            trash_clause: str = "AND deleted_at IS NOT NULL"

        elif not include_trashed:
            trash_clause: str = "AND deleted_at IS NULL"

        else:
            trash_clause: str = ""

        if directory_id is not None:
            cursor: Cursor[FileDict] = CONNECTION.execute(
                f"""
                SELECT id, directory_id, name, type, size, modified_at, data_center, links, deleted_at, username
                FROM files
                WHERE directory_id = %s 
                  AND username = %s 
                  {trash_clause}
                ORDER BY name;
                """,
                (directory_id, username),
            )
        else:
            cursor: Cursor[FileDict] = CONNECTION.execute(
                f"""
                SELECT id, directory_id, name, type, size, modified_at, data_center, links, deleted_at, username
                FROM files
                WHERE username = %s 
                  {trash_clause}
                ORDER BY name;
                """,
                (username,),
            )

        row: FileDict
        files: list[File] = []

        for row in cursor.fetchall():
            files.append(cls(**row))

        return files

    def update(self) -> None:
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

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "UPDATE FILE", self.username, f"Failed to update file: {e}")
            raise

    def move_to_trash(self) -> None:
        if self.id is None:
            raise ValueError("File has no ID")

        self.modified_at = datetime.now(timezone.utc)
        self.deleted_at = self.modified_at
        self.update()

    def delete(self) -> None:
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

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "DELETE FILE", str(self.id), f"Failed to delete file: {e}")
            raise

    def restore(self) -> None:
        if self.id is None:
            raise ValueError("File has no ID")

        self.deleted_at = None
        self.modified_at = datetime.now(timezone.utc)
        self.update()

    @classmethod
    def purge_expired(cls, username: str, older_than_days: int = 30) -> None:
        try:
            CONNECTION.execute(
                """
                DELETE
                FROM files
                WHERE username = %s
                  AND deleted_at IS NOT NULL
                  AND deleted_at < %s;
                """,
                (
                    username,
                    datetime.now(timezone.utc) - timedelta(days=older_than_days),
                ),
            )
            CONNECTION.commit()

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "PURGE FILE TRASH", username, f"Failed to purge file trash: {e}")
            raise
