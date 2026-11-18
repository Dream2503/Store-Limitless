from datetime import datetime, timezone, timedelta
from json import loads, dumps
from pathlib import Path
from sqlite3 import Row
from typing import cast

from core.data_center import Database
from core.utils import write_log
from .connection import CURSOR
from .schema import File, User


def add_user(user: User) -> None:
    try:
        CURSOR.execute(
            """
            INSERT INTO users (username, password, first_name, last_name, created_at)
            VALUES (?, ?, ?, ?, ?);
            """, (user.username, user.password, user.first_name, user.last_name, user.created_at.isoformat()),
        ).connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "SET USER", user.username, f"Failed to insert user: {e}")
        raise


def get_user(username: str) -> User | None:
    CURSOR.execute(
        """
        SELECT username, password, first_name, last_name, created_at
        FROM users
        WHERE username = ?;
        """,
        (username,),
    )
    row: Row | None = CURSOR.fetchone()

    if row:
        user: dict[str, str | datetime] = dict(row)
        user["created_at"] = datetime.fromisoformat(cast(str, user["created_at"]))
        return User(**user)

    write_log("ERROR", Database, "GET USER", "", "User not found in the database")
    return None


def update_user(user: User) -> None:
    try:
        CURSOR.execute(
            """
            UPDATE users
            SET password   = ?,
                first_name = ?,
                last_name  = ?
            WHERE username = ?;
            """,
            (user.password, user.first_name, user.last_name, user.username),
        ).connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "UPDATE USER", user.username, f"Failed to update user: {e}")
        raise


