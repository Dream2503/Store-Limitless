from io import BytesIO
from pathlib import Path
from traceback import format_exc

from telegram import File, Message, Update
from telegram.ext import ContextTypes

from backend import database
from core.data_center import Telegram
from core.settings import TRANSFER_PATH
from core.utils import write_log


async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != Telegram.FILE_DUMP_ID:
        return

    username: str = update.effective_user.username or update.effective_user.first_name

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /upload <uid> <filename>")
        return

    try:
        uid: int = int(context.args[0])
        filename: str = " ".join(context.args[1:])

    except ValueError:
        write_log("ERROR", Telegram, "UPLOAD", username, "Invalid UID provided.")
        return

    user: database.User | None = database.get_user(uid=uid)

    if not user:
        return

    try:
        if database.get_file(fname=filename, uid=uid):
            write_log("ERROR", Telegram, "UPLOAD", user.username, f"File `{filename}` already exists.")
            return

        file_path: Path = (TRANSFER_PATH / Path(filename).name).resolve()

        if not file_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", Telegram, "UPLOAD", user.username, f"Illegal file path attempted: {filename}.")
            return

        if not file_path.exists():
            write_log("ERROR", Telegram, "UPLOAD", user.username, f"Local file not found: {file_path}.")
            return

        write_log("INFO", Telegram, "UPLOAD", user.username, f"Found local file: {file_path.name}.")

        file_size: int = file_path.stat().st_size
        total_parts: int = (file_size + Telegram.MAX_SIZE - 1) // Telegram.MAX_SIZE
        links: list[str] = []
        write_log("INFO", Telegram, "UPLOAD", user.username, f"Starting upload: `{file_path.name}` ({total_parts} part(s)).")

        with file_path.open("rb") as file:
            for i in range(1, total_parts + 1):
                chunk: bytes = file.read(Telegram.MAX_SIZE)

                if not chunk:
                    break

                try:
                    msg_id: int = (await context.bot.send_document(
                            chat_id=Telegram.FILE_DUMP_ID,
                            document=BytesIO(chunk),
                            filename=f"{file_path.name}{'' if total_parts == 1 else f'.part{i:03d}'}",
                            write_timeout=36_000,
                            read_timeout=36_000,
                            connect_timeout=60,
                            pool_timeout=36_000,
                    )).id
                    links.append(str(msg_id))
                    progress: float = (i / total_parts) * 100

                    write_log(
                            "INFO", Telegram, "UPLOAD", user.username,
                            f"Uploaded part {i}/{total_parts} ({progress:.1f}%) of `{file_path.name}`. (Message ID: {msg_id})",
                    )

                except Exception as e:
                    write_log("ERROR", Telegram, "UPLOAD", user.username, f"Failed at part {i}/{total_parts}: {e}\n{format_exc()}")
                    return

        database.add_file(database.File(None, filename, links, Telegram.NAME, uid))
        write_log("INFO", Telegram, "UPLOAD", user.username, f"Completed upload: `{file_path.name}` with {len(links)} part(s).")

    except Exception as e:
        write_log("ERROR", Telegram, "UPLOAD", user.username if user else "", f"Unhandled exception during upload: {e}\n{format_exc()}")


async def download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != Telegram.FILE_DUMP_ID:
        return

    username: str = update.effective_user.username or update.effective_user.first_name

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /download <uid> <filename>")
        return

    try:
        uid: int = int(context.args[0])
        filename: str = context.args[1]

    except ValueError:
        write_log("ERROR", Telegram, "DOWNLOAD", username, "Invalid UID provided.")
        return

    user: database.User | None = database.get_user(uid=uid)

    if not user:
        return

    final_path: Path = (TRANSFER_PATH / Path(filename).name).resolve()

    try:
        if not final_path.is_relative_to(TRANSFER_PATH.resolve()):
            write_log("ERROR", Telegram, "DOWNLOAD", user.username, f"Illegal file path attempted: {filename}.")
            return

        file: database.File | None = database.get_file(fname=filename, uid=uid)

        if file:
            if file.data_center == Telegram.NAME:
                links = file.flinks
                write_log("INFO", Telegram, "DOWNLOAD", user.username, f"File `{filename}` located in database (data center: Telegram).")

            else:
                write_log("ERROR", Telegram, "DOWNLOAD", user.username, f"Unsupported data center `{file.data_center}` for file `{filename}`.")
                return

        else:
            write_log("ERROR", Telegram, "DOWNLOAD", user.username, f"File `{filename}` not found in database.")
            return

        if not links:
            write_log("ERROR", Telegram, "DOWNLOAD", user.username, f"No such file `{filename}` in database.")
            return

        total_parts: int = len(links)
        write_log("INFO", Telegram, "DOWNLOAD", user.username, f"Starting download for `{final_path.name}` ({total_parts} part(s)).")

        with final_path.open("wb") as output:
            for i, msg_id in enumerate(links, start=1):
                try:
                    msg: Message = await context.bot.get_message(chat_id=Telegram.FILE_DUMP_ID, message_id=int(msg_id))

                    if not msg.document:
                        raise ValueError("No document found in message.")

                    file_obj: File = await context.bot.get_file(msg.document.file_id)
                    buffer: BytesIO = BytesIO()
                    await file_obj.download_to_memory(buffer)
                    output.write(buffer.getvalue())
                    progress: float = (i / total_parts) * 100
                    write_log(
                            "INFO", Telegram, "DOWNLOAD", user.username,
                            f"Downloaded part {i}/{total_parts} ({progress:.1f}%) of `{final_path.name}`.",
                    )

                except Exception as e:
                    write_log("ERROR", Telegram, "DOWNLOAD", user.username, f"Failed at part {i}/{total_parts} of `{final_path.name}`: {e}")

                    if final_path.exists():
                        final_path.unlink()

                    return

        write_log("INFO", Telegram, "DOWNLOAD", user.username, f"Downloaded file `{final_path.name}` successfully.")

    except Exception as e:
        write_log("ERROR", Telegram, "DOWNLOAD", user.username if user else "", f"Unhandled exception during download of `{filename}`: {e}")
