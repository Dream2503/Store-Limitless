"""Serve the built frontend and inject its runtime API URL configuration."""

import json
import sys
from os import environ
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import Response

BASE_DIR: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))

FRONTEND_DIR: Path = (BASE_DIR / "frontend" / "dist").resolve()
INDEX_FILE: Path = FRONTEND_DIR / "index.html"

frontend_app: FastAPI = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@frontend_app.get("/config.js")
async def runtime_config() -> Response:
    """Return runtime JavaScript configuration for the frontend."""

    api_url: str = environ.get("STORELIMITLESS_API_URL", "http://127.0.0.1:8000")

    return Response(
        content=(f"window.STORELIMITLESS_CONFIG = {{ API_URL: {json.dumps(api_url)} }};"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@frontend_app.get("/{path:path}")
async def serve_frontend(path: str = "") -> FileResponse:
    """Serve an existing frontend asset or fall back to the SPA entry point."""

    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=500, detail="Frontend build not found")

    requested_file: Path = (FRONTEND_DIR / path).resolve()

    if not requested_file.is_relative_to(FRONTEND_DIR):
        raise HTTPException(status_code=404, detail="Not found")

    if requested_file.is_file():
        return FileResponse(requested_file)

    return FileResponse(INDEX_FILE)
