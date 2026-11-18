from sqlite3 import connect, Connection, Row

from core.config import DATABASE_PATH

try:
    CONNECTION: Connection = connect(DATABASE_PATH, check_same_thread=False)
    CONNECTION.row_factory = Row
    CONNECTION.execute("PRAGMA foreign_keys = ON")

    CONNECTION.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            username   TEXT PRIMARY KEY,
            password   TEXT NOT NULL,
            first_name TEXT NOT NULL DEFAULT '',
            last_name  TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS directories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            path        TEXT NOT NULL,
            modified_at TEXT NOT NULL,
            deleted_at  TEXT,
            username    TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username),
            UNIQUE (path, username)
        );

        CREATE TABLE IF NOT EXISTS files (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            directory_id INTEGER NOT NULL,
            name         TEXT    NOT NULL,
            type         TEXT    NOT NULL,
            size         INTEGER NOT NULL,
            modified_at  TEXT    NOT NULL,
            data_center  TEXT    NOT NULL,
            links        TEXT    NOT NULL,
            deleted_at   TEXT,
            username     TEXT    NOT NULL,
            FOREIGN KEY (directory_id) REFERENCES directories (id),
            FOREIGN KEY (username) REFERENCES users (username)
        );

        CREATE TABLE IF NOT EXISTS github_cursor (
            repo_id INTEGER PRIMARY KEY,
            used    INTEGER NOT NULL
        );

        INSERT INTO github_cursor (repo_id, used)
        SELECT 0, 0
        WHERE NOT EXISTS (SELECT 1
                          FROM github_cursor);
        """
    )
    CONNECTION.commit()

except Exception as e:
    print(f"Failed to connect: {e}")
