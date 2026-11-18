import os
import platform
import sys
from logging import INFO, WARNING, basicConfig, getLogger
from pathlib import Path
from shutil import copy2
from typing import TextIO

from dotenv import load_dotenv
from filelock import FileLock

from core.utils import getenv

FROZEN: bool = getattr(sys, "frozen", False)

if FROZEN:
    if platform.system() == "Windows":
        BASE_DIR = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData/Local")) / "StoreLimitless"

    else:
        BASE_DIR = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local/share")) / "storeLimitless"

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH: Path = BASE_DIR / "logs.txt"
    LOG_LOCK_PATH: Path = BASE_DIR / "logs.lock"
    TELEGRAM_SESSION: Path = BASE_DIR / "telegram_bot"
    TRANSFER_PATH: Path = BASE_DIR / "transfer"
    ENV_PATH: Path = BASE_DIR / ".env"
    TRANSFER_PATH.mkdir(parents=True, exist_ok=True)

    if not ENV_PATH.exists():
        source_env = Path(sys._MEIPASS) / ".env"

        if source_env.exists():
            copy2(source_env, ENV_PATH)

    load_dotenv(ENV_PATH)

else:
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    LOG_PATH: Path = BASE_DIR / "logs.txt"
    LOG_LOCK_PATH: Path = BASE_DIR / "logs.lock"
    TELEGRAM_SESSION: Path = BASE_DIR / "telegram_bot"
    TRANSFER_PATH: Path = BASE_DIR / "transfer"
    load_dotenv()

DATABASE_URL: str = getenv("DATABASE_URL")
GOOGLE_API_KEY: str = getenv("GOOGLE_API_KEY")

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

RUNTIME_DIR: Path = Path(sys._MEIPASS) if FROZEN else BASE_DIR
FFMPEG_PATH: Path = RUNTIME_DIR / ("ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg")
REDIS_PATH: Path = RUNTIME_DIR / "redis-server"

TRANSFER_PATH.mkdir(exist_ok=True)
LOG_HANDLER: TextIO = open(LOG_PATH, "a")
LOCK: FileLock = FileLock(LOG_LOCK_PATH)

basicConfig(level=INFO, filename=LOG_PATH, filemode="a", format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", )
getLogger("httpx").setLevel(WARNING)
getLogger("telethon").setLevel(WARNING)
