"""Registration, authentication, and account metadata endpoints."""

from __future__ import annotations

from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from backend.database.models import Directory, File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import OAuth2_scheme, create_access_token, get_current_user, verify_token
from backend.server.security import hash_password, verify_password

router: APIRouter = APIRouter()


@router.post("/register")
async def register(user: User) -> JSONResponse:
    """Register a new user after validating and hashing the password."""

    user.verify()

    if await User.get(user.username):
        raise HTTPException(status_code=400, detail="Username already registered")

    user.password = hash_password(user.password)
    await user.save()
    return JSONResponse({"message": "User registered successfully"})


@router.post("/login")
async def login(username: str, password: str) -> tuple[dict[str, str], dict[str, str], Directory]:
    """Authenticate a user and return its token, profile, and home directory."""

    await Redis.delete(f"user:{username}", "User")
    await Redis.delete(f"user:{username}:directories", "Directory")
    await Redis.delete(f"user:{username}:files", "File")
    user: User | None = await User.get(username)

    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return (
        {
            "message": "Login successful",
            "access_token": create_access_token(data={"sub": user.username}),
            "token_type": "bearer",
        }, {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }, await user.home
    )


@router.post("/logout")
async def logout(token: str = Depends(OAuth2_scheme)) -> JSONResponse:
    """Revoke session-bound private stream and download links for this login."""

    username = verify_token(token)
    if username is None:
        return JSONResponse({"message": "Session already expired"})

    session_hash = sha256(token.encode("utf-8")).hexdigest()
    await Redis.set_json(
        f"auth:revoked:{session_hash}",
        {"username": username, "revoked": True},
        ex=86400,
    )
    return JSONResponse({"message": "Signed out"})


@router.get("/metadata")
async def get_metadata(user: User = Depends(get_current_user)) -> JSONResponse:
    """Return account metadata. Add future account-level metadata fields here."""

    active_files: list[File] = await File.get_all(user.username)
    trashed_files: list[File] = await File.get_all(user.username, trash=True)
    storage_used_bytes: int = sum(max(0, int(file.size or 0)) for file in (*active_files, *trashed_files))
    return JSONResponse({"storage_used_bytes": storage_used_bytes})
