"""Telegram-backed storage provider for chunk upload and retrieval."""

from __future__ import annotations

from asyncio import sleep
from typing import cast

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.custom.message import Message

from core.config import TELEGRAM_SESSION
from core.data_center import ConfigMeta, DataCenter
from core.utils import write_log
from core.utils.env import TELEGRAM_API_HASH, TELEGRAM_API_ID, TELEGRAM_FILE_DUMP_ID, TELEGRAM_TOKEN
from core.utils.progress import Progress


class Telegram(DataCenter, metaclass=ConfigMeta):
    """Store file chunks as Telegram documents in the configured dump chat."""

    NAME: str = "Telegram"
    MAX_PARALLEL_UPLOADS: int = 8
    API_ID: int = TELEGRAM_API_ID
    API_HASH: str = TELEGRAM_API_HASH
    TOKEN: str = TELEGRAM_TOKEN
    FILE_DUMP_ID: int = TELEGRAM_FILE_DUMP_ID
    FILE_DUMP: TelegramClient

    @staticmethod
    async def upload(chunk: bytes, filename: str, progress: Progress) -> str:
        """Upload a chunk as a Telegram document and report transfer progress."""

        last: int = 0

        def progress_callback(current: int, total: int) -> None:
            """Accumulate newly uploaded bytes reported by Telethon."""

            nonlocal last
            progress.transfer += current - last
            last = current

        message: Message | None = cast(Message | None, await Telegram.FILE_DUMP.send_file(
            Telegram.FILE_DUMP_ID,
            chunk,
            file_name=filename,
            force_document=True,
            progress_callback=progress_callback,
        ))
        if message is None or message.document is None:
            raise OSError(f"Telegram upload failed for '{filename}'")

        return str(message.id)

    @staticmethod
    async def download(flink: str) -> bytes:
        """Download the document attached to a Telegram message ID, retrying RPC failures."""

        for attempt in range(5):
            try:
                message: Message | None = cast(Message | None, await Telegram.FILE_DUMP.get_messages(Telegram.FILE_DUMP_ID, ids=int(flink)))

                if message is None or message.media is None:
                    raise OSError(f"Telegram message not found: {flink}")

                data = await Telegram.FILE_DUMP.download_media(message, file=bytes)

                if not isinstance(data, bytes):
                    raise OSError(f"Telegram download failed: {flink}")

                return data

            except RPCError:
                if attempt == 4:
                    raise

                await sleep(1 << attempt)

        raise OSError(f"Telegram download failed: {flink}")

    @staticmethod
    async def main() -> None:
        """Start the Telegram bot client and log its connected identity."""

        try:
            Telegram.FILE_DUMP = TelegramClient(str(TELEGRAM_SESSION), Telegram.API_ID, Telegram.API_HASH)
            await Telegram.FILE_DUMP.start(bot_token=Telegram.TOKEN)
            me = await Telegram.FILE_DUMP.get_me()

            if me is None:
                raise RuntimeError("Telegram returned no account identity after connecting")

            write_log("INFO", Telegram, "INIT", me.username or str(me.id), f"Bot is online. Logged in (id={me.id})")

        except Exception as error:
            write_log("ERROR", Telegram, "INIT", "", f"Initialization failure: {error}")
            raise

    @staticmethod
    async def exit() -> None:
        """Disconnect Telegram if the client was constructed and log shutdown errors."""

        try:
            client: TelegramClient | None = getattr(Telegram, "FILE_DUMP", None)

            if client is not None:
                await client.disconnect()

            TELEGRAM_SESSION.unlink()

            write_log("INFO", Telegram, "SHUTDOWN", "", "Telegram client stopped.")

        except Exception as error:
            write_log("ERROR", Telegram, "SHUTDOWN", "", f"Shutdown failure: {error}")
