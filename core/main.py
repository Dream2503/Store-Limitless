"""Application entry point coordinating API, frontend, FTP, Redis, and storage bots."""

from __future__ import annotations

import sys
from asyncio import CancelledError, Task, create_task, gather, get_running_loop, run, sleep, to_thread
from os import environ, execv, kill
from pathlib import Path
from shutil import rmtree, which
from signal import SIGTERM
from subprocess import DEVNULL, STDOUT, Popen, run as subprocess_run
from tempfile import gettempdir
from threading import Thread
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from fastapi.middleware.cors import CORSMiddleware
from uvicorn import Config, Server

from backend.database.redis_server import Redis
from backend.ftp.server import StoreLimitlessFTPServer
from backend.server.app import app
from backend.server.frontend import frontend_app
from core.config import HOST, TRANSFER_PATH
from core.data_center import DataCenter
from core.utils.binaries import start_cloudflare_tunnel, start_redis_server
from core.utils.discord_ import Discord
from core.utils.telegram_ import Telegram
from core.utils.telegram_link import login_telegram_user_session
from core.utils_ import get_local_ip, stop_process, ensure_frontend_dependencies, register_vercel_backend_tunnel


def _release_port(port: int) -> None:
    """Stop stale processes listening on a StoreLimitless service port before startup."""

    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$listeners = Get-NetTCPConnection -LocalPort {port} -State Listen "
                "-ErrorAction SilentlyContinue; "
                "foreach ($listener in $listeners) { "
                "if ($listener.OwningProcess -ne $PID) { "
                "Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue "
                "} }"
            ),
        ]
        subprocess_run(command, stdout=DEVNULL, stderr=DEVNULL, check=False, timeout=10)
        return

    fuser = which("fuser")
    if fuser is not None:
        subprocess_run(
            [fuser, "-k", f"{port}/tcp"],
            stdout=DEVNULL,
            stderr=DEVNULL,
            check=False,
            timeout=10,
        )
        return

    lsof = which("lsof")
    if lsof is not None:
        result = subprocess_run(
            [lsof, "-ti", f"tcp:{port}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        for value in result.stdout.split():
            try:
                process_id = int(value)
                kill(process_id, SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError):
                continue
        return

    raise RuntimeError(
        f"Cannot check for an old process using port {port}; install 'fuser' or 'lsof', then run StoreLimitless again."
    )


def _http_ready(url: str) -> bool:
    """Return whether an HTTP endpoint has begun responding successfully."""

    try:
        with urlopen(url, timeout=1) as response:
            return 200 <= response.status < 400
    except HTTPError as error:
        return 200 <= error.code < 400
    except (URLError, TimeoutError, OSError):
        return False


def _read_startup_log(path: Path, line_count: int = 30) -> str:
    """Return the last startup-log lines for a process that failed to start."""

    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-line_count:])
    except OSError:
        return "No startup log was available."


