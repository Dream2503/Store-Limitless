"""Registration, authentication, and account metadata endpoints."""

from __future__ import annotations

import json
from asyncio import to_thread
from contextlib import aclosing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import aiofiles

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.database.connection import POOL
from backend.database.models import Directory, File, User
from backend.database.models.file import SYSTEM_CLIPBOARD_FILE_NAME
from backend.database.redis_server import Redis
from backend.server.jwt_handler import OAuth2_scheme, create_access_token, get_current_user, verify_token
from backend.server.security import hash_password, verify_password
from core.config import TRANSFER_PATH
from core.data_center import DataCenter, Database
from core.transfer import file_upload
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



# The clipboard is one hidden JSON text file per account in /home. Each
# entry is an object with its own UUID and timestamp, so create/copy/delete
# operations rewrite the same stored file instead of creating database rows.
CLIPBOARD_MAX_ENTRY_SIZE = 1_000_000
CLIPBOARD_MAX_FILE_SIZE = 5_000_000
CLIPBOARD_DATA_CENTER = "Discord"


class ClipboardCreateRequest(BaseModel):
    """Plain text saved to the authenticated account's single clipboard file."""

    content: str


def _get_clipboard_file_sync(username: str) -> File | None:
    """Read the single active clipboard file's metadata from the existing files table."""

    with POOL.connection() as connection:
        row = connection.execute(
            """
            SELECT f.id, f.directory_id, f.name, f.type, f.size, f.modified_at,
                   f.deleted_at, f.data_center, f.links, f.username, f.public_slug
            FROM files AS f
            INNER JOIN directories AS d
                ON d.id = f.directory_id AND d.username = f.username
            WHERE f.username = %s
              AND d.path = '/home'
              AND f.name = %s
              AND f.deleted_at IS NULL
            ORDER BY f.id DESC
            LIMIT 1;
            """,
            (username, SYSTEM_CLIPBOARD_FILE_NAME),
        ).fetchone()

    return File(**row) if row is not None else None


def _get_clipboard_home_sync(username: str) -> tuple[int, str]:
    """Return the user's home directory ID and preferred existing storage provider."""

    with POOL.connection() as connection:
        directory = connection.execute(
            """
            SELECT id
            FROM directories
            WHERE username = %s AND path = '/home' AND deleted_at IS NULL
            LIMIT 1;
            """,
            (username,),
        ).fetchone()
        provider = connection.execute(
            """
            SELECT data_center
            FROM files
            WHERE username = %s
              AND deleted_at IS NULL
              AND name <> %s
            ORDER BY modified_at DESC, id DESC
            LIMIT 1;
            """,
            (username, SYSTEM_CLIPBOARD_FILE_NAME),
        ).fetchone()

    if directory is None:
        raise RuntimeError("The user's home directory could not be found")

    return int(directory["id"]), str(provider["data_center"]) if provider else CLIPBOARD_DATA_CENTER


async def _read_clipboard_document(file: File) -> list[dict[str, str]]:
    """Download and decode the one JSON clipboard file from its configured storage provider."""

    provider = DataCenter(file.data_center)
    if provider is None:
        raise RuntimeError("The clipboard file uses an unavailable storage provider")

    parts: list[bytes] = []
    for link in file.links:
        parts.append(await provider.download(str(link)))
    raw = b"".join(parts)

    if len(raw) != int(file.size or 0):
        raise ValueError("The clipboard file is incomplete")

    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("The clipboard file has an unsupported format")

    raw_items = document.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("The clipboard file does not contain a valid entries list")

    items: list[dict[str, str]] = []
    for value in raw_items:
        if not isinstance(value, dict):
            continue
        item_id = value.get("id")
        content = value.get("content")
        created_at = value.get("created_at")
        if not isinstance(item_id, str) or not isinstance(content, str) or not isinstance(created_at, str):
            continue
        items.append({"id": item_id, "content": content, "created_at": created_at})

    return items


