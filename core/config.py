import os
from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_PATH = BASE_DIR / "backend" / "database" / "database.db"
LOG_PATH: Path = BASE_DIR / "logs.txt"
TRANSFER_PATH: Path = BASE_DIR / "transfer"

TRANSFER_PATH.mkdir(exist_ok=True)
load_dotenv()

LOG_HANDLER: TextIO = open(LOG_PATH, 'a')

def getenv(key: str) -> str:
    value: str | None = os.getenv(key)

    if value is None or not value.strip():
        raise RuntimeError(f"Environment variable '{key}' is missing or empty. Check your .env file or system environment.")

    return value