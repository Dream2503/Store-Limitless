from discord import TextChannel
from telegram import ChatFullInfo

from core.settings import getenv


class DataCenter:
    pass


class ConfigMeta(type):
    def __str__(cls):
        return cls.__name__

    def __repr__(cls):
        return cls.__name__


class Database(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Database"
    pass


class BackEnd(Database, metaclass=ConfigMeta):
    NAME: str = "BackEnd"
    pass


class Discord(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Discord"
    TOKEN: str = getenv("DISCORD_TOKEN")
    ADMIN: int = int(getenv("DISCORD_ADMIN"))
    FILE_DUMP_ID: int = int(getenv("DISCORD_FILE_DUMP_ID"))
    MAX_SIZE: int = int(getenv("DISCORD_MAX_SIZE"))
    MAX_DELETE_LIMIT: int = int(getenv("DISCORD_MAX_DELETE_LIMIT"))
    FILE_DUMP: TextChannel | None = None


class Telegram(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Telegram"
    TOKEN: str = getenv("TELEGRAM_TOKEN")
    ADMIN: int = int(getenv("TELEGRAM_ADMIN"))
    FILE_DUMP_ID: int = int(getenv("TELEGRAM_FILE_DUMP_ID"))
    MAX_SIZE: int = int(getenv("TELEGRAM_MAX_SIZE"))
    MAX_DELETE_LIMIT: int = int(getenv("TELEGRAM_MAX_DELETE_LIMIT"))
    FILE_DUMP: ChatFullInfo | None = None
