from backend.database import File
from core.download_utils.downloaders import download_google_drive


def parse_link(file: File, link: str):
    if "drive.google.com" in link:
        return download_google_drive(file, link)

    raise ValueError(f"Unsupported link: {link}")
