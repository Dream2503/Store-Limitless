from datetime import datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

from backend.database import get_user, User
from core.settings import getenv

OAuth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(tokenUrl="/login")
SECRET_KEY: str = getenv("SECRET_KEY")
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 60


def create_access_token(data: dict[str, str], expires_delta: timedelta = None) -> str:
    to_encode: dict[str, str | datetime] = data.copy()
    expire: datetime = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encode: str = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encode


def verify_token(token: str) -> str | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")

    except JWTError:
        return None


def get_current_user(token: str = Depends(OAuth2_scheme)) -> User:
    username: str | None = verify_token(token)

    if username is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user: User | None = get_user(username=username)

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return user
