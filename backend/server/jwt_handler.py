"""Create and validate bearer tokens and resolve authenticated users."""

from datetime import datetime, timedelta, UTC

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

from backend.database.models import User
from core.utils.env import SECRET_KEY

OAuth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(tokenUrl="/auth/login")
ALGORITHM: str = "HS256"


def create_access_token(data: dict[str, str], expires_delta: timedelta | None = None) -> str:
    """Encode a signed access token with an expiration time."""

    to_encode: dict[str, str | datetime] = data.copy()
    to_encode.update({"exp": datetime.now(UTC) + (expires_delta or timedelta(hours=24))})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str | None:
    """Return the subject from a valid token, or None when decoding fails."""

    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")

    except JWTError:
        return None


async def get_current_user(token: str = Depends(OAuth2_scheme)) -> User:
    """Resolve the bearer token to an existing database user."""

    username: str | None = verify_token(token)

    if username is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user: User | None = await User.get(username)

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return user
