from io import BytesIO

from telegram import Bot, User
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from core.data_center import ConfigMeta, DataCenter
from core.settings import getenv
from core.utils import write_log


class Telegram(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Telegram"
    TOKEN: str = getenv("TELEGRAM_TOKEN")
    ADMIN: int = int(getenv("TELEGRAM_ADMIN"))
    FILE_DUMP_ID: int = int(getenv("TELEGRAM_FILE_DUMP_ID"))

    FILE_DUMP: Bot
    app: Application

    @staticmethod
    async def upload(chunk: bytes, filename: str) -> str:
        message = await Telegram.FILE_DUMP.send_document(
                chat_id=Telegram.FILE_DUMP_ID,
                document=BytesIO(chunk),
                filename=filename,
                write_timeout=36_000,
                read_timeout=36_000,
                connect_timeout=60,
                pool_timeout=36_000,
        )

        if message.document is None:
            raise OSError(f"Telegram upload failed for '{filename}'")

        return message.document.file_id

    @staticmethod
    async def download(flink: str) -> bytes:
        return bytes(await (await Telegram.FILE_DUMP.get_file(flink)).download_as_bytearray(), )

    @staticmethod
    async def on_ready(app: Application) -> None:
        try:
            Telegram.FILE_DUMP = app.bot
            user: User = await app.bot.get_me()
            write_log("INFO", Telegram, "INIT", user.name, f"Bot is online. Logged in (id={user.id})", )

        except Exception as e:
            write_log("ERROR", Telegram, "INIT", "", f"Initialization failure: {e}", )

    @staticmethod
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE, ) -> None:
        write_log("ERROR", Telegram, "HANDLER", "", f"Exception: {context.error}", )

    @staticmethod
    def main() -> None:
        try:
            write_log("INFO", Telegram, "MAIN", "", "Starting Store Limitless Bot...", )
            Telegram.app = (ApplicationBuilder().token(Telegram.TOKEN).post_init(Telegram.on_ready).build())
            Telegram.app.add_error_handler(Telegram.error_handler)
            Telegram.app.run_polling(drop_pending_updates=True, stop_signals=None)

        except Exception as e:
            write_log("ERROR", Telegram, "MAIN", "", f"Critical error during bot startup: {e}", )
