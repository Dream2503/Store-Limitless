from __future__ import annotations

from logging import INFO, WARNING, basicConfig, getLogger
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from dotenv import load_dotenv
from filelock import FileLock

if TYPE_CHECKING:
    from core.utils import Progress

# Paths
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATABASE_PATH: Path = BASE_DIR / "backend" / "database" / "database.db"
LOG_PATH: Path = BASE_DIR / "logs.txt"
LOG_LOCK_PATH: Path = BASE_DIR / "logs.lock"
TELEGRAM_SESSION: Path = BASE_DIR / "telegram_bot"
TRANSFER_PATH: Path = BASE_DIR / "transfer"

# Environment
load_dotenv()

# Application constants
SUPPORTED_DOMAIN: tuple[str, ...] = (
    "drive.google.com",
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
)

# Runtime state
UPLOAD_JOBS: dict[str, Progress] = {}

# Files and locks
TRANSFER_PATH.mkdir(exist_ok=True)
LOG_HANDLER: TextIO = open(LOG_PATH, "a")
LOCK: FileLock = FileLock(LOG_LOCK_PATH)

# Logging
basicConfig(level=INFO, filename=LOG_PATH, filemode="a", format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", )
getLogger("httpx").setLevel(WARNING)
getLogger("telethon").setLevel(WARNING)
