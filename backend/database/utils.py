from datetime import datetime
from json import loads, dumps

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
        write_log("INFO", Database, "SET USER", user.username, "User successfully inserted into database.")

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

    write_log(
        "INFO",
        Database,
        "GET USER",
        username,
        f"Select query executed for {username}.",
    )

    row = CURSOR.fetchone()

    if row:
        user = dict(row)

        user["created_at"] = datetime.fromisoformat(
            user["created_at"]
        )

        return User(**user)

    write_log(
        "ERROR",
        Database,
        "GET USER",
        "",
        "User not found in the database",
    )

    return None

def update_user(user: User):
    try:
        CURSOR.execute(
            """
            UPDATE users
            SET password = ?,
                first_name = ?,
                last_name = ?
            WHERE username = ?;
            """,
            (
                user.password,
                user.first_name,
                user.last_name,
                user.username,
            ),
        )
        CURSOR.connection.commit()
        write_log("INFO",Database,"UPDATE USER",user.username,"User successfully updated.",)

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
            INSERT INTO files (
                directory,
                name,
                type,
                size,
                modified_at,
                data_center,
                links,
                username
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                file.directory,
                file.name,
                file.type,
                file.size,
                file.modified_at.isoformat(),
                file.data_center,
                dumps(file.links),
                file.username,
            ),
        )

        CURSOR.connection.commit()

        write_log(
            "INFO",
            Database,
            "INSERT FILES",
            file.username,
            f"File `{file.name}` saved to database with "
            f"{len(file.links)} part(s).",
        )

    except Exception as e:
        CURSOR.connection.rollback()

        write_log(
            "ERROR",
            Database,
            "INSERT FILES",
            file.username,
            f"Failed to insert file: {e}",
        )

        raise

def get_file(*, file_id: int | None = None, name: str | None = None, username: str | None = None) -> File | None:
    if file_id is not None:
        CURSOR.execute(
            """
            SELECT id,
                   directory,
                   name,
                   type,
                   "size",
                   modified_at,
                   data_center,
                   links,
                   username
            FROM files
            WHERE id = ?;
            """, (file_id,),
        )
        attribute, value = "id", file_id

    elif name is not None and username is not None:
        CURSOR.execute(
            """
            SELECT id,
                   directory,
                   name,
                   type,
                   "size",
                   modified_at,
                   data_center,
                   links,
                   username
            FROM files
            WHERE name = ?
              AND username = ?;
            """, (name, username),
        )
        attribute, value = ("name", "username"), (name, username)

    else:
        write_log("ERROR", Database, "GET FILE", "", "Invalid search parameters provided by caller.")
        return None

    write_log("INFO", Database, "GET FILE", "", f"Select query executed for {attribute}={value}.")
    file: dict[str, int | str | list[str]] | None = CURSOR.fetchone()

    if file:
        file["links"] = loads(file["links"])
        file["modified_at"] = datetime.fromisoformat(file["modified_at"])
        return File(**file)

    write_log("ERROR", Database, "GET FILE", "", f"No file found for {attribute}={value}.")
    return None


def get_files(*,directory: str | None = None,username: str | None = None,) -> list[File] | None:

    if directory is not None:
        CURSOR.execute(
            """
            SELECT id, directory, name, type, "size",
                   modified_at, data_center, links, username
            FROM files
            WHERE directory = ?;
            """,
            (directory,),
        )
        attribute, value = "directory", directory

    elif username is not None:
        CURSOR.execute(
            """
            SELECT id, directory, name, type, "size",
                   modified_at, data_center, links, username
            FROM files
            WHERE username = ?;
            """,
            (username,),
        )
        attribute, value = "username", username

    else:
        write_log(
            "ERROR",
            Database,
            "GET FILES",
            "",
            "No valid search parameter provided.",
        )
        return None

    rows = CURSOR.fetchall()

    if rows:
        files: list[File] = []

        for row in rows:
            file = dict(row)  # Important

            file["links"] = loads(file["links"])
            file["modified_at"] = datetime.fromisoformat(
                file["modified_at"]
            )

            files.append(File(**file))

        return files
    
    write_log("INFO",Database,"GET FILES","",f"Select query executed for {attribute}={value}.")
    return None

def update_file(file: File) -> None:
    if file.id is None:
        raise ValueError("File ID is missing")

    try:
        CURSOR.execute(
            """
            UPDATE files
            SET directory = ?,
                name = ?,
                type = ?,
                size = ?,
                modified_at = ?,
                data_center = ?,
                links = ?
            WHERE id = ?
              AND username = ?;
            """,
            (
                file.directory,
                file.name,
                file.type,
                file.size,
                file.modified_at.isoformat(),
                file.data_center,
                dumps(file.links),
                file.id,
                file.username,
            ),
        )

        CURSOR.connection.commit()

        write_log(
            "INFO",
            Database,
            "UPDATE FILE",
            file.username,
            f"File `{file.name}` successfully updated.",
        )

    except Exception as e:
        CURSOR.connection.rollback()

        write_log(
            "ERROR",
            Database,
            "UPDATE FILE",
            file.username,
            f"Failed to update file: {e}",
        )

        raise

def delete_file(file: File) -> None:
    if file.id is None:
        raise ValueError("File ID is missing")

    try:
        CURSOR.execute(
            """
            DELETE FROM files
            WHERE id = ?
              AND username = ?;
            """,
            (
                file.id,
                file.username,
            ),
        )

        CURSOR.connection.commit()

        write_log(
            "INFO",
            Database,
            "DELETE FILE",
            file.username,
            f"File `{file.name}` successfully deleted.",
        )

    except Exception as e:
        CURSOR.connection.rollback()

        write_log(
            "ERROR",
            Database,
            "DELETE FILE",
            file.username,
            f"Failed to delete file: {e}",
        )

        raise

def github_cursor_get_repo_id() -> int:
    CURSOR.execute(
        """
        SELECT repo_id
        FROM github_cursor;
        """,
    )
    data: dict[str, int] | None = CURSOR.fetchone()

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
    write_log("INFO", Database, "UPDATE GITHUB CURSOR", "", "GitHub repository ID incremented.")


def github_cursor_get_used() -> int:
    CURSOR.execute(
        """
        SELECT used
        FROM github_cursor;
        """,
    )
    data: dict[str, int] | None = CURSOR.fetchone()

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
    write_log("INFO", Database, "UPDATE GITHUB CURSOR", "", f"GitHub storage usage increased by {value} bytes.")
