from sqlite3 import Row

from pydantic import BaseModel, ConfigDict

from backend.database.connection import CONNECTION
from core.data_center import Database
from core.utils import write_log


class GitHubCursor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repo_id: int = 0
    used: int

    def __repr__(self) -> str:
        return (
            f"GitHubCursor(repo_id={self.repo_id}, "
            f"used={self.used})"
        )

    @classmethod
    def get(cls) -> "GitHubCursor":
        row: Row | None = CONNECTION.execute(
            """
            SELECT repo_id, used
            FROM github_cursor;
            """
        ).fetchone()

        if row is None:
            raise OSError("GitHub cursor not found in database.")

        return cls(**dict(row))

    def save(self) -> None:
        try:
            CONNECTION.execute(
                """
                UPDATE github_cursor
                SET repo_id = ?,
                    used    = ?;
                """,
                (
                    self.repo_id,
                    self.used,
                ),
            )
            CONNECTION.commit()

        except Exception as e:
            CONNECTION.rollback()
            write_log("ERROR", Database, "UPDATE GITHUB CURSOR", "", f"Failed to update GitHub cursor: {e}")
            raise

    def increment_repo_id(self) -> None:
        self.repo_id += 1
        self.save()

    def set_used(self, value: int) -> None:
        self.used = value
        self.save()
