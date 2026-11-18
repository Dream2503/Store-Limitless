"""Shared logging helpers for StoreLimitless core services."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from core.data_center import DataCenter


def write_log(level: str, data_center: type[DataCenter] | DataCenter, func: str, user: str, message: str) -> None:
    """Write and flush a timestamped, lock-protected application log record."""

    from core.config import LOG_HANDLER, LOCK

    timestamp: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with LOCK:
        LOG_HANDLER.write(f"[{timestamp}] [{data_center.NAME}] [{level}] [{func}] [{user}] {message}\n")
        LOG_HANDLER.flush()


def read_chunk(path: Path, offset: int, size: int) -> bytes:
    """Read exactly the requested file slice in a worker thread."""

    with path.open("rb") as buffer:
        buffer.seek(offset)
        return buffer.read(size)


def sanitize_sensitive_urls(value: str) -> str:
    """Redact credentials, query parameters, and fragments from URLs in log/error text."""

    def redact(match: re.Match[str]) -> str:
        raw_url: str = match.group(0)
        trailing: str = ""

        while raw_url and raw_url[-1] in ".,;)]}":
            trailing = raw_url[-1] + trailing
            raw_url = raw_url[:-1]

        try:
            parsed = urlsplit(raw_url)
            hostname = parsed.hostname

            if not parsed.scheme or not hostname:
                return "<redacted-url>" + trailing

            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"

            try:
                port = parsed.port

            except ValueError:
                port = None

            netloc = f"{hostname}:{port}" if port is not None else hostname
            safe_query = "<redacted>" if parsed.query else ""
            safe_url = urlunsplit((parsed.scheme, netloc, parsed.path, safe_query, ""))
            return safe_url + trailing

        except ValueError:
            return "<redacted-url>" + trailing

    return re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE).sub(redact, value)
