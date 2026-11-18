from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    uid: int | None = None
    first_name: str
    last_name: str
    username: str
    password: str

    def __repr__(self) -> str:
        return (
            f"User(uid={self.uid}, "
            f"first_name={self.first_name!r}, "
            f"last_name={self.last_name!r}, "
            f"username={self.username!r})"
        )


class File(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    fid: int | None = None
    fname: str
    flinks: list[str]
    data_center: str
    uid: int

    def __repr__(self) -> str:
        return (
            f"File(fid={self.fid}, "
            f"fname={self.fname!r}, "
            f"flinks={self.flinks!r}, "
            f"data_center={self.data_center!r}, "
            f"uid={self.uid})"
        )


class LoginRequest(BaseModel):
    username: str
    password: str

    def __repr__(self) -> str:
        return f"LoginRequest(username={self.username!r})"