async def _write_clipboard_document(username: str, items: list[dict[str, str]], file: File | None) -> File:
    """Create the account's clipboard file once, then replace its contents on later edits."""

    payload = json.dumps(
        {"version": 1, "username": username, "items": items},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    if len(payload) > CLIPBOARD_MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Your clipboard file is full. Delete older entries before saving more text.",
        )

    file_path = TRANSFER_PATH / username / "clipboard" / uuid4().hex / SYSTEM_CLIPBOARD_FILE_NAME
    file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with aiofiles.open(file_path, "wb") as output:
            await output.write(payload)

        if file is None:
            directory_id, data_center = await to_thread(_get_clipboard_home_sync, username)
            file = File(
                directory_id=directory_id,
                name=SYSTEM_CLIPBOARD_FILE_NAME,
                type="application/json",
                size=0,
                modified_at=datetime.now(timezone.utc),
                links=[],
                data_center=data_center,
                username=username,
            )
            async with aclosing(file_upload(
                    file,
                    file_path,
                    bypass_queue=True,
                    total_size=len(payload),
            )) as upload_stream:
                async for _ in upload_stream:
                    pass
        else:
            previous_links = list(file.links)
            previous_size = int(file.size or 0)
            previous_modified_at = file.modified_at
            file.links = []
            file.size = 0
            try:
                async with aclosing(file_upload(
                        file,
                        file_path,
                        intermediate=True,
                        bypass_queue=True,
                        total_size=len(payload),
                )) as upload_stream:
                    async for _ in upload_stream:
                        pass

                file.modified_at = datetime.now(timezone.utc)
                await file.update()
            except Exception:
                file.links = previous_links
                file.size = previous_size
                file.modified_at = previous_modified_at
                raise

        return file
    finally:
        file_path.unlink(missing_ok=True)
        try:
            file_path.parent.rmdir()
        except OSError:
            pass


def _clipboard_lock(username: str):
    """Return a Redis-backed lock so multiple devices cannot overwrite each other's changes."""

    return Redis.redis.lock(
        "clipboard:file:" + username,
        timeout=300,
        blocking_timeout=300,
    )


@router.get("/clipboard")
async def list_clipboard_items(user: User = Depends(get_current_user)) -> dict[str, list[dict[str, str]]]:
    """Read entries from one account-owned file; initialize that file empty on first use."""

    async with _clipboard_lock(user.username):
        file = await to_thread(_get_clipboard_file_sync, user.username)
        if file is None:
            await _write_clipboard_document(user.username, [], None)
            file = await to_thread(_get_clipboard_file_sync, user.username)

        if file is None:
            raise HTTPException(status_code=502, detail="The clipboard file could not be initialized")

        try:
            items = await _read_clipboard_document(file)
        except Exception as error:
            write_log("ERROR", Database, "CLIPBOARD", user.username, f"Could not read clipboard file: {type(error).__name__}: {error}")
            raise HTTPException(status_code=502, detail="The stored clipboard file could not be read") from error

    return {"items": items}


@router.post("/clipboard", status_code=201)
async def create_clipboard_item(
        request: ClipboardCreateRequest,
        user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Append one metadata-tagged text entry by rewriting the user's single clipboard file."""

    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Clipboard text cannot be empty")
    if len(request.content.encode("utf-8")) > CLIPBOARD_MAX_ENTRY_SIZE:
        raise HTTPException(status_code=413, detail="Clipboard text cannot exceed 1 MB")

    async with _clipboard_lock(user.username):
        file = await to_thread(_get_clipboard_file_sync, user.username)
        items = await _read_clipboard_document(file) if file is not None else []
        item = {
            "id": str(uuid4()),
            "content": request.content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await _write_clipboard_document(user.username, [item, *items], file)

    return item


@router.delete("/clipboard/{item_id}")
async def delete_clipboard_item(
        item_id: str,
        user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """Remove one metadata-tagged entry by rewriting the same clipboard file."""

    try:
        normalized_id = str(UUID(item_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=404, detail="Clipboard entry not found") from error

    async with _clipboard_lock(user.username):
        file = await to_thread(_get_clipboard_file_sync, user.username)
        if file is None:
            raise HTTPException(status_code=404, detail="Clipboard entry not found")

        items = await _read_clipboard_document(file)
        remaining = [item for item in items if item["id"] != normalized_id]
        if len(remaining) == len(items):
            raise HTTPException(status_code=404, detail="Clipboard entry not found")

        await _write_clipboard_document(user.username, remaining, file)

    return {"deleted": True}
