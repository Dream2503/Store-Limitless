from pydantic import BaseModel


class User(BaseModel):
    uid: int | None = None
    first_name: str
    last_name: str
    username: str
    password: str

    class Config:
        from_attributes = True

class LoginRequest(BaseModel):
    username: str
    password: str


class File(BaseModel):
    fid: int | None = None
    fname: str
    flinks: list[str]
    data_center: str
    uid: int

    class Config:
        from_attributes = True
