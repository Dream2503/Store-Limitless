from io import BytesIO
from pathlib import Path
from traceback import format_exc

from discord import File, Message
from discord.ext.commands import Context

import backend.database as database
from core.data_center import Discord
from core.discord_utils.setup import app
from core.settings import TRANSFER_PATH
from core.utils import write_log


@app.command()
async def upload(ctx: Context, uid: int, filename: str) -> None:
    user: database.User | None = database.get_user(uid=uid)

    if not user:
        return

    try:
        if database.get_file(fname=filename, uid=uid):
            write_log("ERROR", Discord, "UPLOAD", user.username, f"File `{filename}` already exists.")
            return

        file_path: Path = (TRANSFER_PATH / Path(filename).name).resolve()

        if not file_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", Discord, "UPLOAD", user.username, f"Illegal file path attempted: {filename}.")
            return

        if not file_path.exists():
            write_log("ERROR", Discord, "UPLOAD", user.username, f"Local file not found: {file_path}.")
            return

        write_log("INFO", Discord, "UPLOAD", user.username, f"Found local file: {file_path.name}.")
        file_size: int = file_path.stat().st_size
        total_parts: int = (file_size + Discord.MAX_SIZE - 1) // Discord.MAX_SIZE
        links: list[str] = []
        write_log("INFO", Discord, "UPLOAD", user.username, f"Starting upload: `{file_path.name}` ({total_parts} part(s)).")

        with file_path.open("rb") as file:
            for i in range(1, total_parts + 1):
                chunk: bytes = file.read(Discord.MAX_SIZE)

                if not chunk:
                    break

                try:
                    while True:
                        try:
                            msg_id: int = (await Discord.FILE_DUMP.send(
                                    file=File(BytesIO(chunk), filename=f"{file_path.name}{'' if total_parts == 1 else f'.part{i:03d}'}"),
                            )).id
                            break

                        except OSError as e:
                            write_log("ERROR", Discord, "UPLOAD", user.username, f"Network error on part {i}/{total_parts}, retrying...: {e}")

                    links.append(str(msg_id))
                    progress: float = (i / total_parts) * 100

                    write_log(
                            "INFO", Discord, "UPLOAD", user.username,
                            f"Uploaded part {i}/{total_parts} ({progress:.1f}%) of `{file_path.name}`. (Message ID: {msg_id})",
                    )

                except Exception as e:
                    write_log("ERROR", Discord, "UPLOAD", user.username, f"Failed at part {i}/{total_parts}: {e}")
                    return

        database.add_file(database.File(None, filename, links, Discord.NAME, uid))
        write_log("INFO", Discord, "UPLOAD", user.username, f"Completed upload: `{file_path.name}` with {len(links)} part(s).")

    except Exception as e:
        write_log("ERROR", Discord, "UPLOAD", user.username if user else "", f"Unhandled exception during upload: {e}\n{format_exc()}")


@app.command()
async def download(ctx: Context, uid: int, filename: str) -> None:
    user: database.User | None = database.get_user(uid=uid)

    if not user:
        return

    final_path: Path = (TRANSFER_PATH / Path(filename).name).resolve()

    try:
        if not final_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", Discord, "DOWNLOAD", user.username, f"Illegal file path attempted: {filename}.")
            return

        file: database.File | None = database.get_file(fname=filename, uid=uid)

        if file:
            if file.data_center == Discord.NAME:
                links = file.flinks
                write_log("INFO", Discord, "DOWNLOAD", user.username, f"File `{filename}` located in database (data center: Discord).")

            else:
                write_log("ERROR", Discord, "DOWNLOAD", user.username, f"Unsupported data center `{file.data_center}` for file `{filename}`.")
                return

        else:
            write_log("ERROR", Discord, "DOWNLOAD", user.username, f"File `{filename}` not found in database.")
            return

        if not links:
            write_log("ERROR", Discord, "DOWNLOAD", user.username, f"No such file `{filename}` in database.")
            return

        total_parts: int = len(links)
        write_log("INFO", Discord, "DOWNLOAD", user.username, f"Starting download for `{final_path.name}` ({total_parts} part(s)).")

        with final_path.open("wb") as file:
            for i, msg_id in enumerate(links, start=1):
                try:
                    msg: Message = await Discord.FILE_DUMP.fetch_message(int(msg_id))

                    if not msg.attachments:
                        raise ValueError("No attachment found in message.")

                    file.write(await msg.attachments[0].read())
                    progress: float = (i / total_parts) * 100

                    write_log(
                            "INFO", Discord, "DOWNLOAD", user.username,
                            f"Downloaded part {i}/{total_parts} ({progress:.1f}%) of `{final_path.name}`.",
                    )

                except Exception as e:
                    write_log("ERROR", Discord, "DOWNLOAD", user.username, f"Failed at part {i}/{total_parts} of `{final_path.name}`: {e}")

                    if final_path.exists():
                        final_path.unlink()

                    return

        write_log("INFO", Discord, "DOWNLOAD", user.username, f"Downloaded file `{final_path.name}` successfully.")

    except Exception as e:
        write_log(
                "ERROR", Discord, "DOWNLOAD", user.username if user else "",
                f"Unhandled exception during download of `{filename}`: {e}\n{format_exc()}",
        )
