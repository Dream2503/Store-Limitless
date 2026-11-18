from pathlib import Path

from src.storelimtless import User, StoreLimitless

user: User = StoreLimitless.login("iamloki", "whoami")
# print(user.home.find("P04S03").directories[0].upload(Path("~/Downloads/Telegram Desktop/Phase 4/P04S03 - Loki - Season 1/Loki S01E01 1080p 60FPS 10bit x265 HEVC.mkv")))
# user.home.find("Loki S01E01").files[0].stream()
