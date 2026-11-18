class DataCenter:
    NAME: str
    TOKEN: str
    ADMIN: int
    FILE_DUMP_ID: int
    MAX_SIZE: int = 10 * 1024 * 1024

    def __new__(cls, name: str):
        from core.discord_utils import Discord
        from core.telegram_utils import Telegram
        from core.github_utils import GitHub

        match name:
            case Discord.NAME:
                return Discord

            case Telegram.NAME:
                return Telegram

            case GitHub.NAME:
                return GitHub

            case Database.NAME:
                return Database

            case BackEnd.NAME:
                return BackEnd

        return None

    @staticmethod
    async def upload(chunk: bytes, filename: str) -> str:
        pass

    @staticmethod
    async def download(flink: str) -> bytes:
        pass


class ConfigMeta(type):
    def __str__(cls):
        return cls.__name__

    def __repr__(cls):
        return cls.__name__


class Database(DataCenter, metaclass=ConfigMeta):
    NAME: str = "Database"


class BackEnd(Database, metaclass=ConfigMeta):
    NAME: str = "BackEnd"
