import asyncio
import os
import time
from collections import OrderedDict
from pathlib import Path

from core.config import TRANSFER_PATH


class DataCenter:
    NAME: str
    TOKEN: str
    ADMIN: int
    FILE_DUMP_ID: int
    MAX_SIZE: int = 10 * 1024 * 1024

    CACHE_DIR: Path = TRANSFER_PATH / Path("cached")
    CACHE_LIMIT: int = 64 * 1024 * 1024

    _cache: OrderedDict[str, tuple[int, float]] = OrderedDict()
    _cache_size: int = 0
    _cache_lock: asyncio.Lock | None = None

    def __new__(cls, name: str):
        from core.discord_utils import Discord
        from core.telegram_utils import Telegram
        from core.github_utils import GitHub

        match name:
            case Discord.NAME:
                return Discord
            case Telegram.NAME:
                return Telegram
            case GitHub.NAME:
                return GitHub
            case Database.NAME:
                return Database
            case BackEnd.NAME:
                return BackEnd

        return None

    @staticmethod
    def _lock() -> asyncio.Lock:
        if DataCenter._cache_lock is None:
            DataCenter._cache_lock = asyncio.Lock()

        return DataCenter._cache_lock

    @staticmethod
    def _part_path(fid: str, part: int) -> Path:
        return DataCenter.CACHE_DIR / fid / f"part_{part:08d}"

    @staticmethod
    async def cache_part(fid: str, part: int, data: bytes) -> Path:
        path = DataCenter._part_path(fid, part)
        path.parent.mkdir(parents=True, exist_ok=True)

        temp = path.with_suffix(".tmp")

        await asyncio.to_thread(temp.write_bytes, data)
        os.replace(temp, path)

        key = str(path)
        new_size = len(data)

        async with DataCenter._lock():
            old = DataCenter._cache.pop(key, None)

            if old:
                DataCenter._cache_size -= old[0]

            DataCenter._cache[key] = (new_size, time.monotonic())
            DataCenter._cache_size += new_size

            await DataCenter._evict()

        return path

    @staticmethod
    async def get_cached_part(fid: str, part: int) -> bytes | None:
        path = DataCenter._part_path(fid, part)

        if not path.is_file() or path.stat().st_size <= 0:
            return None

        data = await asyncio.to_thread(path.read_bytes)
        key = str(path)
        size = len(data)

        async with DataCenter._lock():
            old = DataCenter._cache.pop(key, None)

            if old:
                DataCenter._cache_size -= old[0]

            DataCenter._cache[key] = (size, time.monotonic())
            DataCenter._cache_size += size

        return data

    @staticmethod
    async def has_cached_part(fid: str, part: int) -> bool:
        path = DataCenter._part_path(fid, part)

        if not path.is_file() or path.stat().st_size <= 0:
            return False

        await DataCenter.touch_cache(str(path))
        return True

    @staticmethod
    async def touch_cache(path: str) -> bool:
        if not os.path.isfile(path):
            async with DataCenter._lock():
                old = DataCenter._cache.pop(path, None)

                if old:
                    DataCenter._cache_size -= old[0]

            return False

        size = os.path.getsize(path)

        async with DataCenter._lock():
            old = DataCenter._cache.pop(path, None)

            if old:
                DataCenter._cache_size -= old[0]

            DataCenter._cache[path] = (size, time.monotonic())
            DataCenter._cache_size += size

        return True

    @staticmethod
    async def _evict() -> None:
        while DataCenter._cache_size > DataCenter.CACHE_LIMIT and DataCenter._cache:
            key, (size, _) = DataCenter._cache.popitem(last=False)
            path = Path(key)

            try:
                path.unlink(missing_ok=True)

                try:
                    path.parent.rmdir()
                except OSError:
                    pass

            finally:
                DataCenter._cache_size -= size

    @staticmethod
    async def clear_cache(fid: str | None = None) -> None:
        import shutil

        async with DataCenter._lock():
            if fid is None:
                if DataCenter.CACHE_DIR.exists():
                    await asyncio.to_thread(shutil.rmtree, DataCenter.CACHE_DIR)

                DataCenter._cache.clear()
                DataCenter._cache_size = 0
                return

            folder = DataCenter.CACHE_DIR / fid

            if folder.exists():
                await asyncio.to_thread(shutil.rmtree, folder)

            prefix = str(folder) + os.sep

            for key, (size, _) in list(DataCenter._cache.items()):
                if key.startswith(prefix):
                    DataCenter._cache.pop(key)
                    DataCenter._cache_size -= size

    @staticmethod
    async def initialize_cache() -> None:
        import shutil

        async with DataCenter._lock():
            if DataCenter.CACHE_DIR.exists():
                await asyncio.to_thread(shutil.rmtree, DataCenter.CACHE_DIR)

            DataCenter.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            DataCenter._cache.clear()
            DataCenter._cache_size = 0

    @staticmethod
    async def upload(chunk: bytes, filename: str) -> str:
        pass

    @staticmethod
    async def download(flink: str) -> bytes:
        pass


class ConfigMeta(type):
    def __str__(cls):
        return cls.__name__

    def __repr__(cls):
        return cls.__name__


class Database(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Database"


class BackEnd(Database, metaclass=ConfigMeta):
    NAME: str = "BackEnd"
