"""Central MIME type inference for StoreLimitless files.

Python's mimetypes database can vary between operating systems and deployments.
Keep application-specific aliases deterministic so the same file is stored and
served with the same Content-Type everywhere.
"""

from mimetypes import guess_type
from pathlib import Path

MIME_TYPE_OVERRIDES: dict[str, str] = {
    ".mkv": "video/x-matroska",
}


def get_file_mime_type(filename: str, fallback: str = "application/octet-stream") -> str:
    """Return a deterministic MIME type for a filename."""
    suffix = Path(filename).suffix.lower()
    if suffix in MIME_TYPE_OVERRIDES:
        return MIME_TYPE_OVERRIDES[suffix]

    return guess_type(filename)[0] or fallback
