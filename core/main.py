import asyncio
from threading import Thread

import uvicorn

from core import discord_utils, telegram_utils
from core.data_center import DataCenter


async def run_server() -> None:
    config = uvicorn.Config("backend.server.app:app", host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    await DataCenter.initialize_cache()
    await telegram_utils.Telegram.initialize()
    discord_thread = Thread(target=discord_utils.Discord.main, daemon=True)
    server_task = asyncio.create_task(run_server())
    discord_thread.start()

    try:
        await server_task

    except asyncio.CancelledError:
        pass

    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await telegram_utils.Telegram.shutdown()
        discord_thread.join(timeout=5)


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        pass
