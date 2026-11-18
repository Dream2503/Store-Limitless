from asyncio import run_coroutine_threadsafe
from io import BytesIO
from pathlib import Path
from time import sleep
from traceback import format_exc
from typing import Any, AsyncGenerator, Generator

import discord

from backend.database import add_file, File, get_file, get_user, User
from core.data_center import DataCenter, Discord, Telegram
from core.settings import TRANSFER_PATH
from core.utils import write_log


async def upload(file: File) -> AsyncGenerator[float, None]:
    user: User | None = get_user(uid=file.uid)
    data_center: type[DataCenter] = DataCenter(file.data_center)
    write_log("INFO", data_center, "UPLOAD", user.username, f"Got file: {file}")

    if not user:
        return

    try:
        if get_file(fname=file.fname, uid=file.uid):
            write_log("ERROR", data_center, "UPLOAD", user.username, f"File `{file.fname}` already exists.")
            return

        file_path: Path = (TRANSFER_PATH / Path(file.fname).name).resolve()

        if not file_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", data_center, "UPLOAD", user.username, f"Illegal file path attempted: {file.fname}")
            return

        if not file_path.exists():
            write_log("ERROR", data_center, "UPLOAD", user.username, f"Local file not found: {file_path}")
            return

        write_log("INFO", data_center, "UPLOAD", user.username, f"Found local file: {file_path.name}")

        match file.data_center:
            case Discord.NAME:
                max_size: int = Discord.MAX_SIZE

            case Telegram.NAME:
                max_size: int = Telegram.MAX_SIZE

            case _:
                raise ValueError("Unknown data center")

        file_size: int = file_path.stat().st_size
        total_parts: int = (file_size + max_size - 1) // max_size
        write_log("INFO", data_center, "UPLOAD", user.username, f"Starting upload `{file_path.name}` ({total_parts} parts)", )

        with file_path.open("rb") as f:
            for i in range(1, total_parts + 1):
                chunk: bytes = f.read(max_size)

                if not chunk:
                    break

                filename: str = f"{file_path.name}{'' if total_parts == 1 else f'.part{i:03d}'}"

                while True:
                    try:
                        match file.data_center:
                            case Discord.NAME:
                                msg_id: int = run_coroutine_threadsafe(
                                        Discord.FILE_DUMP.send(file=discord.File(BytesIO(chunk), filename=filename)),
                                        Discord.LOOP,
                                ).result().id

                            case Telegram.NAME:
                                msg_id = (await Telegram.FILE_DUMP.send_document(
                                        chat_id=Telegram.FILE_DUMP_ID,
                                        document=BytesIO(chunk),
                                        filename=filename,
                                        write_timeout=36_000,
                                        read_timeout=36_000,
                                        connect_timeout=60,
                                        pool_timeout=36_000,
                                )).id

                        break

                    except OSError as e:
                        write_log("ERROR", data_center, "UPLOAD", user.username, f"Network error part {i}/{total_parts}, retrying: {e}")

                file.flinks.append(str(msg_id))
                progress: float = (i / total_parts) * 100
                write_log("INFO", data_center, "UPLOAD", user.username, f"Uploaded {i}/{total_parts} ({progress:.1f}%)")
                yield progress

        add_file(file)
        write_log("INFO", data_center, "UPLOAD", user.username, f"Upload complete `{file_path.name}`")
        (TRANSFER_PATH / file.fname).unlink()

    except Exception as e:
        write_log("ERROR", data_center, "UPLOAD", user.username if user else "", f"Unhandled exception: {e}\n{format_exc()}")


def download(file: File) -> Generator[float, Any, None]:
    write_log("INFO", DataCenter(file.data_center), "DOWNLOAD", str(file.uid), f"Got file: {file}")

    match file.data_center:
        case Discord.NAME:
            pass

        case Telegram.NAME:
            pass

    for i in range(10):
        sleep(0.5)
        yield float(i)
