from traceback import format_exc

from discord.ext.commands import Context

from core.data_center import Discord
from core.discord_utils.setup import app
from core.utils import write_log


@app.event
async def on_ready():
    try:
        Discord.FILE_DUMP = app.get_channel(Discord.FILE_DUMP_ID)

        if Discord.FILE_DUMP:
            write_log("INFO", Discord, "INIT", str(app.user), f"FILE_DUMP channel initialized: {Discord.FILE_DUMP.name} (id={Discord.FILE_DUMP.id}).")

        else:
            write_log(
                    "ERROR", Discord, "INIT", str(app.user),
                    f"Failed to fetch FILE_DUMP channel with ID {Discord.FILE_DUMP_ID}. Check bot permissions.",
            )

        write_log("INFO", Discord, "INIT", str(app.user), f"Bot online and ready (id={app.user.id}).")

    except Exception as e:
        write_log("ERROR", Discord, "INIT", "", f"Initialization failure: {e}\n{format_exc()}")


@app.command()
async def clear(ctx: Context, limit: int = Discord.MAX_DELETE_LIMIT) -> None:
    deleted: int = 0

    try:
        limit = max(1, min(int(limit), Discord.MAX_DELETE_LIMIT))

    except ValueError:
        write_log("ERROR", Discord, "CLEAR", ctx.author.name, f"Invalid limit provided: '{limit}'.")
        return

    try:
        async for msg in ctx.channel.history(limit=limit):
            if msg.author in {ctx.author, app.user}:
                try:
                    await msg.delete()
                    deleted += 1

                except:
                    continue

        write_log("INFO", Discord, "CLEAR", ctx.author.name, f"Cleared {deleted} message(s) from channel `{ctx.channel.name}`.")

    except Exception as e:
        write_log("ERROR", Discord, "CLEAR", ctx.author.name, f"Failed during clear operation: {e}\n{format_exc()}")


@app.command()
async def ping(ctx: Context) -> None:
    latency: float = round(app.latency * 1000)

    try:
        await ctx.send(f"🏓 Pong! Latency: {latency} ms")
        write_log("INFO", Discord, "PING", ctx.author.name, f"Latency check successful: {latency} ms")

    except Exception as e:
        write_log("ERROR", Discord, "PING", ctx.author.name, f"Failed to respond to ping: {e}\n{format_exc()}")


@app.command()
async def shell(ctx: Context, command: str) -> None:
    if ctx.author.id != Discord.ADMIN:
        write_log("ERROR", Discord, "SHELL", ctx.author.name, f"Unauthorized shell access attempt by user ({ctx.author.id}).")
        return

    write_log("INFO", Discord, "SHELL", ctx.author.name, f"Executing shell command: {command}")

    try:
        exec(command)
        write_log("INFO", Discord, "SHELL", ctx.author.name, "Shell command executed successfully.")

    except Exception as e:
        write_log("ERROR", Discord, "SHELL", ctx.author.name, f"Shell execution failed: {e}\n{format_exc()}")
