import asyncio
from pathlib import Path
from traceback import format_exc
from typing import AsyncGenerator

from backend.database import add_file, File, get_file, get_user, User
from core.config import TRANSFER_PATH
from core.data_center import DataCenter
from core.utils import write_log


async def upload(file: File) -> AsyncGenerator[float | int, None]:
    user: User | None = get_user(username=file.username)
    data_center: type[DataCenter] = DataCenter(file.data_center)

    if not user:
        return

    write_log("INFO", data_center, "UPLOAD", user.username, f"Got file: {file}")

    try:
        if get_file(name=file.name, username=file.username):
            path: Path = Path(file.name)
            stem, extension = path.stem, path.suffix
            i = 1

            while get_file(name=file.name, username=file.username):
                file.name = f"{stem}({i}){extension}"
                i += 1

        file_path: Path = (TRANSFER_PATH / file.username / Path(file.name)).resolve()

        if not file_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", data_center, "UPLOAD", user.username, f"Illegal file path attempted: {file.name}")
            return

        if not file_path.exists():
            write_log("ERROR", data_center, "UPLOAD", user.username, f"Local file not found: {file_path}")
            return

        write_log("INFO", data_center, "UPLOAD", user.username, f"Found local file: {file_path.name}")
        file_size: int = file_path.stat().st_size
        total_parts: int = (file_size + data_center.MAX_SIZE - 1) // data_center.MAX_SIZE
        write_log("INFO", data_center, "UPLOAD", user.username, f"Starting upload `{file_path.name}` ({total_parts} parts)")

        with file_path.open("rb") as f:
            chunks: list[tuple[int, bytes, str]] = []

            for i in range(1, total_parts + 1):
                chunk: bytes = f.read(data_center.MAX_SIZE)

                if not chunk:
                    break

                filename: str = f"{file_path.name}{'' if total_parts == 1 else f'.part{i:03d}'}"
                chunks.append((i, chunk, filename))

            semaphore = asyncio.Semaphore(8)

            async def upload_part(i: int, chunk: bytes, filename: str) -> tuple[int, str]:
                async with semaphore:
                    while True:
                        try:
                            msg_id: str = await data_center.upload(chunk, filename)
                            return i, msg_id

                        except OSError as e:
                            write_log("ERROR", data_center, "UPLOAD", user.username, f"Network error part {i}/{total_parts}, retrying: {e}")

            tasks = [asyncio.create_task(upload_part(i, chunk, filename)) for i, chunk, filename in chunks]
            results: dict[int, str] = {}

            for task in asyncio.as_completed(tasks):
                i, msg_id = await task
                results[i] = msg_id
                progress: float | int = round((len(results) / total_parts) * 100, 2)
                write_log("INFO", data_center, "UPLOAD", user.username, f"Uploaded {len(results)}/{total_parts} ({progress:.1f}%)")
                yield progress

            file.links = [results[i] for i in range(1, total_parts + 1)]

        add_file(file)
        write_log("INFO", data_center, "UPLOAD", user.username, f"Upload complete `{file_path.name}`")
        file_path.unlink()

    except Exception as e:
        write_log("ERROR", data_center, "UPLOAD", user.username, f"Unhandled exception: {e}\n{format_exc()}")


async def download(file: File) -> AsyncGenerator[float | int, None]:
    data_center: type[DataCenter] = DataCenter(file.data_center)
    write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Got file: {file}")

    try:
        total_parts: int = len(file.links)

        if total_parts == 0:
            write_log("ERROR", data_center, "DOWNLOAD", str(file.username), "File has no parts")
            return

        file_path: Path = (TRANSFER_PATH / file.username / Path(file.name)).resolve()

        if not file_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", data_center, "DOWNLOAD", str(file.username), f"Illegal file path attempted: {file.name}")
            return

        write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Starting download `{file.name}` ({total_parts} parts)")

        with file_path.open("wb") as output:
            for i, flink in enumerate(file.links):
                part = await DataCenter.get_cached_part(str(file.username), i)

                if part is None:
                    while True:
                        try:
                            chunk: bytes = await data_center.download(flink)
                            break

                        except OSError as e:
                            write_log("ERROR", data_center, "DOWNLOAD", str(file.username),
                                      f"Network error part {i + 1}/{total_parts}, retrying: {e}")

                    await DataCenter.cache_part(str(file.username), i, chunk)
                    part = chunk

                output.write(part)
                progress: float | int = round(((i + 1) / total_parts) * 100, 2)
                write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Downloaded {i + 1}/{total_parts} ({progress:.1f}%)")
                yield progress

        write_log("INFO", data_center, "DOWNLOAD", str(file.username), f"Download complete `{file_path.name}`")

    except Exception as e:
        write_log("ERROR", data_center, "DOWNLOAD", str(file.username), f"Unhandled exception: {e}\n{format_exc()}")
