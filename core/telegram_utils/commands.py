from time import perf_counter
from traceback import format_exc

from backend import database
from core.module import Telegram
from core.utils import write_log
from telegram import Message, Update, User
from telegram.ext import ContextTypes


async def on_ready(app):
    user: User = await app.bot.get_me()

    try:
        Telegram.FILE_DUMP = await app.bot.get_chat(Telegram.FILE_DUMP_ID)

        if Telegram.FILE_DUMP:
            write_log("INFO", Telegram, "INIT", user.name, f"FILE_DUMP chat set: {Telegram.FILE_DUMP.title} (id={Telegram.FILE_DUMP.id})")

        else:
            write_log("ERROR", Telegram, "INIT", user.name, f"Failed to fetch FILE_DUMP chat with ID {Telegram.FILE_DUMP_ID}.")

    except Exception as e:
        write_log("ERROR", Telegram, "INIT", user.name, f"Error fetching FILE_DUMP chat: {e}")

    write_log("INFO", Telegram, "INIT", user.name, f"Bot is online. Logged in (id={user.id})")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    write_log("ERROR", Telegram, "HANDLER", "", f"Exception: {context.error}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != Telegram.FILE_DUMP_ID:
        return

    username: str = update.effective_user.username or update.effective_user.first_name
    deleted: int = 0

    try:
        if not context.args:
            await update.message.reply_text("Usage: /clear <limit>")
            write_log("ERROR", Telegram, "CLEAR", username, "No limit provided.")
            return

        try:
            limit: int = max(1, min(int(context.args[0]), Telegram.MAX_DELETE_LIMIT))

        except ValueError:
            write_log("ERROR", Telegram, "CLEAR", username, f"Invalid limit provided: '{context.args[0]}'")
            return

        database.clear_file()

        for i in range(limit):
            try:
                await context.bot.delete_message(chat_id=Telegram.FILE_DUMP_ID, message_id=update.message.message_id - i)
                deleted += 1

            except:
                continue

        write_log("INFO", Telegram, "CLEAR", username, f"Cleared {deleted} message(s).")

    except Exception as e:
        write_log("ERROR", Telegram, "CLEAR", username, f"Failed during clear operation: {e}\n{format_exc()}")


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != Telegram.FILE_DUMP_ID:
        return

    username: str = update.effective_user.username or update.effective_user.first_name

    try:
        start: float = perf_counter()
        msg: Message = await update.message.reply_text("Pinging...")
        end: float = perf_counter()
        latency: float = round((end - start) * 1000, 2)
        await msg.edit_text(f"🏓 Pong! Latency: {latency} ms")
        write_log("INFO", Telegram, "PING", username, f"Latency check successful: {latency} ms")

    except Exception as e:
        write_log("ERROR", Telegram, "PING", username, f"Failed to respond to ping: {e}")


async def shell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != Telegram.FILE_DUMP_ID:
        return

    username: str = update.effective_user.username or update.effective_user.first_name

    if update.effective_user.id != Telegram.ADMIN:
        write_log("ERROR", Telegram, "SHELL", username, f"Unauthorized shell access attempt by user ({update.effective_user.id}).")
        return

    if not context.args:
        await update.message.reply_text("Usage: /shell python-code")
        return

    command: str = " ".join(context.args)

    write_log("INFO", Telegram, "SHELL", username, f"Executing shell command: {command}")

    try:
        exec(command)
        write_log("INFO", Telegram, "SHELL", username, "Shell command executed successfully.")

    except Exception as e:
        write_log("ERROR", Telegram, "SHELL", username, f"Shell execution failed: {e}")
