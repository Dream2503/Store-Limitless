from asyncio import sleep

from telethon import TelegramClient
from telethon.errors import RPCError

from core.config import TELEGRAM_SESSION
from core.data_center import ConfigMeta, DataCenter
from core.utils import write_log, Progress, getenv


class Telegram(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Telegram"
    API_ID: int = int(getenv("TELEGRAM_API_ID"))
    API_HASH: str = getenv("TELEGRAM_API_HASH")
    TOKEN: str = getenv("TELEGRAM_TOKEN")
    FILE_DUMP_ID: int = int(getenv("TELEGRAM_FILE_DUMP_ID"))
    FILE_DUMP: TelegramClient

    @staticmethod
    async def upload(chunk: bytes, filename: str, progress: Progress) -> str:
        last: int = 0

        def progress_callback(current: int, total: int) -> None:
            nonlocal last
            progress.transfer += current - last
            last = current

        message = await Telegram.FILE_DUMP.send_file(
            Telegram.FILE_DUMP_ID,
            chunk,
            file_name=filename,
            force_document=True,
            progress_callback=progress_callback,
        )

        if message is None or message.document is None:
            raise OSError(f"Telegram upload failed for '{filename}'")

        return str(message.id)

    @staticmethod
    async def download(flink: str) -> bytes:
        for attempt in range(5):
            try:
                message = await Telegram.FILE_DUMP.get_messages(Telegram.FILE_DUMP_ID, ids=int(flink))

                if message is None or message.media is None:
                    raise OSError(f"Telegram message not found: {flink}")

                data: bytes = await Telegram.FILE_DUMP.download_media(message, file=bytes)

                if data is None:
                    raise OSError(f"Telegram download failed: {flink}")

                return data

            except RPCError:
                if attempt == 4:
                    raise

                await sleep(1 << attempt)

        raise OSError(f"Telegram download failed: {flink}")

    @staticmethod
    async def main() -> None:
        try:
            Telegram.FILE_DUMP = TelegramClient(str(TELEGRAM_SESSION), Telegram.API_ID, Telegram.API_HASH)
            await Telegram.FILE_DUMP.start(bot_token=Telegram.TOKEN)

            me = await Telegram.FILE_DUMP.get_me()
            write_log("INFO", Telegram, "INIT", me.username or str(me.id), f"Bot is online. Logged in (id={me.id})")

        except Exception as e:
            write_log("ERROR", Telegram, "INIT", "", f"Initialization failure: {e}")
            raise

    @staticmethod
    async def exit() -> None:
        try:
            if hasattr(Telegram, "FILE_DUMP"):
                await Telegram.FILE_DUMP.disconnect()

            write_log("INFO", Telegram, "SHUTDOWN", "", "Telegram client stopped.")

        except Exception as e:
            write_log("ERROR", Telegram, "SHUTDOWN", "", f"Shutdown failure: {e}")
