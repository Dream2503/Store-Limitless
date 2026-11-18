from threading import Thread

import uvicorn

from core import discord_utils, telegram_utils


def main() -> None:
    threads: list[Thread] = [
        Thread(target=lambda: uvicorn.run("backend.server.app:app", host="0.0.0.0", port=8000, log_level="info"), daemon=True),
        Thread(target=discord_utils.Discord.main, daemon=True),
        Thread(target=telegram_utils.Telegram.main, daemon=True),
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()


if __name__ == "__main__":
    try:
        main()

    except:
        pass
