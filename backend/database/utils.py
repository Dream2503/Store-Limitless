from datetime import datetime, timezone, timedelta
from json import loads, dumps
from sqlite3 import Row

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
        )
        CURSOR.connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "SET USER", user.username, f"Failed to insert user: {e}")


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
        user: dict[str, str] = dict(row)
        user["created_at"] = datetime.fromisoformat(user["created_at"])
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
        )
        CURSOR.connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "UPDATE USER", user.username, f"Failed to update user: {e}")
        raise


def add_file(file: File) -> None:
    user: User | None = get_user(username=file.username)
    if user is None:
        raise ValueError(f"User `{file.username}` does not exist")

    try:
        CURSOR.execute(
            """
            INSERT INTO files (directory,
                               name,
                               type,
                               size,
                               modified_at,
                               data_center,
                               links,
                               deleted_at,
                               username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (file.directory, file.name, file.type, file.size, file.modified_at.isoformat(), file.data_center,
             dumps(file.links), file.deleted_at.isoformat() if file.deleted_at else None, file.username),
        )
        CURSOR.connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "INSERT FILES", file.username, f"Failed to insert file: {e}")
        raise


def get_file(*, file_id: int | None = None, name: str | None = None, username: str | None = None,
             include_trashed: bool = False) -> File | None:
    trash_clause = "" if include_trashed else "AND deleted_at IS NULL"

    if file_id is not None:
        CURSOR.execute(
            f"""
            SELECT id, directory, name, type, size, modified_at, data_center, links, deleted_at, username
            FROM files
            WHERE id = ? {trash_clause};
            """, (file_id,),
        )

    elif name is not None and username is not None:
        CURSOR.execute(
            f"""
            SELECT id, directory, name, type, size, modified_at, data_center, links, deleted_at, username
            FROM files
            WHERE name = ? AND username = ? {trash_clause};
            """, (name, username),
        )

    else:
        write_log("ERROR", Database, "GET FILE", "", "Invalid search parameters provided by caller.")
        return None

    row: Row | None = CURSOR.fetchone()

    if row:
        file: dict[str, int | str] = dict(row)
        file["links"] = loads(file["links"])
        file["modified_at"] = datetime.fromisoformat(file["modified_at"])
        file["deleted_at"] = datetime.fromisoformat(file["deleted_at"]) if file["deleted_at"] else None
        return File(**file)

    return None


def get_files(*, directory: str | None = None, username: str | None = None,
              include_trashed: bool = False) -> list[File] | None:
    trash_clause = "" if include_trashed else "AND deleted_at IS NULL"

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
        return None

    files: list[File] = []

    for row in CURSOR.fetchall():
        file: dict[str, int | str] = dict(row)
        file["links"] = loads(file["links"])
        file["modified_at"] = datetime.fromisoformat(file["modified_at"])
        file["deleted_at"] = datetime.fromisoformat(file["deleted_at"]) if file["deleted_at"] else None
        files.append(File(**file))

    return files


def get_trashed_files(*, username: str) -> list[File]:
    CURSOR.execute(
        """
        SELECT id,
               directory,
               name,
               type,
               size,
               modified_at,
               data_center,
               links,
               deleted_at,
               username
        FROM files
        WHERE username = ?
          AND deleted_at IS NOT NULL;
        """,
        (username,),
    )
    write_log("INFO", Database, "GET TRASHED FILES", username, "Select query executed for trashed files.")
    files: list[File] = []

    for row in CURSOR.fetchall():
        file: dict[str, int | str] = dict(row)
        file["links"] = loads(file["links"])
        file["modified_at"] = datetime.fromisoformat(file["modified_at"])
        file["deleted_at"] = datetime.fromisoformat(file["deleted_at"])
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
            (file.directory, file.name, file.type, file.modified_at.isoformat(),
             file.deleted_at.isoformat() if file.deleted_at else None, file.id, file.username),
        )
        CURSOR.connection.commit()

    except Exception as e:
        CURSOR.connection.rollback()
        write_log("ERROR", Database, "UPDATE FILE", file.username, f"Failed to update file: {e}")
        raise


def purge_expired_trash(*, username: str, older_than_days: int = 30) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
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
        )
        CURSOR.connection.commit()
        write_log("INFO", Database, "PURGE TRASH", username, f"Purged {CURSOR.rowcount} expired trash item(s).")

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
        )
        CURSOR.connection.commit()

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
        """,
    )
    CURSOR.connection.commit()


def github_cursor_get_used() -> int:
    CURSOR.execute(
        """
        SELECT used
        FROM github_cursor;
        """,
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
    )
    CURSOR.connection.commit()
