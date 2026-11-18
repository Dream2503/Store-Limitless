"""Data-center provider factory and bounded on-disk cache for remote file chunks."""

from __future__ import annotations

import os
from asyncio import to_thread
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from core.config import TRANSFER_PATH
from core.utils_ import part_path, restore_cache_index, update_cache_entry

if TYPE_CHECKING:
    from core.utils.progress import Progress


class DataCenter:
    """Factory base for storage providers and owner of the shared chunk cache."""

    NAME: str
    TOKEN: str
    FILE_DUMP_ID: int
    MAX_SIZE: int = 10 * 1024 * 1024
    MAX_PARALLEL_UPLOADS: int
    CACHE_DIR: Path = TRANSFER_PATH / "cached"
    CACHE_LIMIT: int = 256 * 1024 * 1024

    def __new__(cls, name: str) -> type[DataCenter] | None:
        """Resolve a configured provider name to its provider class."""

        from core.utils.discord_ import Discord
        from core.utils.telegram_ import Telegram

        providers: dict[str, type[DataCenter]] = {
            Discord.NAME: Discord,
            Telegram.NAME: Telegram,
            Database.NAME: Database,
            BackEnd.NAME: BackEnd,
            Binaries.NAME: Binaries,
        }
        return providers.get(name)

    @staticmethod
    async def cache_part(fid: str, part: int, data: bytes) -> Path:
        """Atomically write a downloaded part and account for it in the LRU cache."""

        path: Path = part_path(DataCenter.CACHE_DIR, fid, part)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp: Path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")

        try:
            await to_thread(temp.write_bytes, data)
            os.replace(temp, path)

        finally:
            temp.unlink(missing_ok=True)

        await update_cache_entry(str(path), len(data), DataCenter.CACHE_LIMIT)
        return path

    @staticmethod
    async def get_cached_part(fid: str, part: int) -> bytes | None:
        """Read a cached part and refresh its recency in the LRU index."""

        path: Path = part_path(DataCenter.CACHE_DIR, fid, part)

        try:
            stat_result = await to_thread(path.stat)

        except FileNotFoundError:
            await DataCenter.touch_cache(str(path))
            return None

        if stat_result.st_size <= 0:
            await DataCenter.touch_cache(str(path))
            return None

        try:
            data: bytes = await to_thread(path.read_bytes)

        except FileNotFoundError:
            await DataCenter.touch_cache(str(path))
            return None

        await update_cache_entry(str(path), len(data), DataCenter.CACHE_LIMIT)
        return data

    @staticmethod
    async def has_cached_part(fid: str, part: int) -> bool:
        """Return whether a non-empty cached part exists and refresh its recency."""

        path: Path = part_path(DataCenter.CACHE_DIR, fid, part)

        if not path.is_file() or path.stat().st_size <= 0:
            await DataCenter.touch_cache(str(path))
            return False

        return await DataCenter.touch_cache(str(path))

    @staticmethod
    async def touch_cache(path: str) -> bool:
        """Refresh or remove one cache-index entry and enforce the configured size limit."""

        cache_path: Path = Path(path)

        if not cache_path.is_file():
            await update_cache_entry(path, None, DataCenter.CACHE_LIMIT)
            return False

        await update_cache_entry(path, cache_path.stat().st_size, DataCenter.CACHE_LIMIT)
        return cache_path.is_file()

    @staticmethod
    async def initialize_cache() -> None:
        """Restore chunk-cache bookkeeping while preserving reusable cached parts."""

        await restore_cache_index(DataCenter.CACHE_DIR, DataCenter.CACHE_LIMIT)

    @staticmethod
    async def upload(chunk: bytes, filename: str, progress: Progress) -> str:
        """Upload one chunk; concrete storage providers must implement this operation."""

        raise NotImplementedError("DataCenter subclasses must implement upload().")

    @staticmethod
    async def download(flink: str) -> bytes:
        """Download one chunk; concrete storage providers must implement this operation."""

        raise NotImplementedError("DataCenter subclasses must implement download().")


class ConfigMeta(type):
    """Display provider classes by their configured names in logs and diagnostics."""

    def __str__(cls) -> str:
        """Return the class name as its display value."""

        return cls.__name__

    def __repr__(cls) -> str:
        """Return the class name as its representation."""

        return cls.__name__


class Database(DataCenter, metaclass=ConfigMeta):
    """Marker provider representing database-backed storage operations."""

    NAME: str = "Database"


class BackEnd(Database, metaclass=ConfigMeta):
    """Marker provider used for backend-owned files and operations."""

    NAME: str = "BackEnd"


class Binaries(Database, metaclass=ConfigMeta):
    """Log source for dependency installers and system binary management."""

    NAME: str = "Binaries"
