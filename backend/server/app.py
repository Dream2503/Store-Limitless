"""Create and configure the StoreLimitless API application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.server.routers import auth, public

app: FastAPI = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(?:localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}):5173",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
)

app.include_router(auth)
app.include_router(public)
