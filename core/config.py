from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent.parent

DATABASE_PATH: Path = BASE_DIR / "backend" / "database" / "database.db"
LOG_PATH: Path = BASE_DIR / "logs.txt"
TRANSFER_PATH: Path = BASE_DIR / "transfer"
TRANSFER_PATH.mkdir(exist_ok=True)

SUPPORTED_DOMAIN: list[str] = ["drive.google.com", ]


def get_transfer_path(username: str, directory: str, filename: str) -> Path:
    path: Path = TRANSFER_PATH / username

    if directory:
        path = path / directory

    return path / filename


load_dotenv()

LOG_HANDLER: TextIO = open(LOG_PATH, 'a')


def getenv(key: str) -> str:
    import os
    value: str | None = os.getenv(key)

    if value is None or not value.strip():
        raise RuntimeError(f"Environment variable '{key}' is missing or empty. Check your .env file or system environment.")

    return value


GOOGLE_API_KEY: str = getenv("GOOGLE_API_KEY")
