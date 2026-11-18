"""Define filesystem locations, supported providers, and process-wide logging configuration."""

from __future__ import annotations

import os
import platform
import sys
from logging import INFO, WARNING, basicConfig, getLogger
from pathlib import Path
from typing import Literal, TextIO

from filelock import FileLock

HOST: Literal["LOCAL", "CLOUD"] = "LOCAL"
FROZEN: bool = bool(getattr(sys, "frozen", False))

if FROZEN:
    if platform.system() == "Windows":
        BASE_DIR: Path = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "StoreLimitless"

    else:
        BASE_DIR = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "storeLimitless"

    BASE_DIR.mkdir(parents=True, exist_ok=True)

else:
    BASE_DIR = Path(__file__).resolve().parent.parent / "data"

BACKUP_DIR: Path = BASE_DIR.parent / "backend" / "database" / "backup"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

RESOURCE_DIR: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
FRONTEND_DIST: Path = RESOURCE_DIR / "frontend" / "dist"

LOG_PATH: Path = BASE_DIR / "logs.log"
LOG_LOCK_PATH: Path = BASE_DIR / "logs.lock"
TELEGRAM_SESSION: Path = BASE_DIR / "telegram_bot"
TRANSFER_PATH: Path = BASE_DIR / "transfer"
TRANSFER_PATH.mkdir(parents=True, exist_ok=True)

AVAILABLE_DATACENTERS: frozenset[str] = frozenset({"Discord", "Telegram"})

LOG_HANDLER: TextIO = open(LOG_PATH, "a", encoding="utf-8")
LOCK: FileLock = FileLock(LOG_LOCK_PATH)

basicConfig(level=INFO, filename=LOG_PATH, filemode="a", format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
getLogger("httpx").setLevel(WARNING)
getLogger("telethon").setLevel(WARNING)
getLogger("discord.client").setLevel(WARNING)
getLogger("discord.gateway").setLevel(WARNING)
