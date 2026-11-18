import hmac
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256
from hmac import compare_digest
from json import JSONDecodeError, dumps, loads

from passlib.context import CryptContext

from backend.database import File
from backend.server.jwt_handler import SECRET_KEY

pwd_context: CryptContext = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


def create_public_stream_token(file: File, username: str) -> str:
    encoded_payload: bytes = urlsafe_b64encode(
        dumps(
            {"directory": file.directory, "file_id": file.id, "username": username},
            separators=(",", ":"),
            sort_keys=True
        ).encode()
    ).rstrip(b"=")

    encoded_signature: bytes = urlsafe_b64encode(
        hmac.new(SECRET_KEY.encode(), encoded_payload, sha256).digest()
    ).rstrip(b"=")

    return f"{encoded_payload.decode()}.{encoded_signature.decode()}"


def verify_public_stream_token(token: str) -> dict[str, str | int]:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)

        expected_signature: bytes = hmac.new(
            SECRET_KEY.encode(),
            encoded_payload.encode(),
            sha256
        ).digest()

        signature: bytes = urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )

        if not compare_digest(expected_signature, signature):
            raise ValueError("Invalid signature")

        data: dict[str, str | int] = loads(
            urlsafe_b64decode(
                encoded_payload + "=" * (-len(encoded_payload) % 4)
            )
        )

        if (
                not isinstance(data["directory"], str)
                or not isinstance(data["file_id"], int)
                or not isinstance(data["username"], str)
        ):
            raise ValueError("Invalid payload")

        return data

    except (ValueError, KeyError, TypeError, JSONDecodeError):
        raise ValueError("Invalid public stream token")
