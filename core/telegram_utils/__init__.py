from telegram import User
from telegram.ext import Application, ApplicationBuilder, CommandHandler

from core.telegram_utils.transfer import *
from core.utils import write_log


async def on_ready(app):
    user: User = await app.bot.get_me()

    try:
        Telegram.FILE_DUMP = app.bot

        if Telegram.FILE_DUMP:
            write_log("INFO", Telegram, "INIT", user.name, f"FILE_DUMP chat set: {Telegram.FILE_DUMP.title} (id={Telegram.FILE_DUMP.id})")

        else:
            write_log("ERROR", Telegram, "INIT", user.name, f"Failed to fetch FILE_DUMP chat with ID {Telegram.FILE_DUMP_ID}.")

    except Exception as e:
        write_log("ERROR", Telegram, "INIT", user.name, f"Error fetching FILE_DUMP chat: {e}")

    write_log("INFO", Telegram, "INIT", user.name, f"Bot is online. Logged in (id={user.id})")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    write_log("ERROR", Telegram, "HANDLER", "", f"Exception: {context.error}")


def main() -> None:
    try:
        write_log("INFO", Telegram, "MAIN", "", "Starting Store Limitless Bot...")

        app: Application = ApplicationBuilder().token(Telegram.TOKEN).post_init(on_ready).build()
        app.add_handler(CommandHandler("download", download))
        app.add_error_handler(error_handler)
        app.run_polling(drop_pending_updates=True, stop_signals=None)

    except Exception as e:
        write_log("ERROR", Telegram, "MAIN", "", f"Critical error during bot startup: {e}")
