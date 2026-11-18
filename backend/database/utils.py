from core.module import Database
from core.utils import write_log
from .connection import CURSOR
from .schema import File, User


def add_user(user: User) -> None:
    try:
        CURSOR.execute(
                """
                INSERT INTO users (first_name, last_name, username, password)
                VALUES (%s, %s, %s, %s);
                """, (user.first_name, user.last_name, user.username, user.password),
        )
        write_log("INFO", Database, "SET USER", user.username, "Insert query executed.")
        CURSOR.connection.commit()
        write_log("INFO", Database, "SET USER", user.username, "User successfully inserted into database.")

    except Exception as e:
        write_log("ERROR", Database, "SET USER", user.username, f"Failed to insert user: {e}")


def get_user(*, uid: int | None = None, username: str | None = None, fid: int | None = None) -> User | None:
    if uid is not None:
        CURSOR.execute(
                """
                SELECT uid, first_name, last_name, username, password
                FROM users
                WHERE uid = %s;
                """,
                (uid,),
        )
        attribute, value = "uid", uid

    elif username is not None:
        CURSOR.execute(
                """
                SELECT uid, first_name, last_name, username, password
                FROM users
                WHERE username = %s;
                """,
                (username,),
        )
        attribute, value = "username", username

    elif fid is not None:
        CURSOR.execute(
                """
                SELECT uid, first_name, last_name, username, password
                FROM users u
                         JOIN files f ON f.uid = u.uid
                WHERE f.fid = %s;
                """,
                (fid,),
        )
        attribute, value = "fid", fid

    else:
        write_log("ERROR", Database, "GET USER", "", "No search parameter provided.")
        return None

    write_log("INFO", Database, "GET USER", "", f"Select query executed for {attribute}={value}.")
    data: dict[str, int | str] | None = CURSOR.fetchone()

    if data:
        user: User = User(**data)
        write_log("INFO", Database, "GET USER", user.username, "Successfully fetched user from database.")
        return user

    write_log("ERROR", Database, "GET USER", "", "User not found in the database")
    return None


def add_file(file: File) -> None:
    user: User | None = get_user(uid=file.uid)

    if user:
        CURSOR.execute(
                """
                INSERT INTO files (fname, flinks, data_center, uid)
                VALUES (%s, %s, %s, %s);
                """, (file.fname, file.flinks, file.data_center, file.uid),
        )
        write_log("INFO", Database, "INSERT FILES", "", f"Insert query executed.")
        CURSOR.connection.commit()
        write_log("INFO", Database, "INSERT FILES", user.username, f"File `{file.fname}` saved to database with {len(file.flinks)} part(s).")


def get_file(*, fid: int | None = None, fname: str | None = None, uid: int | None = None) -> File | None:
    if fid is not None:
        CURSOR.execute(
                """
                SELECT fid, fname, flinks, data_center, uid
                FROM files
                WHERE fid = %s;
                """, (fid,),
        )
        attribute, value = "fid", fid

    elif fname is not None and uid is not None:
        CURSOR.execute(
                """
                SELECT fid, fname, flinks, data_center, uid
                FROM files
                WHERE fname = %s
                  AND uid = %s;
                """, (fname, uid),
        )
        attribute, value = ("fname", "uid"), (fname, uid)

    else:
        write_log("ERROR", Database, "GET FILE", "", "Invalid search parameters provided by caller.")
        return None

    write_log("INFO", Database, "GET FILE", "", f"Select query executed for {attribute}={value}.")
    data: dict[str, int | str | list[str]] | None = CURSOR.fetchone()

    if data:
        file: File = File(**data)
        write_log("INFO", Database, "GET FILE", file.fname, "Successfully fetched file from database.")
        return file

    write_log("ERROR", Database, "GET FILE", "", f"No file found for {attribute}={value}.")
    return None


def get_files(*, fname: str | None = None, data_center: str | None = None, uid: int | None = None) -> list[File] | None:
    if fname is not None:
        CURSOR.execute(
                """
                SELECT fid, fname, flinks, data_center, uid
                FROM files
                WHERE fname = %s;
                """, (fname,),
        )
        attribute, value = "fname", fname

    elif data_center is not None:
        CURSOR.execute(
                """
                SELECT fid, fname, flinks, data_center, uid
                FROM files
                WHERE data_center = %s;
                """, (data_center,),
        )
        attribute, value = "data_center", data_center

    elif uid is not None:
        CURSOR.execute(
                """
                SELECT fid, fname, flinks, data_center, uid
                FROM files
                WHERE uid = %s;
                """, (uid,),
        )
        attribute, value = "uid", uid

    else:
        write_log("ERROR", Database, "GET FILES", "", "No valid search parameter provided.")
        return None

    write_log("INFO", Database, "GET FILES", "", f"Select query executed for {attribute}={value}.")
    data: list[dict[str, int | str | list[str]]] = CURSOR.fetchall()

    if data:
        files: list[File] = [File(**file) for file in data]
        write_log("INFO", Database, "GET FILES", str(value), f"Successfully fetched {len(files)} file(s) from database.")
        return files

    write_log("ERROR", Database, "GET FILES", "", f"No files found for {attribute}={value}.")
    return None


def clear_file() -> None:
    """SHOULD BE REMOVED AFTER TESTING / DEBUG"""

    CURSOR.execute(
            """
            TRUNCATE TABLE files
                RESTART IDENTITY;
            """,
    )
    write_log("INFO", Database, "CLEAR", "", f"Truncate query executed.")
    CURSOR.connection.commit()
    write_log("INFO", Database, "CLEAR", "", f"Truncated the files table.")
