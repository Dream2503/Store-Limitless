from sqlite3 import connect, Connection, Cursor, Row

from core.config import DATABASE_PATH

try:
    CONNECTION: Connection = connect(DATABASE_PATH, check_same_thread=False)
    CONNECTION.row_factory = Row
    CONNECTION.execute("PRAGMA foreign_keys = ON")
    CURSOR: Cursor = CONNECTION.cursor()

    CURSOR.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            username   TEXT PRIMARY KEY,
            password   TEXT NOT NULL,
            first_name TEXT NOT NULL DEFAULT '',
            last_name  TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            directory   TEXT,
            name        TEXT    NOT NULL,
            type        TEXT    NOT NULL,
            size        INTEGER NOT NULL,
            modified_at TEXT    NOT NULL,
            data_center TEXT    NOT NULL,
            links       TEXT    NOT NULL,
            username    TEXT,
            FOREIGN KEY (username) REFERENCES users (username)
        );

        CREATE TABLE IF NOT EXISTS github_cursor (
            repo_id INTEGER NOT NULL DEFAULT 0 PRIMARY KEY,
            used    INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    CONNECTION.commit()

except Exception as e:
    print(f"Failed to connect: {e}")
