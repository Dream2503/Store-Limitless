"""Serve seekable HTTP byte ranges from files split across a remote data center."""

from __future__ import annotations

from asyncio import Task, create_task, gather, sleep, wait_for
from bisect import bisect_right
from dataclasses import dataclass
from re import Match, fullmatch
from typing import AsyncGenerator

from backend.database.models.file import File
from core.data_center import DataCenter
from core.utils_ import part_path


class Stream:
    """Map a requested byte range onto stored parts and yield the selected bytes."""

    @dataclass(frozen=True)
    class FileChunk:
        """Describe the inclusive byte offsets occupied by one remote file part."""

        flink: str
        start: int
        end: int

    @dataclass(frozen=True)
    class ByteRange:
        """Represent an inclusive byte range within the stored file."""

        start: int
        end: int

    @classmethod
    async def prepare(cls, file: File, range_header: str | None = None) -> "Stream":
        """Build a range stream from persisted metadata without waiting for an unrelated storage read."""

        if not file.links:
            raise OSError("File has no stored data parts")

        # File.size is committed by the upload pipeline. Do not download the final
        # storage part just to calculate headers: that delays the first response byte,
        # even when the browser only requests the start of a video. Each requested
        # chunk is still validated against its expected length by _load_chunk().
        return cls(file, range_header)

    def __init__(self, file: File, range_header: str | None = None) -> None:
        """Build provider-sized chunk offsets and parse an optional single HTTP byte range."""

        self.file: File = file
        self.provider: type[DataCenter] | None = DataCenter(file.data_center)

        if self.provider is None:
            raise ValueError(f"Unsupported data center: {file.data_center}")

        self.chunk_size: int = int(self.provider.MAX_SIZE)

        if self.chunk_size <= 0:
            raise ValueError(f"{self.provider.__name__}.MAX_SIZE must be greater than zero")

        if self.file.size <= 0:
            raise ValueError("Cannot stream an empty file")

        total_chunks: int = (self.file.size + self.chunk_size - 1) // self.chunk_size

        if len(self.file.links) != total_chunks:
            raise OSError(f"Verified size requires {total_chunks} chunks, but metadata contains {len(self.file.links)}")

        self.chunks: list[Stream.FileChunk] = [
            Stream.FileChunk(flink, index * self.chunk_size, min((index + 1) * self.chunk_size, self.file.size) - 1)
            for index, flink in enumerate(self.file.links)
        ]
        self._chunk_starts: list[int] = [chunk.start for chunk in self.chunks]

        if range_header is None:
            self.byte_range: Stream.ByteRange = Stream.ByteRange(0, self.file.size - 1)
            return

        match: Match[str] | None = fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())

        if match is None:
            raise ValueError("Invalid Range header")

        raw_start, raw_end = match.groups()
        start: int | None = int(raw_start) if raw_start else None
        end: int | None = int(raw_end) if raw_end else None

        if start is None:
            if end is None or end <= 0:
                raise ValueError("Invalid suffix range")

            self.byte_range = Stream.ByteRange(max(self.file.size - end, 0), self.file.size - 1)
            return

        if start >= self.file.size:
            raise ValueError("Range outside file")

        if end is not None and end < start:
            raise ValueError("Invalid range")

        range_end: int = self.file.size - 1 if end is None else min(end, self.file.size - 1)
        self.byte_range = Stream.ByteRange(start, range_end)

    async def _load_chunk(self, index: int) -> bytes:
        """Read one cached chunk or fetch and validate it with bounded retries."""

        chunk: Stream.FileChunk = self.chunks[index]
        expected_size: int = chunk.end - chunk.start + 1
        file_id: str = str(self.file.id)
        data: bytes | None = await DataCenter.get_cached_part(file_id, index)

        if data is not None and len(data) != expected_size:
            cache_path = part_path(DataCenter.CACHE_DIR, file_id, index)
            cache_path.unlink(missing_ok=True)
            await DataCenter.touch_cache(str(cache_path))
            data = None

        if data is None:
            if self.provider is None:
                raise ValueError(f"Unsupported data center: {self.file.data_center}")

            for attempt in range(5):
                try:
                    candidate: bytes = await wait_for(self.provider.download(chunk.flink), timeout=300)

                    if len(candidate) != expected_size:
                        raise OSError(f"Remote chunk {chunk.flink} returned {len(candidate)} bytes; expected {expected_size}")

                    data = candidate
                    await DataCenter.cache_part(file_id, index, data)
                    break

                except Exception as error:
                    last_error = error

                    if attempt == 4:
                        raise OSError(f"Could not retrieve storage chunk {index + 1}/{len(self.chunks)} after 5 attempts") from last_error

                    await sleep(min(2 ** attempt, 16))

        if data is None or len(data) != expected_size:
            raise OSError(f"Stored chunk {chunk.flink} has an invalid size")

        return data

    async def stream(self) -> AsyncGenerator[bytes, None]:
        """Yield bytes in order while fetching up to eight storage links concurrently."""

        from core.utils.download_manager import ordered_parallel_downloads

        byte_range: Stream.ByteRange = self.byte_range
        first_index: int = bisect_right(self._chunk_starts, byte_range.start) - 1
        last_index: int = bisect_right(self._chunk_starts, byte_range.end) - 1
        position: int = byte_range.start

        async for index, data in _indexed_parallel_chunks(
            ordered_parallel_downloads(
                range(first_index, last_index + 1),
                self._load_chunk,
            ),
            first_index,
        ):
            chunk: Stream.FileChunk = self.chunks[index]
            expected_size: int = chunk.end - chunk.start + 1
            if len(data) != expected_size:
                raise OSError(f"Stored chunk {chunk.flink} has {len(data)} bytes; expected {expected_size}")

            local_start: int = max(0, position - chunk.start)
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


async def _indexed_parallel_chunks(stream, first_index: int):
    """Attach monotonically increasing chunk indices to ordered download results."""

    index = first_index
    async for data in stream:
        yield index, data
        index += 1
