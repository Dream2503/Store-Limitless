"""
User model and database operations. User records are stored in PostgreSQL and read through the Redis cache; a new user is also given a default /home
directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.database.connection import POOL, UserDict
from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import write_log
from .directory import Directory


class User(BaseModel):
    """Represent an account and provide validation and persistence operations for it."""

    model_config = ConfigDict(from_attributes=True)
    username: str
    password: str
    first_name: str
    last_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        """Return a readable representation of the user without exposing the password."""

        return (
            f"User(username={self.username!r}, "
            f"first_name={self.first_name!r}, "
            f"last_name={self.last_name!r}, "
            f"created_at={self.created_at})"
        )

    async def save(self) -> None:
        """Insert the user and its default /home directory in one database transaction. Roll back and log the error if either insert fails."""

        with POOL.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (username, password, first_name, last_name, created_at)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (self.username, self.password, self.first_name, self.last_name, self.created_at),
                )
                connection.execute(
                    """
                    INSERT INTO directories (path, modified_at, deleted_at, username)
                    VALUES (%s, %s, %s, %s);
                    """,
                    ("/home", datetime.now(timezone.utc), None, self.username),
                )
                connection.commit()

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "SET USER", self.username, f"Failed to insert user: {e}")
                raise

    @property
    async def home(self) -> Directory:
        """Return the user’s default /home directory from the directory model."""

        return cast(Directory, await Directory.get(self.username, path=Path("/home")))

    @classmethod
    async def get(cls, username: str) -> User | None:
        """Retrieve a user from Redis when cached, otherwise query PostgreSQL and cache the result. Return None when the username does not exist."""

        try:
            return cast(User, await Redis.get(f"user:{username}", "User"))

        except KeyError:
            pass

        with POOL.connection() as connection:
            row: UserDict | None = connection.execute(
                """
                SELECT username, password, first_name, last_name, created_at
                FROM users
                WHERE username = %s;
                """,
                (username,),
            ).fetchone()

        if row is None:
            return None

        user: User = cls(**row)
        await Redis.set(f"user:{user.username}", user)
        return user

    def update(self) -> None:
        """Persist the user’s password and profile-name fields to PostgreSQL. Roll back and log failures."""

        with POOL.connection() as connection:
            try:
                connection.execute(
                    """
                    UPDATE users
                    SET password   = %s,
                        first_name = %s,
                        last_name  = %s
                    WHERE username = %s;
                    """,
                    (self.password, self.first_name, self.last_name, self.username),
                )
                connection.commit()

            except Exception as e:
                connection.rollback()
                write_log("ERROR", Database, "UPDATE USER", self.username, f"Failed to update user: {e}")
                raise

    def verify(self) -> None:
        """Validate the username, first name, and last name, raising HTTPException when a field is invalid."""

        if not self.username or len(self.username) < 3 or len(self.username) > 32 or not self.username.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(status_code=400, detail="Invalid username")

        if not self.first_name or len(self.first_name) > 50 or not self.first_name.replace(" ", "").isalpha():
            raise HTTPException(status_code=400, detail="Invalid first name")

        if not self.last_name or len(self.last_name) > 50 or not self.last_name.replace(" ", "").isalpha():
            raise HTTPException(status_code=400, detail="Invalid last name")
