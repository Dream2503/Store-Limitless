from asyncio import sleep
from io import BytesIO

import telegram
from telegram import Bot, Message, User
from telegram.error import NetworkError

from core.config import getenv
from core.data_center import ConfigMeta, DataCenter
from core.utils import write_log


class Telegram(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Telegram"
    TOKEN: str = getenv("TELEGRAM_TOKEN")
    ADMIN: int = int(getenv("TELEGRAM_ADMIN"))
    FILE_DUMP_ID: int = int(getenv("TELEGRAM_FILE_DUMP_ID"))
    FILE_DUMP: Bot

    @staticmethod
    async def upload(chunk: bytes, filename: str) -> str:
        message: Message = await Telegram.FILE_DUMP.send_document(chat_id=Telegram.FILE_DUMP_ID, document=BytesIO(chunk), filename=filename,
                                                                  write_timeout=36_000, read_timeout=36_000, connect_timeout=60, pool_timeout=36_000)

        if message.document is None:
            raise OSError(f"Telegram upload failed for '{filename}'")

        return message.document.file_id

    @staticmethod
    async def download(flink: str) -> bytes:
        for attempt in range(5):
            try:
                file: telegram.File = await Telegram.FILE_DUMP.get_file(flink)
                return bytes(await file.download_as_bytearray())

            except NetworkError:
                if attempt == 4:
                    raise

                await sleep(1 << attempt)

        raise OSError(f"Telegram download failed: {flink}")

    @staticmethod
    async def initialize() -> None:
        try:
            Telegram.FILE_DUMP = Bot(token=Telegram.TOKEN)
            me: User = await Telegram.FILE_DUMP.get_me()
            write_log("INFO", Telegram, "INIT", me.username or str(me.id), f"Bot is online. Logged in (id={me.id})")

        except Exception as e:
            write_log("ERROR", Telegram, "INIT", "", f"Initialization failure: {e}")
            raise

    @staticmethod
    async def shutdown() -> None:
        try:
            if hasattr(Telegram, "FILE_DUMP"):
                await Telegram.FILE_DUMP.shutdown()

            write_log("INFO", Telegram, "SHUTDOWN", "", "Telegram client stopped.")

        except Exception as e:
            write_log("ERROR", Telegram, "SHUTDOWN", "", f"Shutdown failure: {e}")
