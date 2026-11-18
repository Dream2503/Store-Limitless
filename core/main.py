from threading import Thread

from core import discord_utils
from core import telegram_utils
import uvicorn


def main() -> None:
    fastapi_thread = Thread(target=lambda: uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, log_level="info"), daemon=True)
    discord_thread: Thread = Thread(target=discord_utils.main, daemon=True)
    telegram_thread: Thread = Thread(target=telegram_utils.main, daemon=True)

    fastapi_thread.start()
    discord_thread.start()
    telegram_thread.start()
    fastapi_thread.join()
    discord_thread.join()
    telegram_thread.join()


if __name__ == "__main__":
    try:
        main()

    except:
        pass