async def main() -> None:
    """Start all StoreLimitless services and cleanly stop them on exit."""

    subprocess_run("cls" if sys.platform == "win32" else "clear", shell=True)
    frozen: bool = bool(getattr(sys, "frozen", False))
    frontend_dir: Path | None = None

    if not frozen and HOST != "CLOUD":
        frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
        await to_thread(ensure_frontend_dependencies, cast(Path, frontend_dir))

    redis_process: Popen[bytes] | None = start_redis_server()
    frontend_process: Popen[bytes] | None = None
    frontend_log_path: Path = Path(gettempdir()) / "storelimitless-vite.log"
    frontend_server: Server | None = None
    frontend_task: Task[None] | None = None
    server: Server | None = None
    server_task: Task[None] | None = None
    cloudflare_processes: list[Popen[str]] = []
    ftp_server: StoreLimitlessFTPServer | None = None
    ftp_started: bool = False
    discord_thread: Thread | None = None
    discord_started: bool = False
    telegram_started: bool = False
    restart_requested: bool = False

    try:
        await Redis.initialize()
        await DataCenter.initialize_cache()
        await Telegram.main()
        telegram_started = True

        discord_thread = Thread(target=Discord.main, daemon=True, name="storelimitless-discord")
        ftp_server = StoreLimitlessFTPServer(get_running_loop())
        ip: str = get_local_ip()

        if HOST == "CLOUD":
            api_tunnel, api_url = await to_thread(start_cloudflare_tunnel, "http://127.0.0.1:8000", "API")
            cloudflare_processes.append(api_tunnel)
            website_url = "https://storelimitless.vercel.app"
            environ["STORELIMITLESS_API_URL"] = api_url
            environ["VITE_API_URL"] = api_url
            app.add_middleware(
                CORSMiddleware,
                # The stable Vercel API host redirects public byte-range downloads to
                # the active Cloudflare tunnel. Browsers can send Origin: null on the
                # redirected range preflight, so explicitly allow it for this token-based
                # download flow; the other authenticated API routes still require a bearer token.
                allow_origins=[website_url, "null"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition", "ETag"],
            )

        elif HOST == "LOCAL":
            api_url = f"http://{ip}:8000"
            website_url = f"http://{ip}:5173"
            environ["STORELIMITLESS_API_URL"] = api_url
            environ["VITE_API_URL"] = api_url
            app.add_middleware(
                CORSMiddleware,
                allow_origins=[website_url],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition", "ETag"],
            )

        else:
            raise ValueError(f"Unsupported HOST configuration: {HOST!r}")

        # Clear a stale process left behind by a previously interrupted launch.
        _release_port(8000)
        server = Server(Config(app, host="0.0.0.0", port=8000, log_level="warning"))
        server_task = create_task(server.serve(), name="storelimitless-api")

        if HOST == "CLOUD":
            await to_thread(register_vercel_backend_tunnel, api_url)

        if HOST == "LOCAL":
            if frozen:
                frontend_server = Server(Config(frontend_app, host="0.0.0.0", port=5173, log_level="warning"))
                frontend_task = create_task(frontend_server.serve(), name="storelimitless-frontend")

            else:
                if frontend_dir is None:
                    raise RuntimeError("Frontend source directory was not initialized.")

                # The main Python entry point owns the Vite process. Remove an orphaned
                # Vite instance so it cannot serve stale code or bind an unexpected port.
                _release_port(5173)

                frontend_env: dict[str, str] = environ.copy()
                frontend_env["VITE_API_URL"] = api_url
                frontend_env["STORELIMITLESS_API_URL"] = api_url
                frontend_env["BROWSER"] = "none"

                with frontend_log_path.open("w", encoding="utf-8") as frontend_log:
                    frontend_process = Popen(
                        [
                            "npm", "run", "dev", "--",
                            "--host", "0.0.0.0",
                            "--port", "5173",
                            "--strictPort",
                        ],
                        cwd=str(frontend_dir),
                        env=frontend_env,
                        stdout=frontend_log,
                        stderr=STDOUT,
                    )

                # Don't silently continue if Vite crashed or the wrong server owns port 5173.
                for _ in range(60):
                    if frontend_process.poll() is not None:
                        raise RuntimeError(
                            "Vite failed to start. Startup output:\\n"
                            + _read_startup_log(frontend_log_path)
                        )

                    if await to_thread(_http_ready, "http://127.0.0.1:5173/"):
                        break

                    await sleep(0.25)
                else:
                    raise RuntimeError(
                        "Vite did not become ready on port 5173 within 15 seconds. Startup output:\\n"
                        + _read_startup_log(frontend_log_path)
                    )

                print("Frontend   : Vite ready at " + website_url)
                print("Vite API   : " + frontend_env["VITE_API_URL"])
                print("Vite log   : " + str(frontend_log_path))

        discord_thread.start()
        discord_started = True
        ftp_server.start()
        ftp_started = True

        try:
            while not Discord.ready:
                if server_task.done():
                    await server_task

                if Discord.startup_error is not None:
                    raise RuntimeError("Discord failed to initialize") from Discord.startup_error

                if discord_started and discord_thread is not None and not discord_thread.is_alive():
                    raise RuntimeError("Discord client stopped before becoming ready")

                await sleep(0.1)

            print("\n" + "=" * 50)
            print("Servers started")
            print("=" * 50)
            print(f"FastAPI   : {api_url}")

            if HOST == "CLOUD":
                print("Stable API: https://storelimitlessbackend.vercel.app")

            print(f"Website   : {website_url}")
            print(f"FTP       : ftp://{ip}:2121")
            print("Redis     : redis://127.0.0.1:6379")
            print("=" * 50 + "\n")
            server_mode: bool = False

            while True:
                try:
                    prompt: str = "" if server_mode else "StoreLimitless> "
                    command: str = (await to_thread(input, prompt)).strip().lower()

                    if command == "server":
                        server_mode = not server_mode

                        if server_mode:
                            print("Server mode enabled. Interactive prompt hidden.")

                        else:
                            print("Interactive prompt restored.")

                    elif command in {
                        "telegram-login",
                        "login-telegram",
                        "telegram-login --new",
                        "login-telegram --new",
                        "telegram-login-new",
                    }:
                        try:
                            force_new: bool = command in {
                                "telegram-login --new",
                                "login-telegram --new",
                                "telegram-login-new",
                            }
                            await to_thread(run, login_telegram_user_session(force_new=force_new))
                        except Exception as error:
                            print(f"Telegram user login failed: {error}")

                    elif command == "restart":
                        print("Restarting StoreLimitless after shutting down its services...")
                        restart_requested = True
                        break

                    elif command in {"exit", "stop"}:
                        print("Shutting down StoreLimitless...")
                        break

                    elif command:
                        print(f"Unknown command: {command}")

                except (EOFError, KeyboardInterrupt):
                    break

        except CancelledError:
            pass

    finally:
        if server is not None:
            server.should_exit = True

        if frontend_server is not None:
            frontend_server.should_exit = True

        background_tasks: list[Task[None]] = [task for task in (server_task, frontend_task) if task is not None]

        if background_tasks:
            await gather(*background_tasks, return_exceptions=True)

        if ftp_server is not None and ftp_started:
            ftp_server.stop()
            ftp_server.join(timeout=5)

        if discord_started and discord_thread is not None:
            try:
                await Discord.shutdown()

            except Exception:
                pass

            discord_thread.join(timeout=5)

        if telegram_started:
            await Telegram.exit()

        await Redis.close()

        for process in cloudflare_processes:
            stop_process(process)

        if redis_process is not None:
            stop_process(redis_process)

        if frontend_process is not None:
            stop_process(frontend_process)

        rmtree(TRANSFER_PATH, ignore_errors=True)

    if restart_requested:
        if frozen:
            execv(sys.executable, [sys.executable, *sys.argv[1:]])

        else:
            original_args: list[str] = list(getattr(sys, "orig_argv", []))[1:]

            if not original_args:
                original_args = sys.argv

            execv(sys.executable, [sys.executable, *original_args])


if __name__ == "__main__":
    try:
        run(main())

    except KeyboardInterrupt:
        pass
