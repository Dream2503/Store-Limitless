"""Create and configure the StoreLimitless API application."""

import asyncio
from typing import Any

from fastapi import FastAPI

from backend.server.routers import auth, public

app: FastAPI = FastAPI()
app.include_router(auth)
app.include_router(public)


@app.on_event("startup")
async def start_video_processing_worker() -> None:
    """Start the persistent post-upload video queue after API startup."""

    from backend.server.video_processing import run_video_processing_worker

    app.state.video_processing_task = asyncio.create_task(
        run_video_processing_worker(),
        name="storelimitless-video-processing",
    )


@app.on_event("shutdown")
async def stop_video_processing_worker() -> None:
    """Stop the processing worker cleanly during intentional server shutdown."""

    task: Any = getattr(app.state, "video_processing_task", None)
    if task is None:
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
