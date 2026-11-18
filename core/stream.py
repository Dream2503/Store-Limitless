"""Serve seekable HTTP byte ranges from files split across a remote data center."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from re import Match, fullmatch
from typing import AsyncGenerator

from backend.database.models.file import File
from core.data_center import DataCenter


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

    def __init__(self, file: File, range_header: str | None = None) -> None:
        """Validate the file metadata and parse an optional single HTTP byte range."""

        self.file: File = file

        if self.file.size <= 0:
            raise ValueError("Cannot stream an empty file")

        total_chunks: int = (self.file.size + DataCenter.MAX_SIZE - 1) // DataCenter.MAX_SIZE

        if len(self.file.links) != total_chunks:
            raise OSError(f"Expected {total_chunks} chunks, got {len(self.file.links)}")

        self.chunks: list[Stream.FileChunk] = [
            Stream.FileChunk(flink, index * DataCenter.MAX_SIZE, min((index + 1) * DataCenter.MAX_SIZE, self.file.size) - 1)
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

    async def stream(self) -> AsyncGenerator[bytes, None]:
        """Yield at most 1 MiB at a time for the validated byte range."""

        byte_range: Stream.ByteRange = self.byte_range
        index: int = bisect_right(self._chunk_starts, byte_range.start) - 1
        position: int = byte_range.start

        while position <= byte_range.end:
            chunk: Stream.FileChunk = self.chunks[index]
            data: bytes | None = await DataCenter.get_cached_part(str(self.file.id), index)

            if data is None:
                data_center: type[DataCenter] | None = DataCenter(self.file.data_center)

                if data_center is None:
                    raise ValueError(f"Unsupported data center: {self.file.data_center}")

                data = await data_center.download(chunk.flink)

                if not data:
                    raise OSError(f"Empty chunk: {chunk.flink}")

                await DataCenter.cache_part(str(self.file.id), index, data)

            expected_size: int = chunk.end - chunk.start + 1

            if len(data) != expected_size:
                raise OSError(f"Stored chunk {chunk.flink} has {len(data)} bytes; expected {expected_size}")

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
