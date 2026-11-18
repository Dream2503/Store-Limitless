import base64
import hashlib
import hmac
import json

from passlib.context import CryptContext

from backend.server.jwt_handler import SECRET_KEY

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


def create_public_stream_token(file_id: int, username: str) -> str:
    payload = {"file_id": file_id, "username": username}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded_payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=")
    signature = hmac.new(SECRET_KEY.encode(), encoded_payload, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return f"{encoded_payload.decode()}.{encoded_signature.decode()}"


def verify_public_stream_token(token: str) -> dict[str, int]:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        expected_signature = hmac.new(SECRET_KEY.encode(), encoded_payload.encode(), hashlib.sha256).digest()
        supplied_signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))

        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise ValueError("Invalid signature")

        payload = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
        data = json.loads(payload)

        if not isinstance(data["file_id"], int) or not isinstance(data["username"], str):
            raise ValueError("Invalid payload")

        return data

    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("Invalid public stream token")
