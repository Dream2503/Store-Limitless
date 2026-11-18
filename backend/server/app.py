"""Create and configure the StoreLimitless API application."""

from fastapi import FastAPI

from backend.server.routers import auth, public

app: FastAPI = FastAPI()
app.include_router(auth)
app.include_router(public)
