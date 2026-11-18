from telegram.ext import Application, ApplicationBuilder, CommandHandler

from core.telegram_utils.commands import *
from core.telegram_utils.transfer import *


def main() -> None:
    try:
        write_log("INFO", Telegram, "MAIN", "", "Starting Store Limitless Bot...")

        app: Application = (ApplicationBuilder().token(Telegram.TOKEN).post_init(on_ready).build())
        app.add_handler(CommandHandler("clear", clear))
        app.add_handler(CommandHandler("ping", ping))
        app.add_handler(CommandHandler("shell", shell))
        app.add_handler(CommandHandler("upload", upload))
        app.add_handler(CommandHandler("download", download))
        app.add_error_handler(error_handler)
        app.run_polling(drop_pending_updates=True, stop_signals=None)

    except Exception as e:
        write_log("ERROR", Telegram, "MAIN", "", f"Critical error during bot startup: {e}")
