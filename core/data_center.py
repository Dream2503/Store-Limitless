from asyncio import AbstractEventLoop

from discord import TextChannel
from telegram.ext import Application

from core.settings import getenv


class DataCenter:
    def __new__(cls, name: str):
        match name:
            case Discord.NAME:
                return Discord

            case Telegram.NAME:
                return Telegram

            case Database.NAME:
                return Database

            case BackEnd.NAME:
                return BackEnd

        return None


class ConfigMeta(type):
    def __str__(cls):
        return cls.__name__

    def __repr__(cls):
        return cls.__name__


class Database(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Database"


class BackEnd(Database, metaclass=ConfigMeta):
    NAME: str = "BackEnd"
    MAX_SIZE: int = 10_000_000


class Discord(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Discord"
    TOKEN: str = getenv("DISCORD_TOKEN")
    ADMIN: int = int(getenv("DISCORD_ADMIN"))
    FILE_DUMP_ID: int = int(getenv("DISCORD_FILE_DUMP_ID"))
    MAX_SIZE: int = 10_000_000
    MAX_DELETE_LIMIT: int = 100
    FILE_DUMP: TextChannel
    LOOP: AbstractEventLoop


class Telegram(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Telegram"
    TOKEN: str = getenv("TELEGRAM_TOKEN")
    ADMIN: int = int(getenv("TELEGRAM_ADMIN"))
    FILE_DUMP_ID: int = int(getenv("TELEGRAM_FILE_DUMP_ID"))
    MAX_SIZE: int = 10_000_000
    MAX_DELETE_LIMIT: int = 100
    FILE_DUMP: Application
