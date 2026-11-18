from asyncio import get_running_loop
from traceback import format_exc

import core.discord_utils.transfer
from core.data_center import Discord
from core.discord_utils.setup import app, app
from core.utils import write_log


@app.event
async def on_ready():
    try:
        Discord.FILE_DUMP = app.get_channel(Discord.FILE_DUMP_ID)
        Discord.LOOP = get_running_loop()

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


def main() -> None:
    try:
        write_log("INFO", Discord, "MAIN", "", "Starting Store Limitless Bot...")
        app.run(Discord.TOKEN)

    except Exception as e:
        write_log("ERROR", Discord, "MAIN", "", f"Critical startup failure: {e}\n{format_exc()}")
        raise
