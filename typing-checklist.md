# Python typing checklist

## Discovery record

- Runtime target: Python 3.13.7 (from the Windows build script); annotations use Python 3.10+ syntax.
- Stack: FastAPI, Pydantic, psycopg 3 / psycopg-pool, async Redis, pyftpdlib, asyncio, Discord.py, Telethon, libtorrent, yt-dlp, gdown, Requests.
- Existing type-checker configuration: none found before this pass.
- Test suite and migrations: no tracked test directory, test modules, migrations, or migration files were found.
- Stub files: no tracked `.pyi` files found.
- Generated Python files skipped: none found. Virtualenv, cache, build, and dependency directories are excluded by repository inventory rules.

## Source-file checklist

- [ ] `backend/database/backup.py`
- [ ] `backend/database/connection.py`
- [ ] `backend/database/models/__init__.py`
- [ ] `backend/database/models/directory.py`
- [ ] `backend/database/models/file.py`
- [ ] `backend/database/models/user.py`
- [ ] `backend/database/redis_server.py`
- [ ] `backend/ftp/__init__.py`
- [ ] `backend/ftp/auth.py`
- [ ] `backend/ftp/filesystem/__init__.py`
- [ ] `backend/ftp/filesystem/download.py`
- [ ] `backend/ftp/filesystem/filesystem.py`
- [ ] `backend/ftp/filesystem/upload.py`
- [ ] `backend/ftp/filesystem/utils.py`
- [ ] `backend/ftp/handler.py`
- [ ] `backend/ftp/server.py`
- [ ] `backend/server/app.py`
- [ ] `backend/server/frontend.py`
- [ ] `backend/server/jwt_handler.py`
- [ ] `backend/server/routers/__init__.py`
- [ ] `backend/server/routers/accounts.py`
- [ ] `backend/server/routers/directories.py`
- [ ] `backend/server/routers/public_routes.py`
- [ ] `backend/server/routers/upload_state.py`
- [ ] `backend/server/routers/uploads.py`
- [ ] `backend/server/security.py`
- [ ] `backend/server/utils.py`
- [ ] `backend/server/zip_stream.py`
- [ ] `core/__init__.py`
- [ ] `core/config.py`
- [ ] `core/data_center.py`
- [ ] `core/main.py`
- [ ] `core/stream.py`
- [ ] `core/transfer.py`
- [ ] `core/utils/__init__.py`
- [ ] `core/utils/binaries.py`
- [ ] `core/utils/discord_.py`
- [ ] `core/utils/download_.py`
- [ ] `core/utils/download_manager.py`
- [ ] `core/utils/download_utils.py`
- [ ] `core/utils/env.py`
- [ ] `core/utils/ingestion_queue.py`
- [ ] `core/utils/progress.py`
- [ ] `core/utils/telegram_.py`
- [ ] `core/utils/telegram_link.py`
- [ ] `core/utils/upload_manager.py`
- [ ] `core/utils_.py`

The checklist is ticked only after the signatures, attributes, ambiguous locals, and checker results have been reviewed.
