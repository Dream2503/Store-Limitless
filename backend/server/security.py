"""Password hashing and signed public stream token helpers."""

import hmac
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256
from hmac import compare_digest
from json import JSONDecodeError, dumps, loads
from time import time

from passlib.context import CryptContext
from jose import JWTError, jwt

from backend.database.models import File
from backend.server.jwt_handler import ALGORITHM, SECRET_KEY

pwd_context: CryptContext = CryptContext(schemes=["bcrypt"], deprecated="auto")

def _session_expiration(session_token: str) -> int:
    """Read the expiration of the validated bearer token used to mint a private link."""

    try:
        payload = jwt.decode(session_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as error:
        raise ValueError("Invalid login session") from error

    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise ValueError("Login session has no valid expiration")
    return expires_at


def hash_password(password: str) -> str:
    """Hash a plaintext password using the configured password context."""

    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a plaintext password against its stored hash."""

    return pwd_context.verify(password, hashed_password)


def create_public_zip_token(
        file_ids: list[int],
        username: str,
        *,
        folder_ids: list[int] | None = None,
        expires_in: int = 900,
        session_token: str | None = None,
) -> str:
    """Create a short-lived signed token for downloading selected files and folders."""

    payload = {
        "file_ids": list(dict.fromkeys(int(file_id) for file_id in file_ids)),
        "folder_ids": list(dict.fromkeys(int(folder_id) for folder_id in (folder_ids or []))),
        "username": username,
        "exp": min(int(time()) + max(60, int(expires_in)), _session_expiration(session_token)),
        "type": "zip",
    }
    if session_token:
        payload["session_hash"] = sha256(session_token.encode("utf-8")).hexdigest()
        payload["exp"] = min(int(payload["exp"]), _session_expiration(session_token))
    encoded_payload = urlsafe_b64encode(dumps(payload, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=")
    signature = urlsafe_b64encode(hmac.new(SECRET_KEY.encode(), encoded_payload, sha256).digest()).rstrip(b"=")
    return f"{encoded_payload.decode()}.{signature.decode()}"


def verify_public_zip_token(token: str) -> dict[str, object]:
    """Verify a short-lived ZIP token and validate the user and file ID list."""

    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        expected_signature = hmac.new(SECRET_KEY.encode(), encoded_payload.encode(), sha256).digest()
        signature = urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        if not compare_digest(signature, expected_signature):
            raise ValueError("Invalid ZIP token signature")

        data = loads(urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4)))
        file_ids = data.get("file_ids", [])
        folder_ids = data.get("folder_ids", [])
        username = data.get("username")
        expires_at = data.get("exp")
        if data.get("type") != "zip" or not isinstance(username, str) or not username:
            raise ValueError("Invalid ZIP token payload")
        if not isinstance(file_ids, list) or not isinstance(folder_ids, list):
            raise ValueError("Invalid ZIP selection")
        selected_count = len(file_ids) + len(folder_ids)
        if selected_count < 1 or selected_count > 500 or (not folder_ids and len(file_ids) < 2):
            raise ValueError("Invalid ZIP selection size")
        if any(not isinstance(file_id, int) or isinstance(file_id, bool) or file_id <= 0 for file_id in file_ids):
            raise ValueError("Invalid ZIP file ID")
        if any(not isinstance(folder_id, int) or isinstance(folder_id, bool) or folder_id <= 0 for folder_id in folder_ids):
            raise ValueError("Invalid ZIP folder ID")
        if len(set(file_ids)) != len(file_ids) or len(set(folder_ids)) != len(folder_ids):
            raise ValueError("Duplicate ZIP selection ID")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool) or expires_at <= int(time()):
            raise ValueError("ZIP download token has expired")
        session_hash = data.get("session_hash")
        if session_hash is not None and (not isinstance(session_hash, str) or len(session_hash) != 64):
            raise ValueError("Invalid session binding")
        return {
            "file_ids": file_ids,
            "folder_ids": folder_ids,
            "username": username,
            "exp": expires_at,
            **({"session_hash": session_hash} if session_hash is not None else {}),
        }
    except (ValueError, KeyError, TypeError, JSONDecodeError) as error:
        raise ValueError("Invalid or expired ZIP download token") from error


def create_public_stream_token(file: File, username: str) -> str:
    """Create a signed token binding a public stream to a file and owner."""

    encoded_payload: bytes = urlsafe_b64encode(
        dumps({"file_id": file.id, "username": username}, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=")
    encoded_signature: bytes = urlsafe_b64encode(hmac.new(SECRET_KEY.encode(), encoded_payload, sha256).digest()).rstrip(b"=")
    return f"{encoded_payload.decode()}.{encoded_signature.decode()}"



def create_session_bound_stream_token(
        file: File,
        username: str,
        session_token: str,
        *,
        token_type: str,
        expires_in: int = 86400,
) -> str:
    """Create a stream/download token that becomes unusable when its login session ends."""

    if token_type not in {"private-stream", "private-download"}:
        raise ValueError("Invalid private stream token type")
    payload = {
        "file_id": file.id,
        "username": username,
        "type": token_type,
        "session_hash": sha256(session_token.encode("utf-8")).hexdigest(),
        "exp": int(time()) + max(60, int(expires_in)),
    }
    encoded_payload = urlsafe_b64encode(dumps(payload, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=")
    encoded_signature = urlsafe_b64encode(hmac.new(SECRET_KEY.encode(), encoded_payload, sha256).digest()).rstrip(b"=")
    return f"{encoded_payload.decode()}.{encoded_signature.decode()}"


def verify_public_stream_token(token: str) -> dict[str, str | int]:
    """Verify a public stream token signature and return its decoded payload."""

    try:
        encoded_payload: str
        encoded_signature: str
        encoded_payload, encoded_signature = token.split(".", 1)
        expected_signature: bytes = hmac.new(SECRET_KEY.encode(), encoded_payload.encode(), sha256).digest()
        signature: bytes = urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))

        if not compare_digest(expected_signature, signature):
            raise ValueError("Invalid signature")

        data = loads(urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4)))
        if not isinstance(data, dict):
            raise ValueError("Invalid payload")
        if not isinstance(data.get("file_id"), int) or isinstance(data.get("file_id"), bool):
            raise ValueError("Invalid file ID")
        if not isinstance(data.get("username"), str) or not data["username"]:
            raise ValueError("Invalid username")

        token_type = data.get("type")
        if token_type is not None:
            if token_type not in {"private-stream", "private-download"}:
                raise ValueError("Invalid stream token type")
            expires_at = data.get("exp")
            session_hash = data.get("session_hash")
            if not isinstance(expires_at, int) or isinstance(expires_at, bool) or expires_at <= int(time()):
                raise ValueError("Private stream token has expired")
            if not isinstance(session_hash, str) or len(session_hash) != 64:
                raise ValueError("Invalid session binding")

        return data

    except (ValueError, KeyError, TypeError, JSONDecodeError):
        raise ValueError("Invalid public stream token")
