"""Discord-backed storage provider for chunk upload and retrieval."""

from __future__ import annotations

from asyncio import AbstractEventLoop, TimeoutError as AsyncTimeoutError, get_running_loop, run_coroutine_threadsafe, sleep, wrap_future
from traceback import format_exc

import discord
from aiohttp import ClientConnectionError, ClientPayloadError, ClientSession, ClientTimeout
from discord import Intents, Message, TextChannel
from discord.ext.commands import Bot

from core.data_center import ConfigMeta, DataCenter
from core.utils import write_log
from core.utils.env import DISCORD_FILE_DUMP_ID, DISCORD_TOKEN
from core.utils.progress import Progress, ProgressStream


class Discord(DataCenter, metaclass=ConfigMeta):
    """Send and retrieve storage chunks through a configured Discord text channel."""

    NAME: str = "Discord"
    MAX_PARALLEL_UPLOADS: int = 2
    TOKEN: str = DISCORD_TOKEN
    FILE_DUMP_ID: int = DISCORD_FILE_DUMP_ID
    FILE_DUMP: TextChannel
    LOOP: AbstractEventLoop
    startup_error: Exception | None = None
    INTENTS: Intents = Intents.default()
    INTENTS.messages = True
    INTENTS.message_content = True
    app: Bot = Bot(command_prefix="!", intents=INTENTS, help_command=None, heartbeat_timeout=36_000)
    ready: bool = False

    @staticmethod
    async def upload(chunk: bytes, filename: str, progress: Progress) -> str:
        """Upload one chunk and retry transient Discord/API connection failures."""

        max_attempts: int = 5

        for attempt in range(1, max_attempts + 1):
            try:
                send_future = run_coroutine_threadsafe(
                    Discord.FILE_DUMP.send(file=discord.File(ProgressStream(chunk, progress), filename=filename)),
                    Discord.LOOP,
                )
                message: Message = await wrap_future(send_future)
                return str(message.id)

            except (ClientPayloadError, ClientConnectionError, AsyncTimeoutError, TimeoutError) as error:
                if attempt >= max_attempts:
                    raise

                write_log(
                    "WARNING",
                    Discord,
                    "UPLOAD",
                    "",
                    f"Transient Discord connection error uploading {filename} "
                    f"(attempt {attempt}/{max_attempts}): {error}",
                )

            except discord.HTTPException as error:
                if not 500 <= error.status < 600 or attempt >= max_attempts:
                    raise

                write_log(
                    "WARNING",
                    Discord,
                    "UPLOAD",
                    "",
                    f"Transient Discord HTTP {error.status} uploading {filename} (attempt {attempt}/{max_attempts})",
                )
            await sleep(min(2 ** (attempt - 1), 8))

        exit(0)  # do something i dont want the return type to be ``str | None`` i want ``str``

    @staticmethod
    async def download(flink: str) -> bytes:
        """Retrieve a Discord attachment, retrying incomplete CDN reads without a short total timeout."""

        while not Discord.ready:
            await sleep(0.1)

        last_error: Exception | None = None
        timeout: ClientTimeout = ClientTimeout(total=None, sock_connect=30, sock_read=120)

        for attempt in range(1, 6):
            try:
                fetch_future = run_coroutine_threadsafe(Discord.FILE_DUMP.fetch_message(int(flink)), Discord.LOOP)
                message: Message = await wrap_future(fetch_future)

                if not message.attachments:
                    raise OSError(f"No attachment found in Discord message {flink}")

                async with ClientSession(timeout=timeout) as session:
                    async with session.get(message.attachments[0].url) as response:
                        if response.status != 200:
                            raise OSError(f"Discord CDN returned status {response.status}")

                        chunks: list[bytes] = []

                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            if chunk:
                                chunks.append(chunk)

                        data: bytes = b"".join(chunks)

                        if not data:
                            raise OSError(f"Discord CDN returned an empty attachment for message {flink}")

                        return data

            except (ClientPayloadError, ClientConnectionError, AsyncTimeoutError, TimeoutError, OSError) as error:
                last_error = error

                if attempt == 5:
                    break

                write_log("WARNING", Discord, "STREAM", "", f"CDN read for message {flink} failed (attempt {attempt}/5): {error}")
                await sleep(min(2 ** (attempt - 1), 8))

        raise OSError(f"Could not retrieve Discord attachment {flink} after 5 attempts") from last_error

    @staticmethod
    @app.event
    async def on_ready() -> None:
        """Resolve the dump channel and mark Discord ready only after initialization succeeds."""

        try:
            Discord.LOOP = get_running_loop()
            channel = Discord.app.get_channel(Discord.FILE_DUMP_ID)

            if channel is None:
                channel = await Discord.app.fetch_channel(Discord.FILE_DUMP_ID)

            if not isinstance(channel, TextChannel):
                raise RuntimeError(f"Configured dump channel {Discord.FILE_DUMP_ID} is not a text channel")

            Discord.FILE_DUMP = channel
            current_user = Discord.app.user
            identity: str = str(current_user) if current_user is not None else "unknown"
            write_log("INFO", Discord, "INIT", identity, f"FILE_DUMP channel initialized: {Discord.FILE_DUMP.name} (id={Discord.FILE_DUMP.id}).")
            write_log("INFO", Discord, "INIT", identity, f"Bot online and ready (id={current_user.id if current_user is not None else 'unknown'}).")
            Discord.ready = True
            Discord.startup_error = None

        except Exception as error:
            Discord.startup_error = error
            write_log("ERROR", Discord, "INIT", "", f"Initialization failure: {error}\n{format_exc()}")

    @staticmethod
    def main() -> None:
        """Run the Discord client in the calling worker thread."""

        try:
            write_log("INFO", Discord, "MAIN", "", "Starting Store Limitless Bot...")
            # Let discord.py messages propagate to the application's file logger.
            # Its default setup installs a console StreamHandler, which we don't want.
            Discord.app.run(Discord.TOKEN, log_handler=None)

        except Exception as error:
            Discord.startup_error = error
            write_log("ERROR", Discord, "MAIN", "", f"Critical startup failure: {error}\n{format_exc()}")
            raise

    @staticmethod
    async def shutdown() -> None:
        """Close the Discord client on the event loop that owns the client."""

        try:
            if Discord.app.is_closed():
                return

            loop: AbstractEventLoop | None = getattr(Discord, "LOOP", None)

            if loop is not None and loop.is_running():
                close_future = run_coroutine_threadsafe(Discord.app.close(), loop)
                await wrap_future(close_future)

            elif loop is None:
                write_log("WARNING", Discord, "SHUTDOWN", "", "Discord never initialized; no client loop to close.")

            else:
                write_log("WARNING", Discord, "SHUTDOWN", "", "Discord event loop is already stopped.")

            write_log("INFO", Discord, "SHUTDOWN", "", "Discord client stopped.")

        except Exception as error:
            write_log("ERROR", Discord, "SHUTDOWN", "", f"Shutdown failure: {error}\n{format_exc()}")
            raise
