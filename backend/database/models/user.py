from datetime import datetime, timezone
from sqlite3 import Row
from typing import cast

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import CONNECTION
from core.data_center import Database
from core.utils import write_log


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str
    password: str
    first_name: str
    last_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return (
            f"User(username={self.username!r}, "
            f"first_name={self.first_name!r}, "
            f"last_name={self.last_name!r}, "
            f"created_at={self.created_at})"
        )

    def save(self) -> None:
        try:
            CONNECTION.execute(
                """
                INSERT INTO users (username, password, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (self.username, self.password, self.first_name, self.last_name, self.created_at.isoformat()),
            ).execute(
                """
                INSERT INTO directories (path, modified_at, deleted_at, username)
                VALUES (?, ?, ?, ?);
                """,
                ("/home", datetime.now(timezone.utc).isoformat(), None, self.username),
            )
            CONNECTION.commit()

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "SET USER", self.username, f"Failed to insert user: {e}")
            raise

    @classmethod
    def get(cls, username: str) -> "User | None":
        row: Row | None = CONNECTION.execute(
            """
            SELECT username, password, first_name, last_name, created_at
            FROM users
            WHERE username = ?;
            """,
            (username,),
        ).fetchone()

        if row is None:
            return None

        data: dict[str, str | datetime] = dict(row)
        data["created_at"] = datetime.fromisoformat(cast(str, data["created_at"]))
        return cls(**data)

    def update(self) -> None:
        try:
            CONNECTION.execute(
                """
                UPDATE users
                SET password   = ?,
                    first_name = ?,
                    last_name  = ?
                WHERE username = ?;
                """,
                (self.password, self.first_name, self.last_name, self.username),
            )
            CONNECTION.commit()

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "UPDATE USER", self.username, f"Failed to update user: {e}")
            raise

    def verify(self) -> None:
        if not self.username or len(self.username) < 3 or len(self.username) > 32 or not self.username.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(status_code=400, detail="Invalid username")

        if not self.first_name or len(self.first_name) > 50 or not self.first_name.replace(" ", "").isalpha():
            raise HTTPException(status_code=400, detail="Invalid first name")

        if not self.last_name or len(self.last_name) > 50 or not self.last_name.replace(" ", "").isalpha():
            raise HTTPException(status_code=400, detail="Invalid last name")
