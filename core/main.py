import subprocess
import sys
from asyncio import CancelledError, Task, create_task, gather, get_running_loop, run, sleep, to_thread
from os import environ, execv
from pathlib import Path
from shutil import rmtree
from socket import AF_INET, SOCK_DGRAM, socket
from subprocess import Popen, TimeoutExpired, DEVNULL
from threading import Thread

from fastapi.middleware.cors import CORSMiddleware
from uvicorn import Config, Server

from backend.database.redis_server import Redis
from backend.ftp.server import StoreLimitlessFTPServer
from backend.server.app import app
from backend.server.frontend import frontend_app
from core.config import TRANSFER_PATH, HOST
from core.data_center import DataCenter
from core.utils.binaries import start_redis_server, start_cloudflare_tunnel
from core.utils.discord_ import Discord
from core.utils.telegram_ import Telegram


async def main() -> None:
    subprocess.run(["clear"], check=False)

    redis_process: Popen | None = start_redis_server()
    frontend_process: Popen | None = None
    frontend_server: Server | None = None
    frontend_task: Task[None] | None = None
    cloudflare_processes: list[Popen] = []
    restart: bool = False
    frozen: bool = getattr(sys, "frozen", False)

    try:
        await Redis.initialize()
        await DataCenter.initialize_cache()
        await Telegram.main()

        discord_thread: Thread = Thread(target=Discord.main, daemon=True)
        ftp_server = StoreLimitlessFTPServer(get_running_loop())

        with socket(AF_INET, SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip: str = sock.getsockname()[0]

        if HOST == "CLOUD":
            api_tunnel, api_url = await to_thread(start_cloudflare_tunnel, "http://127.0.0.1:8000", "API")
            cloudflare_processes.append(api_tunnel)

            website_tunnel, website_url = await to_thread(start_cloudflare_tunnel, "http://127.0.0.1:5173", "Website")
            cloudflare_processes.append(website_tunnel)

            environ["STORELIMITLESS_API_URL"] = api_url
            environ["VITE_API_URL"] = api_url

            app.add_middleware(CORSMiddleware, allow_origins=[website_url], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

        elif HOST == "LOCAL":
            api_url = f"http://{ip}:8000"
            website_url = f"http://{ip}:5173"
            environ["STORELIMITLESS_API_URL"] = api_url
            environ["VITE_API_URL"] = api_url

        else:
            raise ValueError(f"Unsupported HOST configuration: {HOST!r}")

        server: Server = Server(Config(app, host="0.0.0.0", port=8000, log_level="warning"))
        server_task: Task[None] = create_task(server.serve())

        if frozen:
            frontend_server = Server(Config(frontend_app, host="0.0.0.0", port=5173, log_level="warning"))
            frontend_task = create_task(frontend_server.serve())

        else:
            frontend_dir: Path = Path(__file__).resolve().parent.parent / "frontend"
            frontend_env = environ.copy()
            frontend_env["VITE_API_URL"] = api_url

            frontend_process = Popen(
                [
                    "npm", "run", "dev", "--",
                    "--host", "0.0.0.0",
                    "--port", "5173",
                ],
                cwd=str(frontend_dir),
                env=frontend_env,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )

        discord_thread.start()
        ftp_server.start()

        try:
            while not Discord.ready:
                if server_task.done():
                    await server_task

                await sleep(0.1)

            print("\n" + "=" * 50)
            print("Servers started")
            print("=" * 50)

            if HOST == "LOCAL":
                print(f"FastAPI : {api_url}")
                print(f"Website : {website_url}")
                print(f"FTP     : ftp://{ip}:2121")
                print(f"Redis   : redis://{ip}:6379")

            else:
                print(f"FastAPI : {api_url}")
                print(f"Website : {website_url}")
                print(f"FTP     : ftp://{ip}:2121")
                print(f"Redis   : redis://{ip}:6379")

            print("=" * 50 + "\n")
            server_mode: bool = False

            while True:
                try:
                    if server_mode:
                        command: str = (await to_thread(input, "")).strip().lower()

                    else:
                        command: str = (await to_thread(input, "StoreLimitless> ")).strip().lower()

                    if command == "server":
                        server_mode = not server_mode

                        if server_mode:
                            print("Server mode enabled. Interactive prompt hidden.")

                        else:
                            print("Interactive prompt restored.")

                    elif command == "restart":
                        print("Restarting StoreLimitless...")
                        restart = True
                        break

                    elif command == "exit":
                        print("Shutting down StoreLimitless...")
                        break

                    elif command:
                        print(f"Unknown command: {command}")

                except (EOFError, KeyboardInterrupt):
                    break

        except CancelledError:
            pass

        finally:
            server.should_exit = True

            if frontend_server is not None:
                frontend_server.should_exit = True

            await gather(server_task, *([frontend_task] if frontend_task is not None else []), return_exceptions=True)

            ftp_server.stop()
            ftp_server.join(timeout=5)

            await Telegram.exit()
            discord_thread.join(timeout=5)

    finally:
        await Redis.close()

        for process in cloudflare_processes:
            if process.poll() is None:
                process.terminate()

                try:
                    process.wait(timeout=5)

                except TimeoutExpired:
                    process.kill()
                    process.wait()

        if redis_process is not None and redis_process.poll() is None:
            redis_process.terminate()

            try:
                redis_process.wait(timeout=5)

            except TimeoutExpired:
                redis_process.kill()
                redis_process.wait()

        if frontend_process is not None and frontend_process.poll() is None:
            frontend_process.terminate()

            try:
                frontend_process.wait(timeout=5)

            except TimeoutExpired:
                frontend_process.kill()
                frontend_process.wait()

        rmtree(TRANSFER_PATH, ignore_errors=True)

    if restart:
        if frozen:
            execv(sys.executable, [sys.executable, *sys.argv[1:]])

        else:
            execv(sys.executable, [sys.executable, "-m", "core.main"])


if __name__ == "__main__":
    try:
        run(main())

    except KeyboardInterrupt:
        pass
