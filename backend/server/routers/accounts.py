"""Registration, authentication, and account metadata endpoints."""

from __future__ import annotations

from asyncio import to_thread
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.database.connection import POOL
from backend.database.models import Directory, File, User
from backend.database.redis_server import Redis
from backend.server.jwt_handler import OAuth2_scheme, create_access_token, get_current_user, verify_token
from backend.server.security import hash_password, verify_password
from core.data_center import Database
from core.utils import write_log

router: APIRouter = APIRouter()


class LoginRequest(BaseModel):
    """Credentials sent in the POST body instead of the URL query string."""

    username: str
    password: str


@router.post("/register")
async def register(user: User) -> JSONResponse:
    """Register a new user after validating and hashing the password."""

    user.verify()

    if await User.get(user.username):
        write_log("WARNING", Database, "AUTH", user.username, "Registration rejected: username already exists")
        raise HTTPException(status_code=400, detail="Username already registered")

    user.password = hash_password(user.password)
    await user.save()
    write_log("INFO", Database, "AUTH", user.username, "Account registration completed")
    return JSONResponse({"message": "User registered successfully"})


@router.post("/login")
async def login(credentials: LoginRequest) -> tuple[dict[str, str], dict[str, str], Directory]:
    """Authenticate a user and return its token, profile, and home directory."""

    username = credentials.username
    password = credentials.password

    await Redis.delete(f"user:{username}", "User")
    await Redis.delete(f"user:{username}:directories", "Directory")
    await Redis.delete(f"user:{username}:files", "File")
    user: User | None = await User.get(username)

    if not user or not verify_password(password, user.password):
        write_log("WARNING", Database, "AUTH", username, "Login rejected: invalid username or password")
        raise HTTPException(status_code=401, detail="Invalid username or password")

    write_log("INFO", Database, "AUTH", user.username, "Login successful")
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
        write_log("INFO", Database, "AUTH", "unknown", "Logout request used an already expired or invalid session")
        return JSONResponse({"message": "Session already expired"})

    session_hash = sha256(token.encode("utf-8")).hexdigest()
    await Redis.set_json(
        f"auth:revoked:{session_hash}",
        {"username": username, "revoked": True},
        ex=86400,
    )
    write_log("INFO", Database, "AUTH", username, "Session revoked; signed out")
    return JSONResponse({"message": "Signed out"})


@router.get("/metadata")
async def get_metadata(user: User = Depends(get_current_user)) -> JSONResponse:
    """Return account metadata. Add future account-level metadata fields here."""

    active_files: list[File] = await File.get_all(user.username)
    trashed_files: list[File] = await File.get_all(user.username, trash=True)
    storage_used_bytes: int = sum(max(0, int(file.size or 0)) for file in (*active_files, *trashed_files))
    return JSONResponse({"storage_used_bytes": storage_used_bytes})



class ClipboardCreateRequest(BaseModel):
    """Plain text saved to the authenticated account's cross-device clipboard."""

    content: str


def _list_clipboard_items(username: str) -> list[dict[str, object]]:
    """Read only the authenticated user's clipboard entries from PostgreSQL."""

    with POOL.connection() as connection:
        rows = connection.execute(
            """
            SELECT id, content, created_at
            FROM clipboard_items
            WHERE username = %s
            ORDER BY created_at DESC, id DESC;
            """,
            (username,),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "content": str(row["content"]),
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


def _create_clipboard_item(username: str, content: str) -> dict[str, object]:
    """Insert one clipboard entry and return the saved database record."""

    with POOL.connection() as connection:
        row = connection.execute(
            """
            INSERT INTO clipboard_items (username, content)
            VALUES (%s, %s)
            RETURNING id, content, created_at;
            """,
            (username, content),
        ).fetchone()
        connection.commit()

    if row is None:
        raise RuntimeError("PostgreSQL did not return the created clipboard entry")

    return {
        "id": int(row["id"]),
        "content": str(row["content"]),
        "created_at": row["created_at"].isoformat(),
    }


def _delete_clipboard_item(username: str, item_id: int) -> bool:
    """Delete one clipboard item only when it belongs to the authenticated user."""

    with POOL.connection() as connection:
        row = connection.execute(
            """
            DELETE FROM clipboard_items
            WHERE id = %s AND username = %s
            RETURNING id;
            """,
            (item_id, username),
        ).fetchone()
        connection.commit()

    return row is not None


@router.get("/clipboard")
async def list_clipboard_items(user: User = Depends(get_current_user)) -> dict[str, list[dict[str, object]]]:
    """Return the signed-in user's clipboard entries for cross-device access."""

    items = await to_thread(_list_clipboard_items, user.username)
    return {"items": items}


@router.post("/clipboard", status_code=201)
async def create_clipboard_item(
        request: ClipboardCreateRequest,
        user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Save one plain-text clipboard entry for the authenticated account."""

    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Clipboard text cannot be empty")
    if len(request.content) > 1_000_000:
        raise HTTPException(status_code=413, detail="Clipboard text cannot exceed 1 MB")

    return await to_thread(_create_clipboard_item, user.username, request.content)


@router.delete("/clipboard/{item_id}")
async def delete_clipboard_item(
        item_id: int,
        user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """Delete one of the authenticated user's clipboard entries."""

    if item_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid clipboard entry ID")

    deleted = await to_thread(_delete_clipboard_item, user.username, item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Clipboard entry not found")

    return {"deleted": True}
