from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from psycopg import Cursor
from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import CONNECTION, DirectoryDict
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

    def save(self) -> None:
        if Directory.get(path=self.path, username=self.username) is not None:
            parent: Path = self.path.parent
            name: str = self.path.name
            i = 1

            while Directory.get(path=parent / name, username=self.username, ) is not None:
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

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "INSERT DIRECTORY", self.username, f"Failed to insert directory: {e}")
            raise

    @classmethod
    def get(cls, *,
            did: int | None = None,
            path: Path | None = None,
            username: str | None = None,
            include_trashed: bool = False,
            trashed_only: bool = False) -> "Directory | None":
        if trashed_only:
            trash_clause: str = "AND deleted_at IS NOT NULL"

        elif not include_trashed:
            trash_clause: str = "AND deleted_at IS NULL"

        else:
            trash_clause: str = ""

        if did is not None:
            cursor: Cursor[DirectoryDict] = CONNECTION.execute(
                f"""
                SELECT id, path, modified_at, deleted_at, username
                FROM directories
                WHERE id = %s
                  {trash_clause};
                """,
                (did,),
            )

        elif path is not None and username is not None:
            cursor: Cursor[DirectoryDict] = CONNECTION.execute(
                f"""
                SELECT id, path, modified_at, deleted_at, username
                FROM directories
                WHERE path = %s
                  AND username = %s
                  {trash_clause};
                """,
                (
                    str(path),
                    username
                ),
            )

        else:
            return None

        row: DirectoryDict | None = cursor.fetchone()

        if row is None:
            return None

        data: dict[str, int | str | Path | datetime | None] = row
        data["path"] = Path(cast(str, data["path"]))
        return cls(**data)

    @classmethod
    def get_all(cls, username: str, *, directory: Path | None = None, include_trashed: bool = False, trashed_only: bool = False) -> list["Directory"]:
        if trashed_only:
            trash_clause: str = "AND deleted_at IS NOT NULL"

        elif not include_trashed:
            trash_clause: str = "AND deleted_at IS NULL"

        else:
            trash_clause: str = ""

        if directory is not None:
            directory_string: str = str(directory).strip("/")
            path_clause: str = "AND path LIKE %s AND path NOT LIKE %s"
            parameters: tuple[str, ...] = (f"/{directory_string}/%", f"/{directory_string}/%/%")

        else:
            path_clause: str = ""
            parameters: tuple[str, ...] = tuple()

        rows: list[DirectoryDict] = CONNECTION.execute(
            f"""
            SELECT id, path, modified_at, deleted_at, username
            FROM directories
            WHERE username = %s
              {path_clause}
              {trash_clause}
            ORDER BY path;
            """,
            (username, *parameters),
        ).fetchall()
        directories: list[Directory] = []

        for row in rows:
            data: dict[str, int | str | Path | datetime | None] = row
            data["path"] = Path(cast(str, data["path"]))
            directories.append(cls(**data))

        return directories

    def update(self) -> None:
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

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "UPDATE DIRECTORY", self.username, f"Failed to update directory: {e}")
            raise

    def move_to_trash(self) -> None:
        if self.id is None:
            raise ValueError("Directory has no ID")

        self.modified_at = datetime.now(timezone.utc)
        self.deleted_at = self.modified_at
        self.update()

    def delete(self) -> None:
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

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "DELETE DIRECTORY", str(self.id), f"Failed to delete directory: {e}")
            raise

    def restore(self) -> None:
        if self.id is None:
            raise ValueError("Directory has no ID")

        self.deleted_at = None
        self.modified_at = datetime.now(timezone.utc)
        self.update()
