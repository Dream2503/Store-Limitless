from pathlib import Path
from traceback import format_exc

from discord import Message
from discord.ext.commands import Context

import backend.database as database
from core.data_center import Discord
from core.discord_utils.setup import app
from core.settings import TRANSFER_PATH
from core.utils import write_log


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
