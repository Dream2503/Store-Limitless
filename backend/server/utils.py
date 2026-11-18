from typing import Literal
from urllib.parse import ParseResult, urlparse

from fastapi import HTTPException

from core.config import POSSIBLE_DATACENTERS, UPLOAD_JOBS

ValueType = Literal[
    "datacenter",
    "directory",
    "file_name",
    "job_id",
    "link",
]


def perform_validation(value: str, value_type: ValueType) -> str:
    value = value.strip()

    match value_type:
        case "datacenter":
            if value not in POSSIBLE_DATACENTERS:
                raise HTTPException(status_code=400, detail="Invalid data center")

        case "directory":
            value = "/" + value.strip("/") if value.strip("/") else "/"
            parts: list[str] = value.split("/")[1:]

            if any(not part or part in {".", ".."} or "\\" in part for part in parts):
                raise HTTPException(status_code=400, detail="Invalid directory")

        case "file_name":
            if not value or value in {".", ".."} or "/" in value or "\\" in value:
                raise HTTPException(status_code=400, detail="Invalid file name")

        case "job_id":
            if not value or value not in UPLOAD_JOBS:
                raise HTTPException(status_code=404, detail="Upload job not found")

        case "link":
            parsed: ParseResult = urlparse(value)

            if not value or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(status_code=400, detail="Invalid link")

    return value
