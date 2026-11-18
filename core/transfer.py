from pathlib import Path
from traceback import format_exc
from typing import AsyncGenerator

from backend.database import add_file, File, get_file, get_user, User
from core.data_center import DataCenter
from core.settings import TRANSFER_PATH
from core.utils import write_log


async def upload(file: File) -> AsyncGenerator[float | int, None]:
    user: User | None = get_user(uid=file.uid)
    data_center: type[DataCenter] = DataCenter(file.data_center)

    if not user:
        return

    write_log("INFO", data_center, "UPLOAD", user.username, f"Got file: {file}")

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
        file_size: int = file_path.stat().st_size
        total_parts: int = (file_size + data_center.MAX_SIZE - 1) // data_center.MAX_SIZE
        write_log("INFO", data_center, "UPLOAD", user.username, f"Starting upload `{file_path.name}` ({total_parts} parts)", )

        with file_path.open("rb") as f:
            for i in range(1, total_parts + 1):
                chunk: bytes = f.read(data_center.MAX_SIZE)

                if not chunk:
                    break

                filename: str = f"{file_path.name}{'' if total_parts == 1 else f'.part{i:03d}'}"

                while True:
                    try:
                        msg_id: str = await data_center.upload(chunk, filename)
                        break

                    except OSError as e:
                        write_log("ERROR", data_center, "UPLOAD", user.username, f"Network error part {i}/{total_parts}, retrying: {e}")

                file.flinks.append(msg_id)
                progress: float | int = round((i / total_parts) * 100, 2)
                write_log("INFO", data_center, "UPLOAD", user.username, f"Uploaded {i}/{total_parts} ({progress:.1f}%)")
                yield progress

        add_file(file)
        write_log("INFO", data_center, "UPLOAD", user.username, f"Upload complete `{file_path.name}`")
        (TRANSFER_PATH / file.fname).unlink()

    except Exception as e:
        write_log("ERROR", data_center, "UPLOAD", user.username if user else "", f"Unhandled exception: {e}\n{format_exc()}")


async def download(file: File) -> AsyncGenerator[float | int, None]:
    data_center: type[DataCenter] = DataCenter(file.data_center)
    write_log("INFO", data_center, "DOWNLOAD", str(file.uid), f"Got file: {file}")

    try:
        total_parts: int = len(file.flinks)

        if total_parts == 0:
            write_log("ERROR", data_center, "DOWNLOAD", str(file.uid), "File has no parts")
            return

        file_path: Path = (TRANSFER_PATH / Path(file.fname).name).resolve()

        if not file_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", data_center, "DOWNLOAD", str(file.uid), f"Illegal file path attempted: {file.fname}")
            return

        write_log("INFO", data_center, "DOWNLOAD", str(file.uid), f"Starting download `{file.fname}` ({total_parts} parts)")

        with file_path.open("wb") as output:
            for i, flink in enumerate(file.flinks, 1):

                while True:
                    try:
                        chunk: bytes = await data_center.download(flink)
                        break

                    except OSError as e:
                        write_log("ERROR", data_center, "DOWNLOAD", str(file.uid), f"Network error part {i}/{total_parts}, retrying: {e}")

                output.write(chunk)
                progress: float | int = round((i / total_parts) * 100, 2)
                write_log("INFO", data_center, "DOWNLOAD", str(file.uid), f"Downloaded {i}/{total_parts} ({progress:.1f}%)")
                yield progress

        write_log("INFO", data_center, "DOWNLOAD", str(file.uid), f"Download complete `{file_path.name}`")

    except Exception as e:
        write_log("ERROR", data_center, "DOWNLOAD", str(file.uid), f"Unhandled exception: {e}\n{format_exc()}")
