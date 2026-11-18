from bisect import bisect_right
from dataclasses import dataclass
from re import fullmatch
from typing import AsyncGenerator

from backend.database.models.file import File
from core.data_center import DataCenter


class Stream:
    @dataclass(frozen=True)
    class FileChunk:
        flink: str
        start: int
        end: int

    @dataclass(frozen=True)
    class ByteRange:
        start: int
        end: int

    def __init__(self, file: File, range_header: str | None = None):
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

        if range_header is None:
            self.byte_range: Stream.ByteRange = Stream.ByteRange(0, self.file.size - 1)

        else:
            match = fullmatch(r"bytes=(\d*)-(\d*)", range_header)

            if match is None:
                raise ValueError("Invalid Range header")

            start, end = (int(value) if value else None for value in match.groups())

            if start is None:
                if not end or end <= 0:
                    raise ValueError("Invalid suffix range")

                self.byte_range = Stream.ByteRange(max(self.file.size - end, 0), self.file.size - 1)

            else:
                if start >= self.file.size:
                    raise ValueError("Range outside file")

                end = self.file.size - 1 if end is None else end

                if end < start:
                    raise ValueError("Invalid range")

                self.byte_range = Stream.ByteRange(start, min(end, self.file.size - 1))

    async def stream(self) -> AsyncGenerator[bytes, None]:
        chunks: list[Stream.FileChunk] = self.chunks
        byte_range: Stream.ByteRange = self.byte_range
        index: int = bisect_right([chunk.start for chunk in chunks], byte_range.start) - 1
        position: int = byte_range.start

        while position <= byte_range.end:
            chunk: Stream.FileChunk = chunks[index]
            data: bytes | None = await DataCenter.get_cached_part(str(self.file.id), index)

            if data is None:
                data_center: DataCenter = DataCenter(self.file.data_center)
                data = await data_center.download(chunk.flink)

                if not data:
                    raise OSError(f"Empty chunk: {chunk.flink}")

                await DataCenter.cache_part(str(self.file.id), index, data)

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