def add_file(file: File) -> None:
    user: User | None = get_user(username=file.username)

    if user is None:
        raise ValueError(f"User `{file.username}` does not exist")

    name: str = file.name

    if get_file(directory=file.directory, name=name, username=file.username):
        path: Path = Path(name)
        stem, extension = path.stem, path.suffix
        i: int = 1

        while get_file(directory=file.directory, name=f"{stem}({i}){extension}", username=file.username):
            i += 1

        name = f"{stem}({i}){extension}"

    file.name = name

    try:
        CURSOR.execute(
            """
            INSERT INTO files (directory, name, type, size, modified_at, data_center, links, deleted_at, username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (file.directory, file.name, file.type, file.size, file.modified_at.isoformat(), file.data_center, dumps(file.links),
             file.deleted_at.isoformat() if file.deleted_at else None, file.username),
        ).connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "INSERT FILES", file.username, f"Failed to insert file: {e}")
        raise


def get_file(*, fid: int | None = None,
             directory: str | None = None,
             name: str | None = None,
             username: str | None = None,
             include_trashed: bool = False,
             trashed_only: bool = False) -> File | None:
    if trashed_only:
        trash_clause: str = "AND deleted_at IS NOT NULL"

    elif not include_trashed:
        trash_clause: str = "AND deleted_at IS NULL"

    else:
        trash_clause: str = ""

    if fid is not None:
        CURSOR.execute(
            f"""
            SELECT id, directory, name, type, size, modified_at, data_center, links, deleted_at, username
            FROM files
            WHERE id = ? {trash_clause};
            """, (fid,),
        )

    elif directory is not None and name is not None and username is not None:
        CURSOR.execute(
            f"""
            SELECT id, directory, name, type, size, modified_at, data_center, links, deleted_at, username
            FROM files
            WHERE directory = ?
              AND name = ?
              AND username = ? {trash_clause};
            """, (directory, name, username),
        )

    else:
        write_log("ERROR", Database, "GET FILE", "", "Invalid search parameters provided by caller.")
        return None

    row: Row | None = CURSOR.fetchone()

    if row:
        file: dict[str, int | str | datetime | None] = dict(row)
        file["links"] = loads(cast(str, file["links"]))
        file["modified_at"] = datetime.fromisoformat(cast(str, file["modified_at"]))
        file["deleted_at"] = datetime.fromisoformat(cast(str, file["deleted_at"])) if file["deleted_at"] else None
        return File(**file)

    return None


def get_files(*, directory: str | None = None,
              username: str | None = None,
              include_trashed: bool = False,
              trashed_only: bool = False) -> list[File]:
    if trashed_only:
        trash_clause: str = "AND deleted_at IS NOT NULL"

    elif not include_trashed:
        trash_clause: str = "AND deleted_at IS NULL"

    else:
        trash_clause: str = ""

    if directory is not None:
        CURSOR.execute(
            f"""
            SELECT id, directory, name, type, size, modified_at, data_center, links, deleted_at, username
            FROM files
            WHERE directory = ? {trash_clause};
            """,
            (directory,),
        )

    elif username is not None:
        CURSOR.execute(
            f"""
            SELECT id, directory, name, type, size, modified_at, data_center, links, deleted_at, username
            FROM files
            WHERE username = ? {trash_clause};
            """,
            (username,),
        )

    else:
        write_log("ERROR", Database, "GET FILES", "", "No valid search parameter provided.")
        return []

    files: list[File] = []

    for row in CURSOR.fetchall():
        file: dict[str, int | str | datetime | None] = dict(cast(Row, row))
        file["links"] = loads(cast(str, file["links"]))
        file["modified_at"] = datetime.fromisoformat(cast(str, file["modified_at"]))
        file["deleted_at"] = datetime.fromisoformat(cast(str, file["deleted_at"])) if file["deleted_at"] else None
        files.append(File(**file))

    return files


def update_file(file: File) -> None:
    try:
        CURSOR.execute(
            """
            UPDATE files
            SET directory   = ?,
                name        = ?,
                type        = ?,
                modified_at = ?,
                deleted_at  = ?
            WHERE id = ?
              AND username = ?;
            """,
            (
                file.directory,
                file.name,
                file.type,
                file.modified_at.isoformat(),
                file.deleted_at.isoformat() if file.deleted_at else None,
                file.id,
                file.username
            ),
        ).connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "UPDATE FILE", file.username, f"Failed to update file: {e}")
        raise


def purge_expired_trash(*, username: str, older_than_days: int = 30) -> None:
    cutoff: str = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()

    try:
        CURSOR.execute(
            """
            DELETE
            FROM files
            WHERE username = ?
              AND deleted_at IS NOT NULL
              AND deleted_at < ?;
            """,
            (username, cutoff),
        ).connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "PURGE TRASH", username, f"Failed to purge trash: {e}")
        raise


def delete_file(file: File) -> None:
    try:
        CURSOR.execute(
            """
            DELETE
            FROM files
            WHERE id = ?
              AND username = ?;
            """,
            (file.id, file.username),
        ).connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "DELETE FILE", file.username, f"Failed to delete file: {e}")
        raise


def github_cursor_get_repo_id() -> int:
    CURSOR.execute(
        """
        SELECT repo_id
        FROM github_cursor;
        """,
    )
    data: Row | None = CURSOR.fetchone()

    if data:
        return data["repo_id"]

    write_log("ERROR", Database, "GET GITHUB CURSOR", "", "No GitHub cursor found in database.")
    raise OSError("GitHub cursor not found in database.")


def github_cursor_increment_repo_id() -> None:
    CURSOR.execute(
        """
        UPDATE github_cursor
        SET repo_id = repo_id + 1;
        """
    ).connection.commit()


def github_cursor_get_used() -> int:
    CURSOR.execute(
        """
        SELECT used
        FROM github_cursor;
        """
    )
    data: Row | None = CURSOR.fetchone()

    if data:
        return data["used"]

    write_log("ERROR", Database, "GET GITHUB CURSOR", "", "No GitHub cursor found in database.")
    raise OSError("GitHub cursor not found in database.")


def github_cursor_set_used(value: int) -> None:
    CURSOR.execute(
        """
        UPDATE github_cursor
        SET used = ?;
        """,
        (value,),
    ).connection.commit()
