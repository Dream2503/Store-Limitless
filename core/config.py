import os
from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv

LOG_PATH: Path = Path("..") / "logs.txt"
TRANSFER_PATH: Path = Path('.') / "transfer"
LOG_HANDLER: TextIO = open(LOG_PATH, 'a')
TRANSFER_PATH.mkdir(exist_ok=True)
load_dotenv()


def getenv(key: str) -> str:
    value: str | None = os.getenv(key)

    if value is None or not value.strip():
        raise RuntimeError(f"Environment variable '{key}' is missing or empty. Check your .env file or system environment.")

    return value
