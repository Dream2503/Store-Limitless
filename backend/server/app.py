"""Create and configure the StoreLimitless API application."""

from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.types import RequestResponseEndpoint

from backend.server.routers import auth, public
from core.data_center import Database
from core.utils import reset_log_context, sanitize_sensitive_urls, set_log_context, write_log

app: FastAPI = FastAPI()


@app.middleware("http")
async def log_request_flow(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Log request boundaries and timing without recording credentials or signed URLs."""

    request_id: str = uuid4().hex[:12]
    context_token = set_log_context(request_id)
    started_at: float = perf_counter()
    status_code: int = 500
    request_error_logged: bool = False

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers.setdefault("X-Request-ID", request_id)
        return response

    except Exception as error:
        route = request.scope.get("route")
        route_path: str = getattr(route, "path", "<unmatched>")
        safe_error: str = sanitize_sensitive_urls(str(error))[:300]
        write_log(
            "ERROR",
            Database,
            "HTTP",
            "anonymous",
            f"request raised method={request.method} route={route_path} "
            f"error={type(error).__name__}: {safe_error}",
        )
        request_error_logged = True
        raise

    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "<unmatched>")
        duration_ms: float = (perf_counter() - started_at) * 1000
        # Routine successes (including playback range requests and status polling)
        # stay quiet. Keep server failures, unhandled authorization failures, and slow calls.
        auth_handled_routes = {
            "/login", "/auth/login", "/register", "/auth/register", "/logout", "/auth/logout",
        }
        should_log: bool = (
            status_code >= 500
            or (status_code in {401, 403} and route_path not in auth_handled_routes)
            or duration_ms >= 1000
        )
        if should_log and not request_error_logged:
            level: str = (
                "ERROR" if status_code >= 500
                else "WARNING" if status_code in {401, 403}
                else "INFO"
            )
            write_log(
                level,
                Database,
                "HTTP",
                "anonymous",
                f"request completed method={request.method} route={route_path} "
                f"status={status_code} duration_ms={duration_ms:.1f}",
            )
        reset_log_context(context_token)


app.include_router(auth)
app.include_router(public)
