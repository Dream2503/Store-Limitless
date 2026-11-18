from asyncio import AbstractEventLoop, get_running_loop, run_coroutine_threadsafe, wrap_future
from io import BytesIO
from traceback import format_exc

import discord
from discord import Intents, Message, TextChannel
from discord.ext.commands import Bot

from core.data_center import ConfigMeta, DataCenter
from core.settings import getenv
from core.utils import write_log


class Discord(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Discord"
    TOKEN: str = getenv("DISCORD_TOKEN")
    ADMIN: int = int(getenv("DISCORD_ADMIN"))
    FILE_DUMP_ID: int = int(getenv("DISCORD_FILE_DUMP_ID"))

    FILE_DUMP: TextChannel
    LOOP: AbstractEventLoop

    INTENTS: Intents = Intents.default()
    INTENTS.messages = True
    INTENTS.message_content = True

    app: Bot = Bot(command_prefix="!", intents=INTENTS, help_command=None, heartbeat_timeout=36_000, )

    @staticmethod
    async def upload(chunk: bytes, filename: str) -> str:
        return str(
                (await wrap_future(
                        run_coroutine_threadsafe(
                                Discord.FILE_DUMP.send(file=discord.File(BytesIO(chunk), filename=filename)),
                                Discord.LOOP,
                        ),
                )).id,
        )

    @staticmethod
    async def download(flink: str) -> bytes:
        message: Message = await wrap_future(run_coroutine_threadsafe(Discord.FILE_DUMP.fetch_message(int(flink)), Discord.LOOP))

        if not message.attachments:
            raise OSError(f"No attachment found in Discord message {flink}")

        return await wrap_future(run_coroutine_threadsafe(message.attachments[0].read(), Discord.LOOP, ), )

    @staticmethod
    @app.event
    async def on_ready():
        try:
            Discord.FILE_DUMP = Discord.app.get_channel(Discord.FILE_DUMP_ID)
            Discord.LOOP = get_running_loop()

            if Discord.FILE_DUMP:
                write_log(
                        "INFO", Discord, "INIT", str(Discord.app.user),
                        f"FILE_DUMP channel initialized: {Discord.FILE_DUMP.name} (id={Discord.FILE_DUMP.id}).", )
            else:
                write_log(
                        "ERROR", Discord, "INIT", "",
                        f"Failed to fetch FILE_DUMP channel with ID {Discord.FILE_DUMP_ID}. Check bot permissions.", )

            write_log("INFO", Discord, "INIT", str(Discord.app.user), f"Bot online and ready (id={Discord.app.user.id}).", )

        except Exception as e:
            write_log("ERROR", Discord, "INIT", "", f"Initialization failure: {e}\n{format_exc()}", )

    @staticmethod
    def main() -> None:
        try:
            write_log("INFO", Discord, "MAIN", "", "Starting Store Limitless Bot...", )
            Discord.app.run(Discord.TOKEN)

        except Exception as e:
            write_log("ERROR", Discord, "MAIN", "", f"Critical startup failure: {e}\n{format_exc()}", )
            raise
