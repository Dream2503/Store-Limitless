from bisect import bisect_right
from dataclasses import dataclass
from typing import AsyncGenerator

from core import DataCenter


@dataclass(frozen=True)
class FileChunk:
    flink: str
    start: int
    end: int


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int


def parse_range(value: str | None, size: int) -> ByteRange:
    if size <= 0:
        raise ValueError("Cannot stream an empty file")

    if value is None:
        return ByteRange(0, size - 1)

    if not value.startswith("bytes="):
        raise ValueError("Invalid Range header")

    value: str = value[6:]

    if "," in value:
        raise ValueError("Multiple ranges are not supported")

    start_text, end_text = value.split("-", 1)

    if not start_text:
        length: int = int(end_text)

        if length <= 0:
            raise ValueError("Invalid suffix range")

        return ByteRange(max(size - length, 0), size - 1)

    start: int = int(start_text)

    if start < 0 or start >= size:
        raise ValueError("Range outside file")

    end: int = size - 1 if not end_text else int(end_text)

    if end < start:
        raise ValueError("Invalid range")

    return ByteRange(start, min(end, size - 1))


def get_chunks(flinks: list[str], file_size: int) -> list[FileChunk]:
    if file_size <= 0:
        raise OSError("Invalid file size")

    total_chunks: int = (file_size + DataCenter.MAX_SIZE - 1) // DataCenter.MAX_SIZE

    if len(flinks) != total_chunks:
        raise OSError(f"Expected {total_chunks} chunks, got {len(flinks)}")

    return [
        FileChunk(flink, index * DataCenter.MAX_SIZE, min((index + 1) * DataCenter.MAX_SIZE, file_size) - 1)
        for index, flink in enumerate(flinks)
    ]


def find_chunk(chunks: list[FileChunk], position: int) -> int:
    return bisect_right([chunk.start for chunk in chunks], position) - 1


class ChunkCache:
    def __init__(self, fid: str, data_center: str):
        self.fid: str = fid
        self.data_center: DataCenter = DataCenter(data_center)

    async def get(self, part: int, flink: str) -> bytes:
        data: bytes | None = await DataCenter.get_cached_part(self.fid, part)

        if data is not None:
            return data

        data = await self.data_center.download(flink)

        if not data:
            raise OSError(f"Empty chunk: {flink}")

        await DataCenter.cache_part(self.fid, part, data)
        return data


async def stream_range(chunks: list[FileChunk], byte_range: ByteRange, cache: ChunkCache) -> AsyncGenerator[bytes, None]:
    index: int = find_chunk(chunks, byte_range.start)
    position: int = byte_range.start

    while position <= byte_range.end:
        chunk: FileChunk = chunks[index]
        data: bytes = await cache.get(index, chunk.flink)
        local_start: int = position - chunk.start
        local_end: int = min(byte_range.end, chunk.end) - chunk.start
        remaining: int = local_end - local_start + 1

        while remaining > 0:
            size: int = min(1024 * 1024, remaining)
            part: bytes = data[local_start:local_start + size]

            if not part:
                raise OSError(f"Chunk ended early: {chunk.flink}")

            yield part
            local_start += len(part)
            remaining -= len(part)

        position = chunk.end + 1
        index += 1
