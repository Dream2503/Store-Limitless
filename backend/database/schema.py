from datetime import datetime,timezone

from core.data_center import DataCenter
from pydantic import BaseModel, ConfigDict,Field


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    username: str
    password: str
    first_name: str
    last_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return (
            f"User(username={self.username!r}"
            f"first_name={self.first_name!r}, "
            f"last_name={self.last_name!r}, "
            f"created_at={self.created_at})"
        )


class File(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    directory: str
    name: str
    type: str
    size: int
    modified_at: datetime
    data_center: str
    links: list[str]
    username: str

    def __repr__(self) -> str:
        return (
            f"File(fid={self.id}, "
            f"directory={self.directory!r}, "
            f"name={self.name!r}, "
            f"type={self.type}, "
            f"size={self.size}, "
            f"modified_at={self.modified_at}, "
            f"links={self.links!r}, "
            f"data_center={self.data_center!r}, "
            f"username={self.username})"
        )


class LoginRequest(BaseModel):
    username: str
    password: str

    def __repr__(self) -> str:
        return f"LoginRequest(username={self.username!r})"
