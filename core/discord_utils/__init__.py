from traceback import format_exc

import core.discord_utils.commands
import core.discord_utils.transfer
from core.discord_utils.setup import app
from core.module import Discord
from core.utils import write_log


def main() -> None:
    try:
        write_log("INFO", Discord, "MAIN", "", "Starting Store Limitless Bot...")
        app.run(Discord.TOKEN)

    except Exception as e:
        write_log("ERROR", Discord, "MAIN", "", f"Critical startup failure: {e}\n{format_exc()}")
        raise
