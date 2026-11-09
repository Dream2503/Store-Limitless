"""Serve the built frontend and inject its runtime API URL configuration."""

import json
import sys
from os import environ
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from core.config import HOST
from fastapi.responses import FileResponse
from fastapi.responses import Response

BASE_DIR: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))

FRONTEND_DIR: Path = (BASE_DIR / "frontend" / "dist").resolve()
INDEX_FILE: Path = FRONTEND_DIR / "index.html"

frontend_app: FastAPI = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@frontend_app.get("/config.js")
async def runtime_config(request: Request) -> Response:
    """Return the API URL for the hostname the browser used to open this LOCAL app."""

    if HOST == "LOCAL":
        # Follow the incoming browser hostname instead of any stale Cloudflare
        # URL or build-time VITE_API_URL. LAN clients therefore reach this host.
        hostname = request.url.hostname or "127.0.0.1"
        if ":" in hostname:
            hostname = f"[{hostname.strip('[]')}]"
        api_url = f"{request.url.scheme}://{hostname}:8000"
    else:
        api_url = environ.get("STORELIMITLESS_API_URL", "http://127.0.0.1:8000")

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
