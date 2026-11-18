"""Incrementally create a ZIP64 archive from async byte streams without staging files on disk.

Entries use ZIP's STORE method (no compression) so their total wire length can be
calculated from source-file sizes before streaming starts. This lets ordinary browser
download managers display an exact "downloaded / total" value while keeping the
archive and source data fully streamed.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ZipStreamEntry:
    """One entry in a streaming ZIP archive. A trailing slash marks a directory; `source=None` emits an empty entry."""

    name: str
    source: AsyncIterable[bytes] | None
    size: int = 0


def _dos_date_time() -> tuple[int, int]:
    # ZIP DOS timestamps start at 1980. Using a stable legal timestamp avoids timezone assumptions.
    return 0, 0x21


def _normalize_entry_name(value: str) -> str:
    """Normalize a relative ZIP path while rejecting traversal and absolute paths."""

    raw = str(value or "download").replace("\\", "/")
    is_directory = raw.endswith("/")
    if raw.startswith("/") or (len(raw) >= 3 and raw[0].isalpha() and raw[1:3] == ":/"):
        raise ValueError("ZIP entry paths must be relative")

    raw_parts: list[str] = []
    for part in raw.split("/"):
        component = part.strip()
        if component in {"", "."}:
            continue
        if component == "..":
            raise ValueError("ZIP entry path contains an unsafe component")
        raw_parts.append(component)

    if not raw_parts:
        raise ValueError("ZIP entry path cannot be empty")

    safe_parts = [
        "".join("_" if ord(character) < 32 or ord(character) == 127 else character for character in part) or "download"
        for part in raw_parts
    ]
    normalized = "/".join(safe_parts)
    return normalized + "/" if is_directory else normalized


def _entry_name(name: str) -> bytes:
    """Return a safe relative archive path encoded as UTF-8, preserving subdirectories."""

    encoded = _normalize_entry_name(name).encode("utf-8")
    if len(encoded) > 65535:
        raise ValueError("ZIP entry filename is too long")
    return encoded


def _resolved_names(entries: Sequence[ZipStreamEntry]) -> list[bytes]:
    """Resolve duplicate relative paths exactly once for sizing and streaming."""

    resolved: list[bytes] = []
    used_names: set[str] = set()

    for entry in entries:
        normalized = _normalize_entry_name(entry.name)
        is_directory = normalized.endswith("/")
        path_without_suffix = normalized[:-1] if is_directory else normalized
        parent, separator, basename = path_without_suffix.rpartition("/")
        raw_basename = basename or "download"
        candidate_basename = raw_basename
        duplicate_index = 1

        def build_name(value: str) -> str:
            prefix = f"{parent}/" if separator else ""
            suffix = "/" if is_directory else ""
            return f"{prefix}{value}{suffix}"

        name = build_name(candidate_basename)
        while name.casefold() in used_names:
            if "." in raw_basename and not raw_basename.startswith(".") and not is_directory:
                stem, extension = raw_basename.rsplit(".", 1)
                candidate_basename = f"{stem} ({duplicate_index}).{extension}"
            else:
                candidate_basename = f"{raw_basename} ({duplicate_index})"
            name = build_name(candidate_basename)
            duplicate_index += 1

        used_names.add(name.casefold())
        resolved.append(_entry_name(name))

    return resolved


def zip64_stored_content_length(entries: Sequence[ZipStreamEntry]) -> int:
    """Calculate the exact ZIP64 response length without reading any source streams."""

    total = 56 + 20 + 22  # ZIP64 EOCD, locator, and classic EOCD.
    names = _resolved_names(entries)

    for entry, name_bytes in zip(entries, names):
        size = int(entry.size)
        if size < 0:
            raise ValueError("ZIP entry size cannot be negative")
        # Local header (30) + name + local ZIP64 extra (20) + stored bytes +
        # ZIP64 data descriptor (24) + central header (46) + name + central extra (28).
        total += 30 + len(name_bytes) + 20 + size + 24 + 46 + len(name_bytes) + 28

    return total


async def stream_zip64(entries: Sequence[ZipStreamEntry]) -> AsyncIterator[bytes]:
    """Yield a valid ZIP64 archive incrementally using STORE entries.

    Since stored entry size equals source size, `zip64_stored_content_length` can
    provide an accurate HTTP Content-Length before the first byte is sent.
    CRCs and entry sizes are still computed and checked as source bytes arrive.
    """

    offset = 0
    central_records: list[bytes] = []
    resolved_names = _resolved_names(entries)

    for entry, name_bytes in zip(entries, resolved_names):
        mod_time, mod_date = _dos_date_time()
        flags = 0x0808  # UTF-8 filename and data descriptor follows the file data.
        method = 0  # STORE: no compression, exact length is known in advance.
        local_offset = offset
        local_extra = struct.pack("<HHQQ", 0x0001, 16, 0, 0)
        local_header = struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 45, flags, method, mod_time, mod_date,
            0, 0xFFFFFFFF, 0xFFFFFFFF, len(name_bytes), len(local_extra),
        ) + name_bytes + local_extra
        offset += len(local_header)
        yield local_header

        crc32 = 0
        uncompressed_size = 0
        expected_size = int(entry.size)
        if expected_size < 0:
            raise ValueError("ZIP entry size cannot be negative")

        if entry.source is not None:
            async for source_chunk in entry.source:
                if not source_chunk:
                    continue
                crc32 = zlib.crc32(source_chunk, crc32)
                uncompressed_size += len(source_chunk)
                offset += len(source_chunk)
                yield source_chunk

        if uncompressed_size != expected_size:
            raise OSError(
                f'File "{entry.name}" streamed {uncompressed_size} bytes; expected {expected_size}'
            )

        stored_size = uncompressed_size
        descriptor = struct.pack(
            "<IIQQ", 0x08074B50, crc32 & 0xFFFFFFFF, stored_size, uncompressed_size
        )
        offset += len(descriptor)
        yield descriptor

        central_extra = struct.pack("<HHQQQ", 0x0001, 24, uncompressed_size, stored_size, local_offset)
        central_header = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50, 45, 45, flags, method, mod_time, mod_date, crc32 & 0xFFFFFFFF,
            0xFFFFFFFF, 0xFFFFFFFF, len(name_bytes), len(central_extra), 0, 0, 0, 0, 0xFFFFFFFF,
        ) + name_bytes + central_extra
        central_records.append(central_header)

    central_offset = offset
    for record in central_records:
        offset += len(record)
        yield record
    central_size = offset - central_offset

    zip64_eocd_offset = offset
    zip64_end = struct.pack(
        "<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, len(central_records),
        len(central_records), central_size, central_offset,
    )
    offset += len(zip64_end)
    yield zip64_end

    locator = struct.pack("<IIQI", 0x07064B50, 0, zip64_eocd_offset, 1)
    offset += len(locator)
    yield locator

    # ZIP64 sentinel values keep the classic EOCD valid for ZIP64-aware readers.
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    yield end
