"""Composable FastAPI routers for the StoreLimitless server."""

from fastapi import APIRouter

from . import accounts, directories, public_routes, uploads

# Keep these aggregate router names stable for app.py and existing imports.
auth: APIRouter = APIRouter(prefix="/auth")
public: APIRouter = APIRouter(prefix="/public")

auth.include_router(accounts.router)
auth.include_router(directories.router)
auth.include_router(uploads.router)

public.include_router(public_routes.router)
public.include_router(uploads.public_router)
