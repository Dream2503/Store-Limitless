from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.data_center import DataCenter


def getenv(key: str) -> str:
    import os

    value: str | None = os.getenv(key)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Environment variable '{key}' is missing or empty. "
            "Check your .env file or system environment."
        )

    return value


def write_log(level: str, data_center: type[DataCenter] | DataCenter, func: str, user: str, message: str) -> None:
    from core.config import LOG_HANDLER, LOCK

    with LOCK:
        LOG_HANDLER.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{data_center.NAME}] [{level}] [{func}] [{user}] {message}\n")
        LOG_HANDLER.flush()
