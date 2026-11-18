from logging import INFO, WARNING, basicConfig, getLogger
from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv
from filelock import FileLock
from redis.asyncio import Redis

from core.utils import Jobs, getenv

# Paths
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATABASE_PATH: Path = BASE_DIR / "backend" / "database" / "database.db"
LOG_PATH: Path = BASE_DIR / "logs.txt"
LOG_LOCK_PATH: Path = BASE_DIR / "logs.lock"
TELEGRAM_SESSION: Path = BASE_DIR / "telegram_bot"
TRANSFER_PATH: Path = BASE_DIR / "transfer"

# Environment
load_dotenv()
GOOGLE_API_KEY: str = getenv("GOOGLE_API_KEY")

# Application constants
POSSIBLE_DATACENTERS: frozenset[str] = frozenset({
    "Discord",
    "GitHub",
    "Telegram",
})
SUPPORTED_DOMAINS: frozenset[str] = frozenset({
    "drive.google.com",
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
})

# Runtime state
redis: Redis = Redis.from_url("redis://localhost:6379", decode_responses=True)
UPLOAD_JOBS: Jobs = Jobs(redis)

# Files and locks
TRANSFER_PATH.mkdir(exist_ok=True)
LOG_HANDLER: TextIO = open(LOG_PATH, "a")
LOCK: FileLock = FileLock(LOG_LOCK_PATH)

# Logging
basicConfig(level=INFO, filename=LOG_PATH, filemode="a", format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", )
getLogger("httpx").setLevel(WARNING)
getLogger("telethon").setLevel(WARNING)
