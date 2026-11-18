from pathlib import Path

from src.storelimtless import User, StoreLimitless

StoreLimitless.initialize(Path("~/github/drive-bot/releases/linux/StoreLimitlessServer_0.1.0"))
user: User = StoreLimitless.login("iamloki", "whoami")
print(user.home.find("P04S03").directory.upload(
    Path("~/Downloads/Telegram Desktop/Phase 4/P04S03 - Loki - Season 1/Loki S01E04 1080p 60FPS 10bit x265 HEVC.mkv")
))
# user.home.upload("https://youtu.be/_hpy5X_145c?si=kIAUyb35BeKiGqFa").stream()
# user.home.ls.file.stream()