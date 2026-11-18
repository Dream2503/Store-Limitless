from discord import Intents
from discord.ext.commands import Bot

intents: Intents = Intents.default()
intents.messages = True
intents.message_content = True

app: Bot = Bot(command_prefix='!', intents=intents, help_command=None, heartbeat_timeout=36_000)
