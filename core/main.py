from asyncio import create_task, CancelledError, gather, run, Task
from threading import Thread

from uvicorn import Config, Server

from core.data_center import DataCenter
from core.utils.discord_ import Discord
from core.utils.telegram_ import Telegram


async def run_server() -> None:
    config: Config = Config("backend.server.app:app", host="0.0.0.0", port=8000, log_level="warning")
    server: Server = Server(config)
    await server.serve()


async def main() -> None:
    await DataCenter.initialize_cache()
    await Telegram.main()
    discord_thread: Thread = Thread(target=Discord.main, daemon=True)
    server_task: Task[None] = create_task(run_server())
    discord_thread.start()

    try:
        await server_task

    except CancelledError:
        pass

    finally:
        server_task.cancel()
        await gather(server_task, return_exceptions=True)
        await Telegram.exit()
        discord_thread.join(timeout=5)


if __name__ == "__main__":
    try:
        run(main())

    except KeyboardInterrupt:
        pass
