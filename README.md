# StoreLimitless

StoreLimitless is a self-hosted personal cloud-storage application. It combines a React/Vite web interface, a Python/FastAPI service layer, PostgreSQL
metadata, Redis-backed coordination, and Discord/Telegram as remote storage providers. It supports multi-file and nested-folder uploads, one-source-at-a-time remote imports, torrent file selection, resumable downloads, seekable HTTP
streaming, on-the-fly multi-file ZIP downloads, file previews, folder/trash management, keyboard and touch workflows, a unified upload/download **Task tray**, custom video controls, and an FTP-compatible virtual
filesystem. A separate Next.js service under `backend/storelimitlessbackend/` supplies the stable Vercel API proxy used by
`CLOUD` mode.

The web UI is branded **DriveBot** in places, while the overall application and server are called **StoreLimitless**.

> **Scope:** This document covers the StoreLimitless application, including its internal backend, frontend, storage/transfer code, FTP interface,
> runtime data, and packaging workflow. The separate Python SDK source package is not part of the tracked application tree. The build script generates
> versioned `LOCAL`/`CLOUD` executables under the repository-root `releases/linux` and `releases/windows` directories for selected targets. With no arguments,
> `build.sh` now builds only the Linux `CLOUD` executable; when platform flags are supplied, each selected OS produces both variants. The script also copies only the
> `LOCAL` executable for each selected platform into `Store-Limitless-api` under the SDK's standard `StoreLimitlessServer_<VERSION>` filename. Those binaries are generated
> artifacts, not application source. The internal service under `backend/server/` is documented because the frontend, uploads, streaming, and FTP integration depend on it.


---

## Contents

- [Prebuilt release binaries](#prebuilt-release-binaries)
- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Core concepts](#core-concepts)
- [Application lifecycle](#application-lifecycle)
- [Upload and storage flow](#upload-and-storage-flow)
- [Streaming and caching](#streaming-and-caching)
- [FTP interface](#ftp-interface)
- [Database model](#database-model)
- [Frontend guide](#frontend-guide)
- [Internal HTTP interface](#internal-http-interface)
- [Configuration](#configuration)
- [Development setup](#development-setup)
- [Running StoreLimitless](#running-storelimitless)
- [Build and packaged distributions](#build-and-packaged-distributions)
- [Backups and runtime data](#backups-and-runtime-data)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Developer guide](#developer-guide)
- [Known boundaries and verification notes](#known-boundaries-and-verification-notes)

---

## Prebuilt release binaries

The current published release is **StoreLimitless Server v0.1.0**. Download the executable that matches both your operating system and deployment mode. These are the exact uploaded asset names; the Windows `LOCAL` filename intentionally uses an underscore before the version.

| Platform | Configuration | Download |
|---|---|---|
| Linux | LOCAL | [`StoreLimitlessServer.LOCAL._0.1.0`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.LOCAL._0.1.0) |
| Linux | CLOUD | [`StoreLimitlessServer.CLOUD._0.1.0`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.CLOUD._0.1.0) |
| Windows | LOCAL | [`StoreLimitlessServer.LOCAL_0.1.0.exe`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.LOCAL_0.1.0.exe) |
| Windows | CLOUD | [`StoreLimitlessServer.CLOUD._0.1.0.exe`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.CLOUD._0.1.0.exe) |

- **LOCAL** bundles the built web frontend with the Python server. Use it when the same executable should serve the dashboard and API.
- **CLOUD** omits the frontend bundle, uses the hosted [StoreLimitless website](https://storelimitless.vercel.app), and exposes the API through the configured Cloudflare tunnel/Upstash proxy flow. It still requires valid service credentials and configuration.
- Both modes require the external services and provider setup described below. A packaged executable does not automatically configure PostgreSQL, Redis, Discord/Telegram credentials, firewall rules, or a secure public deployment.

See the [v0.1.0 release page](https://github.com/Dream2503/Store-Limitless/releases/tag/v0.1.0) for all assets and the release summary.

## What it does

StoreLimitless is a metadata-driven storage system. It does not treat the application machine's filesystem as the permanent home of users' cloud
files. Instead, PostgreSQL records each user's folders and file metadata, while file bytes are divided into parts and stored through a configured
provider.

The current code defines two storage providers:

- **Discord**: stores file parts as attachments/messages in a configured dump channel.
- **Telegram**: stores file parts as documents in a configured Telegram destination.

A file record keeps the provider name, byte size, MIME type, logical directory, modification time, and the ordered list of remote part links. The
original file can be reconstructed by downloading those parts in order.

The application provides these user-facing capabilities:

- Open a public landing page that explains the product before choosing to sign in or register. Login and signup are available at separate routes.
- Register and log in to a StoreLimitless account.
- Browse a user's virtual home directory and nested folders. The dashboard loads the directory tree into an account-scoped in-memory cache, so
  navigating between folders does not require a fresh request for every directory.
- Refresh the complete file/folder tree explicitly from the dashboard; a full browser reload also clears the JavaScript memory cache and triggers a
  fresh load. A deliberate upward mouse-wheel gesture at the top of the page or a downward touch pull at the top of the scroll chain reloads the page.
- Calculate recursive folder sizes and show total size in the item-details dialog.
- Upload multiple local files from the browser, including folders with nested directories. Dropped/selected folder paths are recreated under the
  chosen destination.
- Submit multiple local files or complete folder trees as one upload task. Remote-link import is deliberately one source per task: use one direct
  HTTP(S), Google Drive, YouTube, magnet, or supported Telegram message URL at a time. Browser-file batches appear in the tray as queued immediately
  and run sequentially; chunks within the current browser file may transfer concurrently.
- After the server registers the task, the upload dialog closes automatically. The task and its per-item statuses remain in the dashboard tray after
  completion, failure, or cancellation until the user removes that task.
- Track upload/import jobs and browser downloads in one shared **Task tray**. Minimize or maximize it, or open the full-screen `/upload-tray` route and return to the dashboard directory that launched it.
- Cancel an active batch from the tray to abort active browser chunk requests, stop the current backend worker where possible, skip queued items, and
  clean up the uncommitted browser-upload temporary state. The backend locks database finalization against cancellation so an interrupted browser file
  is not saved as a partial database record.
- Remove completed/failed tasks individually and clear task history. Active uploads/imports can be cancelled; browser downloads expose pause/resume and removal where the browser supports resumable downloads.
- Apply optimistic UI updates for single and bulk deletes and item moves, then reconcile with the backend in the background. If a mutation fails, the
  tree is refreshed and an error is shown.
- Import a single remote source per task, including direct HTTP(S) downloads, Google Drive files or public folders, YouTube, magnet torrents, and supported Telegram file-message links.
- Download direct HTTP (S) file URLs, import Google Drive files, process YouTube URLs, and select individual files from magnet torrents.
- Show YouTube processing stages in the upload tray: downloading video, downloading audio, merging audio/video, and uploading to the selected storage
  provider.
- Track upload batches and individual torrent-file states in a persistent, Redis-backed dashboard tray, including queued, downloading, uploading,
  completed, failed, skipped, and cancelled states where applicable.
- Download stored files through the shared Task tray. In browsers that support the File System Access API, byte-range requests and an IndexedDB job
  record allow downloads to pause and resume after a page reload.
- Download one folder, multiple files, multiple folders, or a mixed file/folder selection as a named ZIP archive. Folder paths are preserved, and the
  server streams the ZIP while reading source files from remote storage instead of staging a complete archive on disk. Paused ZIP downloads can resume
  from saved browser-storage bytes through HTTP byte ranges when the selected archive has not changed.
- Use dashboard keyboard shortcuts for selection, opening, renaming, trashing, permanent deletion, details, and parent-folder navigation. Mouse and touch
  users can also use context menus, drag selection, long-press selection, and drag-and-drop moves.
- Stream videos directly from their original storage chunks using HTTP byte ranges, with no server-side transcoding or track extraction. The custom player includes playback-speed controls (0.25x–2x), seeking, volume, and fullscreen; on touch devices, double-tapping the left or right side seeks by 10 seconds. Copy the signed stream URL to open the raw video in VLC. Preview PDFs, audio, Markdown, and common text/source formats.
- Use keyboard shortcuts on the upload page to return to the dashboard or trigger the upload/import action.
- Rename and move files and folders, including bulk move operations. The move dialog supports browsing destinations and toggling an already-selected
  destination off.
- Inspect file/folder details such as location, path, type, size, storage provider, timestamps, status, and record ID when those values are available.
- Move files/folders to trash, restore them, or permanently delete trash items.
- Create signed links for streaming or downloading a file.
- Open video files through the original byte-range stream, with a copy-stream-URL action for VLC. Browser playback is limited by supported codecs. Audio uses the browser's audio player; PDFs use the built-in viewer; Markdown uses a safe rendered subset; and common source/config/text extensions display as plain text.
- Stream media and other files over HTTP, including single byte-range requests for seeking.
- Connect through FTP using the same account and virtual directory tree.
- Cache recently used remote file parts locally to reduce repeat downloads.
- Export database tables to CSV through the backup utility.

## Architecture

```text
                         ┌──────────────────────────────┐
                         │ React + Vite frontend        │
                         │ landing, authentication,     │
                         │ dashboard, upload, preview   │
                         └──────────────┬───────────────┘
                                        │ HTTP + bearer token
                                        ▼
┌───────────────────────────────────────────────────────────────────┐
│ StoreLimitless application                                        │
│                                                                   │
│ backend/server/                                                   │
│   authentication, directory/file actions, upload jobs,            │
│   public-link verification, stream/download responses             │
│                                                                   │
│ core/                                                             │
│   startup/lifecycle, provider selection, chunk transfer,          │ 
│   byte-range streaming, progress, downloads, external binaries    │
│                                                                   │
│ backend/ftp/                                                      │
│   FTP authentication and a virtual filesystem backed by models    │
└──────────────┬──────────────────────────────┬─────────────────────┘
               │                              │
               ▼                              ▼
      ┌────────────────┐            ┌──────────────────────────┐
      │ PostgreSQL     │            │ Redis                    │
      │ users, folders,│            │ model cache, upload job  │
      │ file metadata  │            │ status/session metadata  │
      └────────────────┘            └──────────────────────────┘
               │
               ▼
      ┌──────────────────────────────────────┐
      │ Data-center abstraction              │
      │ Discord / Telegram                   │
      │ ordered file parts + remote links    │
      └──────────────────────────────────────┘

FTP client ── FTP port ──► FTP virtual filesystem ──► same models/transfer flow
```

The optional deployment proxy is a separate service rather than part of the Python process: `backend/storelimitlessbackend/` is a Next.js app that
stores the active Cloudflare Quick Tunnel URL and its update timestamp in Upstash Redis. In `CLOUD` mode, the Python process registers its temporary
HTTPS API tunnel with this service; the stable proxy forwards browser requests to that current upstream. The proxy does not provide file storage and
does not expose the FTP listener.

### Responsibility boundaries

- **`frontend/`** renders the user interface and makes HTTP requests. The browser does not communicate directly with Discord or Telegram.
- **`backend/server/`** is the application's internal HTTP service. It handles account authentication, folder/file operations, upload sessions,
  public-link validation, and streaming responses.
- **`backend/database/`** owns the PostgreSQL connection, database models, Redis integration, and database export utility.
- **`core/`** coordinates service startup, storage provider selection, transfer routines, supported link downloads, progress reporting, and byte-range
  reads.
- **`backend/ftp/`** exposes the same metadata and transfer functionality through a virtual FTP filesystem instead of a real host directory.
- **Discord and Telegram** hold file bytes. PostgreSQL stores the metadata needed to find, order, and reconstruct those bytes.
- **Redis** is used for cached models, upload progress/session state, and the account-scoped upload-job registry. The registry indexes jobs by the
  authenticated username so the tray can be reconstructed on another device connected to the same backend and Redis instance.

---

## Repository layout

This is the current application tree. Runtime files and packaged executables may appear after the application has run or the build script has been
used;
generated caches, virtual environments, and dependency folders are deliberately omitted here.

```text
.
├── backend/
│   ├── database/
│   │   ├── backup/
│   │   │   ├── directories.csv      # Directory export
│   │   │   ├── files.csv            # File metadata export
│   │   │   └── users.csv            # User/account export
│   │   ├── backup.py                # Export database tables
│   │   ├── connection.py             # PostgreSQL pool and schema bootstrap
│   │   ├── redis_server.py           # Async Redis client and serialization
│   │   └── models/
│   │       ├── __init__.py
│   │       ├── directory.py          # Directory persistence, moves, and trash
│   │       ├── file.py               # File metadata and lifecycle operations
│   │       └── user.py               # User/account persistence
│   ├── ftp/
│   │   ├── auth.py                   # FTP authentication and permissions
│   │   ├── handler.py                # FTP command-handler integration
│   │   ├── server.py                 # FTP server thread and lifecycle
│   │   └── filesystem/
│   │       ├── __init__.py
│   │       ├── download.py           # Seekable reader for remote files
│   │       ├── filesystem.py         # Virtual filesystem operations
│   │       ├── upload.py             # Staged FTP upload adapter
│   │       └── utils.py              # FTP path/model helpers
│   ├── server/
│   │   ├── app.py                    # FastAPI application and CORS
│   │   ├── frontend.py               # Packaged SPA and runtime config
│   │   ├── jwt_handler.py            # Bearer-token creation/validation
│   │   ├── security.py               # Password hashes and signed public tokens
│   │   ├── utils.py                  # Input validation helpers
│   │   ├── zip_stream.py             # Streaming ZIP64 archive writer
│   │   └── routers/
│   │       ├── __init__.py            # Router composition
│   │       ├── accounts.py           # Registration, login, metadata
│   │       ├── directories.py        # Directory/file operations, search, trash
│   │       ├── public_routes.py      # Readiness, upload status, stream/download
│   │       ├── uploads.py            # Browser uploads and URL-import jobs
│   │       └── upload_state.py       # Redis upload-session and chunk coordination
│   └── storelimitlessbackend/         # Next.js stable API proxy for CLOUD deployments
│       ├── app/
│       │   ├── api/register/route.ts  # Authenticated tunnel registration
│       │   ├── health/route.ts       # Proxy registry health
│       │   └── page.tsx              # Proxy project's minimal root page
│       ├── proxy.ts                  # Routes API traffic to registered tunnel
│       ├── package.json
│       └── next.config.ts
├── core/
│   ├── config.py                     # Hosting mode, paths, logging, runtime settings
│   ├── data_center.py                # Provider factory and bounded local cache
│   ├── main.py                       # Startup, service coordination, shutdown
│   ├── stream.py                     # Remote-part reconstruction and byte ranges
│   ├── transfer.py                   # File and link upload orchestration
│   ├── utils_.py                     # Cache bookkeeping and shared helpers
│   └── utils/
│       ├── __init__.py
│       ├── binaries.py               # Redis, FFmpeg, and Cloudflare helpers
│       ├── discord_.py               # Discord storage provider
│       ├── download_.py              # Public remote-link download dispatcher
│       ├── download_utils.py         # Direct HTTP, Google Drive, YouTube, and libtorrent helpers
│       ├── env.py                    # Credentials/configuration constants
│       ├── progress.py               # Progress tracking and part uploader
│       ├── telegram_.py              # Telegram storage provider
│       └── telegram_link.py          # Telegram message-link resolver and user-session login
├── data/
│   ├── logs.lock                     # Runtime log-file lock
│   ├── logs.log                      # Runtime application log
│   └── transfer/                     # Temporary downloads, upload staging, and cache
├── frontend/
│   ├── public/
│   │   └── vite.svg
│   ├── src/
│   │   ├── App.jsx                       # Public landing page and application routes
│   │   ├── main.jsx                      # React entry point
│   │   ├── index.css                     # Global styles and scrollbar theme
│   │   ├── assets/
│   │   │   └── react.svg
│   │   └── pages/
│   │       ├── Landing.jsx               # Public product landing page
│   │       ├── Auth.jsx                  # Sign-in and registration forms
│   │       ├── Dashboard.jsx             # Dashboard state, actions, selection, and drag/drop
│   │       ├── UploadTrayPage.jsx         # Dedicated full-screen upload task tray
│   │       └── dashboard/
│   │           ├── constants.js          # API URL, navigation, and shared style/storage constants
│   │           ├── utils.js              # Path, selection, token, file-kind, and display helpers
│   │           ├── components/
│   │           │   ├── Sidebar.jsx
│   │           │   ├── DashboardWorkspace.jsx
│   │           │   ├── DashboardContextMenu.jsx
│   │           │   ├── CreateFolderModal.jsx
│   │           │   ├── RenameItemModal.jsx
│   │           │   ├── ItemDetailsModal.jsx
│   │           │   ├── BulkActionConfirmModal.jsx
│   │           │   ├── UploadJobsTray.jsx
│   │           │   ├── DownloadJobsTray.jsx
│   │           │   ├── BackgroundOperationsTray.jsx # Component exists; not currently wired into Dashboard.jsx
│   │           │   ├── MoveModal.jsx
│   │           │   ├── UploadModal.jsx
│   │           │   ├── UploadTrayPage.jsx   # Full-screen tray wrapper
│   │           │   ├── FileContentPreview.jsx
│   │           │   └── ViewFile.jsx
│   │           ├── uploadFiles.js            # Directory/drop path extraction
│   │           ├── utils/
│   │           │   └── resumableDownloads.js # IndexedDB persistence for download tasks
│   │           └── hooks/
│   │               ├── useDashboardData.js
│   │               ├── useGlobalFileSearch.js
│   │               └── useUploadJobs.js
│   ├── eslint.config.js
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── postcss.config.js
│   ├── tailwind.config.js
│   └── vite.config.js
├── releases/
│   ├── linux/                        # Generated Linux LOCAL/CLOUD executables
│   └── windows/                      # Generated Windows LOCAL/CLOUD executables
├── build.sh                          # Builds selected OS targets in LOCAL and CLOUD modes
├── requirements.txt                  # Python runtime dependencies
├── storelimitless-backend.spec       # PyInstaller specification
├── LICENSE
└── README.md
```

`data/` is runtime storage in source/development mode. In frozen mode, runtime data is instead placed in the operating system's StoreLimitless
application-data directory. Treat logs, CSV exports, Telegram session data, and configuration files as potentially sensitive. The `releases/linux` and
`releases/windows` directories contain generated release variants after a build. The build can also create the separately maintained SDK's
`Store-Limitless-api` binary directory, with the standard-named `LOCAL` executable copied
for each selected platform. Temporary paths such as
`build/`, `frontend/node_modules/`, `frontend/dist/`, `__pycache__/`, and `.pyc`/`.pyo` files can be recreated by the build or runtime workflow and
are
cleaned after a successful build.


---

## Core concepts

### 1. Metadata versus file bytes

PostgreSQL tracks the logical file tree and records the remote part links for each file. Discord or Telegram stores the actual bytes. A file is usable
only when its database metadata and all expected remote parts agree.

This separation means:

- Renaming or moving a file normally changes metadata; it does not need to re-upload its bytes.
- A file's `links` order is significant. It represents the part order used during reconstruction.
- Losing the metadata database can make remote attachments difficult to associate with users and filenames, even if the remote messages still exist.
- Backing up the database is therefore as important as keeping the storage provider content.

Permanent deletion deserves special attention: the current `File.delete()` model method removes the PostgreSQL row and its Redis cache entry. The
supplied implementation does not show a matching deletion of the underlying Discord/Telegram attachments, so do not assume that permanently deleting a
database record also deletes the provider-side bytes. If provider cleanup is required, add and test it explicitly, including failure handling for
partial deletion.

### 2. A user's home directory

Registration creates a default logical directory at `/home`. The dashboard uses `/home` as its internal root, while the FTP filesystem presents that
directory as `/`.

The home directory is not a physical host directory. It is a row in the `directories` table. The FTP filesystem resolves paths back to the
authenticated user's `Directory` and `File` records.

### 3. Storage providers and part size

`core.data_center.DataCenter` maps a provider name to its implementation. The configured providers in this source are `Discord` and `Telegram`. A
file's `data_center` field chooses which implementation is used for upload and download.

The current source defines a maximum part size of **10 MiB** (`10 * 1024 * 1024`) for the configured providers. Each provider also exposes its own
`MAX_PARALLEL_UPLOADS` limit, which bounds simultaneous part uploads independently: the current classes set Discord to **2** and Telegram to **8**.
The `links` array should contain one link for each stored part. The streaming code verifies the part count against the recorded file size. If you
intend a different concurrency policy, change the provider class value in `core/utils/discord_.py` or `core/utils/telegram_.py` and update this
reference accordingly.

### 4. Cache and transfer files are disposable

The local part cache lives under `TRANSFER_PATH/cached`. Its current configured limit is **256 MiB**, and it uses least-recently-used eviction
bookkeeping. Cache files are not the canonical copy of user data.

In source mode, `TRANSFER_PATH` is normally `data/transfer/`; in frozen mode it is placed under the OS-specific StoreLimitless data directory. On
shutdown, all contents beneath this path, including temporary staging and the disposable part cache, are moved to an isolated location for asynchronous
cleanup. Transfer contents are intentionally not preserved across restarts. Do not place durable user files, credentials, or other application data there.

---

## Application lifecycle

Run `python -m core.main` from the repository root. The `main()` function is the process coordinator.

Before starting service processes in source mode, the application checks whether `frontend/node_modules/vite/package.json` exists. If frontend
dependencies are missing, it runs `npm ci` when `package-lock.json` is present, otherwise it runs `npm install`. It verifies that Vite is present
after
installation. Frozen/packaged execution skips this Node.js setup because the frontend is bundled.

At a high level, startup then does the following:

1. Starts a local Redis process if a Redis server is not already responding on `127.0.0.1:6379`.
2. Initializes the asynchronous Redis client and verifies connectivity.
3. Reinitializes the disposable cached-part directory.
4. Starts the Telegram client.
5. Creates the FastAPI server on port `8000`.
6. Starts the website on port `5173`. In source/development mode it launches Vite; in a frozen/packaged build it uses the bundled frontend server.
7. Starts the Discord client in a separate thread.
8. Starts the FTP server on port `2121`.
9. Waits until Discord is ready, then prints the service addresses and opens the interactive command prompt.

The app's current startup sequence relies on Discord becoming ready before it announces that the services are available. A bad Discord token,
inaccessible dump channel, missing permissions, or gateway startup failure can therefore prevent the startup sequence from reaching its ready state.

### Runtime prompt

The current `core/main.py` prompt implements these commands:

| Command  | Behavior                                                                  |
|----------|---------------------------------------------------------------------------|
| `server` | Toggle the interactive prompt display while leaving the services running. |
| `exit`   | Shut down the managed services and exit.                                  |

The current prompt is a service-control prompt, not a general file-management shell. File operations are exposed through the web UI and FTP interface.

### Shutdown

The lifecycle code requests HTTP-server shutdown with a short graceful timeout, closes FTP, shuts down the Discord and Telegram clients, closes Redis,
and stops processes launched for Redis/Vite/Cloudflare. All contents under the transfer directory, including staging folders and cached chunks, are moved
out of the active directory and deleted by a detached cleanup process so large temporary downloads do not hold up restart. The transfer directory itself is
recreated as needed; do not rely on files or cached chunks under `TRANSFER_PATH` (normally `data/transfer/` in source mode) surviving a shutdown.

---

## Upload and storage flow

### Browser file upload

The browser upload implementation is split between `frontend/src/pages/dashboard/components/UploadModal.jsx`, the upload routes, Redis upload-state
helpers, and the core
transfer layer.

1. The frontend first registers the complete selection as a single batch through `POST /auth/upload-batches`. The batch stores every selected relative
   path/link and its initial `queued` status in Redis before any transfer starts.
2. The frontend processes the batch sequentially. For each local file it requests a child job using `POST /auth/upload`, passing the batch ID in
   `X-Upload-Batch-Id` along with the selected data center, destination directory, file name, and size.
3. The backend validates the requested provider, file name, target directory, batch ownership, and batch status. A child job ID is created. The
   backend stores the child's owner, display name, destination directory, provider, type, batch ID, and progress metadata in Redis, adds the job to
   the per-user index, and associates it with the parent batch. Pending file/session data also uses a time-to-live.
4. The frontend divides each local file into **1 MiB chunks**, sends up to **8 chunks concurrently**, and allows up to **3 total attempts** for each
   chunk before failing the item.
5. The frontend sends byte chunks to `PUT /public/upload/{job_id}/chunk`, with `offset` and `total` query parameters.
6. The backend writes each chunk at its declared byte offset and records received ranges in Redis. The range helper merges overlapping/adjacent ranges
   and tracks the contiguous byte count from offset zero.
7. A producer task waits for incoming bytes while the upload worker passes the growing temporary file through the core upload pipeline. The transfer
   can therefore begin before the complete browser file has finished arriving.
8. The transfer layer splits the file into provider-sized parts, uploads those parts, and collects their remote links. Browser-file metadata is saved
   only after the complete transfer succeeds and the worker acquires the same finalization lock used by cancellation.
9. The frontend polls `GET /auth/upload-jobs/{job_id}/status` until the child job reaches a terminal state. Only then does the queue mark that batch
   item completed and start the next file.
10. The batch status is updated with per-item state and aggregate progress. The frontend polls `GET /auth/upload-batches` to keep the
    account-synchronized tray current, and refreshes the affected directory after the whole batch reaches a terminal state.

The pending upload/session data has an approximately one-hour Redis TTL in the current implementation. Job metadata and the per-user job index are
maintained separately so that the dashboard tray can discover jobs from another browser/device. The chunk receiver also detects interrupted/missing
temporary files and reports a restart-required failure rather than silently treating the incomplete file as complete.

Temporary browser upload files are stored beneath `TRANSFER_PATH` in a per-user/job layout. In source mode this is under `data/transfer/`, the runtime
scratch space whose staging folders are isolated and cleaned asynchronously when the application shuts down.

### Remote-link imports

`POST /auth/upload-link` starts a background import job for **one remote source URL**. Link mode intentionally accepts one HTTP(S) URL or one valid
torrent magnet URI per task. It no longer treats newline-separated URLs as a multi-link batch; start another import for each additional source. Local
multi-file and nested-folder uploads remain batch operations.

Torrent metadata inspection is tracked in the upload tray. Before payload downloading starts, `GET /auth/torrent-preview` resolves one magnet and
returns its file list. When metadata is ready, the tray exposes **Select files and start**. Every file is selected by default; deselect unwanted files.
The picker shows names relative to the torrent root, without repeating the root directory name. Selected torrent files download into temporary staging,
then are uploaded sequentially to the chosen storage provider. The upload tray shows per-file download/upload state and progress.

The server chooses the downloader from the URL host or URI scheme:

- **Direct HTTP(S) file URLs** use a streamed download. The response should contain file bytes, not an ordinary web page. The downloader uses filename
  metadata where available, rejects likely HTML pages that do not look like file downloads, and attempts to resume an interrupted transfer with HTTP
  `Range` requests. Resume is safe only when the origin confirms the exact requested byte offset and supports byte-range responses.
- **Google Drive file links** (`drive.google.com`) use the Google Drive downloader. Single-file imports write into a growing, unbuffered destination so the upload worker can consume chunks while the file is still downloading. The current single-file metadata path requests information through the Google Drive API, so the configured Google API key and access to the file are required.
- **Google Drive folder links** preserve relative folder paths. File downloads run through a bounded worker pool and each completed file is queued for upload as soon as it is ready, rather than waiting for the entire folder to finish downloading. Per-file download/upload progress is published to the Task tray; public-link access and Google Drive compatibility still apply.
- **YouTube links** (`youtube.com`, its subdomains, and `youtu.be`) use `yt-dlp` and FFmpeg to download/process media. The downloader resolves a
  stable media filename from metadata/output paths and publishes separate progress phases for video download, audio download, audio/video merging, and
  storage upload. Merging does not necessarily have a meaningful percentage.
- **Magnet links** (`magnet:?xt=urn:btih:...` or `magnet:?xt=urn:btmh:...`) use the `libtorrent` engine. A single magnet task can import several
  selected files from the same torrent. Progress reports peer discovery, metadata retrieval, downloading, and file-level upload progress.
- **Telegram message links** (`t.me`, `telegram.me`, including private `t.me/c/…/…` links the logged-in account can access) are resolved before the
  regular URL downloader starts. StoreLimitless uses a separate Telethon **user** session to open the original message, require a media attachment,
  forward that original message to `@PremiumFiletoLink_Bot`, and wait up to 90 seconds for a response containing the actual URL behind a **FAST
  DOWNLOAD** button or inline hyperlink. The resolver follows HTTP redirects without buffering the file and sends the resulting URL through the normal
  import workflow. It never falls back to downloading the Telegram message page itself. Missing/inaccessible messages, forwarding failures, missing
  download targets, unauthenticated sessions, and timeouts fail the import with an explicit error.

To enable Telegram message-link imports:

1. Configure the existing `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` credentials used by the Telegram integration.
2. Open `@PremiumFiletoLink_Bot` in Telegram and press **Start** once, if this account has not started it before.
3. While StoreLimitless is running, enter `telegram-login` in its terminal and complete the phone-number, login-code, and (if enabled) two-step
   verification prompts. In source mode, run `python -m core.utils.telegram_link --login` from the repository root instead.
4. Keep the generated `telegram_user.session` file private. It authenticates the Telegram user account and is separate from the existing Telegram
   storage-bot session. Do not commit or share it.

The Telegram user account must have access to the linked channel/group and permission to forward its file message. This workflow requires Telethon and
outbound connectivity to Telegram. The other downloader dependencies are `yt-dlp` plus FFmpeg for YouTube, and the Python `libtorrent` module for
magnet torrents.

For a direct-download example, submit a single source URL such as:

```text
https://downloads.example.org/archive.zip
```

Blank lines or additional URLs do not form a multi-source import task. Direct links to ordinary HTML pages, authenticated downloads the server cannot
access, expired links, or unsupported provider-specific pages may fail even when the URL is syntactically valid.

### Torrent download and upload flow

Torrent imports are implemented in `core/utils/download_utils.py`; `core/utils/download_.py` retains the public downloader interface used by
`link_upload()`. The shared download utility contains the torrent engine integration, progress/name updates, safe path handling, temporary file
staging,
and the helpers used for direct HTTP (S), Google Drive, and YouTube downloads.

1. Before importing a magnet, the frontend calls `GET /auth/torrent-preview`. The backend resolves metadata and returns the file paths and sizes
   without intentionally downloading payload data.
2. The frontend opens a selection modal for the imported magnet. Every file is selected by default; users can deselect unwanted files. Each remote-link
   task contains one magnet, but that torrent can contribute multiple selected files.
3. The selected original file indexes are passed to `POST /auth/upload-link`. Libtorrent assigns normal priority to selected files and priority zero
   to unselected files, focusing payload transfer on the requested files.
4. Progress reports the selected payload's byte total, connected peers, and seed count. The resolved torrent name is propagated into the active job
   state.
5. Selected files are downloaded completely to a temporary directory under `TRANSFER_PATH` before being uploaded sequentially. Unselected files are
   not added to the StoreLimitless directory.
6. After completion, the torrent session is stopped and the downloader validates each selected torrent-relative path before accessing local files.
7. The torrent's root folder and nested directories are recreated beneath the selected StoreLimitless destination. Existing trashed directories with
   matching paths are restored when needed.
8. Files are uploaded sequentially through `core.transfer.file_upload()`. The first uploaded file uses the import's initial database record; remaining
   files receive their own records.
9. The temporary torrent directory is removed after success or failure.

The torrent module excludes libtorrent padding files, rejects unsafe paths, and checks that resolved files stay inside the temporary download
directory. A magnet may remain in peer search when no peers are reachable, and a torrent cannot complete when its required pieces are unavailable. The
server needs outbound network access for trackers/DHT and peer connections; router/NAT restrictions can limit peer discovery.

Downloaded HTTP (S)/YouTube content is written to a temporary file and sent through the storage-provider upload pipeline. Direct links to ordinary
HTML pages, authenticated downloads the server cannot access, expired links, and unsupported provider-specific pages may fail even when the URL is
syntactically valid. YouTube processing requires `yt-dlp`; FFmpeg is discovered or installed by the binary helper on supported platforms. Magnet
imports require the Python `libtorrent` module to be installed in the runtime environment.

#### Torrent selection dialog keyboard behavior

The persistent upload tray opens the torrent picker after a metadata-preview task is ready. Every file in the current torrent is checked by default;
checked files are the ones that will be imported. The keyboard focus highlight is deliberately separate from the checkbox selection so navigating does not change the upload
set by itself.

| Key       | Action                                                                                        |
|-----------|-----------------------------------------------------------------------------------------------|
| `↑` / `↓` | Move focus between file rows; the handler stops the event from reaching dashboard navigation. |
| `Space`   | Toggle the checkbox for the focused file.                                                     |
| `Enter`   | Start processing the checked files.                                                           |
| `Esc`     | Cancel/close the selection dialog before processing begins.                                   |

After the selected set is persisted to the upload batch, the dialog closes and downloading/uploading proceeds in the background. Selected file names
are displayed as basenames rather than full torrent root paths; progress is tracked separately per selected file. Libtorrent can download selected
files as part of a torrent session, while the StoreLimitless file records are uploaded sequentially to avoid overlapping provider-level jobs.

### How `file_upload()` works

`core.transfer.file_upload()` chooses the provider from the file metadata, then delegates to `upload_growing_file()` in `core.utils.progress`. That
helper coordinates reading the local file, uploads individual parts, updates progress, and records the resulting remote links. Unless invoked in
intermediate mode, the function saves the completed file record and yields a final progress message containing its file ID.

The separate `link_upload()` function selects the supported downloader, streams its progress, saves the final file record, and reports completion.

### Upload progress

`core.utils.progress.Progress` is a small shared object containing a name, status message, transferred byte count, and total byte count. Redis
serializes progress so the HTTP status route can return updates to the frontend independently of the background transfer task.

`backend/server/routers/upload_state.py` contains the concurrency and session-state logic for browser chunk uploads. It tracks background tasks
strongly, coordinates range updates with a Redis lock, waits for contiguous input bytes, and cleans up session keys and temporary files when the job
ends.

### Optimistic mutations and background work

The dashboard applies local state changes before waiting for the database response for common delete and move actions:

- Single-file delete immediately removes the file from the visible list (permanent delete) or marks it as trashed.
- Folder delete immediately hides/removes the folder and its descendants from the current in-memory tree.
- Bulk delete applies the same optimistic changes and sends independent item operations concurrently. A two-step Shift+Delete sequence remains ordered
  for each individual item.
- File/folder moves update cached paths immediately and submit the move request in the background.
- If a background mutation fails, the dashboard fetches the authoritative tree and displays an error rather than leaving optimistic state permanently
  in place.
- The complete selection is registered as one Redis-backed batch before the modal closes automatically. All of its files/links are visible in the tray
  as queued items before transfers start.
- Batch tasks remain visible after they complete, fail, or are cancelled. Use the row's **X** to remove one terminal task, or **Clear all** to cancel
  active tasks and clear all tray history.
- Cancelling a batch aborts local chunk `XMLHttpRequest`s, prevents subsequent queue items from starting, asks the backend to cancel its active child
  worker, and cleans temporary chunk/session state. The browser-file worker and cancellation route share a Redis finalization lock: if cancellation
  wins, the file metadata is not committed; if the worker already entered the protected final database-save section, the endpoint reports that the
  file is already finishing rather than pretending a completed database write can be undone.

The browser upload queue remains sequential across files and links to avoid overlapping storage-provider jobs. Within one file, chunk uploads can
still run concurrently (up to the configured chunk-lane limit). Nested folder uploads create missing directories before the file queue starts, then
send each file to its relative destination. If a multi-file batch is cancelled after earlier files have completed, those already completed files
remain saved; cancellation prevents the active incomplete browser file from being registered and skips the remaining queued items.

Optimistic UI means the interface reacts immediately; it does not make PostgreSQL operations themselves faster. A failed request is reconciled against
backend state, and the error remains visible after the refresh.

---

## Streaming and caching

`core.stream.Stream` reconstructs a file from its provider parts. It validates that the stored metadata is internally consistent, then maps an
optional HTTP `Range` header to the part (s) containing the requested bytes.

Supported range forms include a normal byte range and a suffix range. Invalid or out-of-bounds ranges are rejected. The public stream route responds
with:

- `200 OK` for a full-file request.
- `206 Partial Content` when a range is requested.
- `416 Range Not Satisfiable` for an invalid/out-of-range request, with the total size in `Content-Range`.

The stream generator:

1. Finds the part containing the requested start offset.
2. Checks the local part cache.
3. Downloads the missing part from the file's configured provider.
4. Validates the downloaded part length.
5. Yields only the requested region, in output blocks of at most 1 MiB.

This arrangement lets the browser video player or an external player seek without downloading the entire file from the start, provided the client
sends byte-range requests and the remote part metadata is valid. The stream generator prefetches the next provider part in the background while it
serves the current one, yields output buffers of at most 1 MiB, and cancels any outstanding prefetch task when the response closes. Provider part
reads
use bounded retries; the Discord adapter reads CDN attachments without a fixed total-response timeout and retries incomplete/failed responses.

### Public stream versus public download

The code has separate endpoints for inline streaming and download-as-attachment. The frontend obtains a signed public token from an authenticated file
action, then uses it to request the corresponding public stream or download endpoint. The public token is signed using HMAC and binds the token to a
file ID and username.

The download endpoint also supports `HEAD`, byte ranges, `ETag`, and `If-Range` validators, which are used by the resumable-download manager to
verify the source and resume safely. In the current implementation, the public token payload does not include an expiration timestamp. Treat links as
bearer capabilities: anyone who obtains a valid link may be able to use it until the signing secret changes or the referenced file ceases to resolve.
Add expiry/revocation behavior before relying on links for sensitive files.

### Browser download manager and resumable downloads

The dashboard has one shared **Task tray** for uploads/imports and local browser downloads. The upload/import jobs are synchronized through the backend's Redis-backed task API, while resumable browser-download metadata and file handles are stored in IndexedDB. The download list is rendered by `DownloadJobsTray.jsx` inside `UploadJobsTray.jsx`, not as a separate top-level tray. In browsers supporting the File System Access API, choosing a file download opens the browser's save-file picker and writes verified byte ranges directly to that file rather than buffering a multi-gigabyte download entirely in JavaScript memory.

- The frontend requests a signed download token, then checks the file with `HEAD` for total size and an `ETag`.
- The download is split into **8 MiB byte-range requests**. Each range must return `206 Partial Content`, the exact expected `Content-Range`, and the
  expected number of bytes before the frontend writes it to disk. Transient range failures are retried up to five attempts.
- `If-Range` and the recorded `ETag` guard against continuing a partial file when the stored representation has changed.
- Download task metadata and the browser file handle are stored in IndexedDB. A page reload restores in-progress jobs as **paused**, and the user can
  resume them from the shared Task tray if browser permission for the chosen file is still available. Completed downloads can be cleared from the tray.
- The UI exposes progress, pause, resume, remove-task, and clear-completed actions inside the shared Task tray. If the File System Access API is unavailable, the app falls back
  to
  a normal browser download; resumable tray controls are not available on that fallback path.

The File System Access APIs are browser- and security-context-dependent. They are generally available on HTTPS origins and localhost, but not on an
ordinary HTTP LAN URL such as `http://192.168.x.x:5173`. In that case, folder selection can fall back to the legacy directory-input path, and Chromium
may show its native “Upload N files to this site?” confirmation. A web page cannot reliably suppress browser-owned security confirmations from
JavaScript. To use the modern folder picker and disk-backed resumable downloads on a LAN, serve the web app in a secure context (for example, HTTPS).

---

### Centralized upload and download managers

All storage uploads pass through the Redis-backed upload manager and its shared FIFO lease. Only one file is uploaded at a time across ingestion sources and backend workers; each provider can still upload that file's storage parts concurrently up to its configured `MAX_PARALLEL_UPLOADS` limit. Direct links, YouTube imports, browser uploads, torrent files, Google Drive folder imports, and video-processing derivatives use the same upload coordination path.

Downloads from remote storage use a separate bounded download manager. It fetches up to **eight storage-part links concurrently** and yields the parts in their original order, allowing ordinary file streams and streamed ZIP downloads to combine bytes correctly without staging entire files or archives on disk. The eight-way limit is shared across active download streams; it does not increase the provider's upload concurrency.

### Streaming multi-file ZIP downloads

Select at least two active files in the dashboard, open the context menu, choose **Download as ZIP**, and enter the archive filename in the app's
filename dialog. The frontend requests a short-lived ZIP token from `POST /auth/files/zip-link`, then starts a browser download from
`GET /public/download-zip/{token}?filename=...`.

The FastAPI endpoint streams a ZIP64 archive while it reads each source file from the configured remote storage provider. It does not download all
files into a temporary server directory or create a complete intermediate archive on disk. Output is streamed in ZIP STORE mode, which avoids
CPU-heavy compression and permits an exact content length. A ZIP token expires after 15 minutes; create a new link if it expires before the download
starts. Large downloads still require a stable connection, and a remote-storage failure after response streaming begins can leave an incomplete archive.

---

## FTP interface

The FTP service exposes the same StoreLimitless data through `pyftpdlib`; it does not expose the host's real root filesystem.

### Main modules

- `backend/ftp/server.py` runs the FTP listener in a daemon thread and provides a stop method.
- `backend/ftp/auth.py` checks StoreLimitless usernames/passwords and assigns the authenticated user to the FTP handler.
- `backend/ftp/handler.py` connects the protocol handler to the custom virtual filesystem and application event loop.
- `backend/ftp/filesystem/filesystem.py` maps FTP paths to the user's database-backed directories/files.
- `backend/ftp/filesystem/download.py` implements a seekable, read-only file object by requesting byte ranges from the internal stream route.
- `backend/ftp/filesystem/upload.py` wraps the local staging file and finalizes the upload when the FTP transfer closes the file.

### FTP path behavior

- An FTP user's visible `/` maps to their StoreLimitless `/home` directory.
- Directory listing, stat/size/time lookup, create-folder, rename/move, file read, and staged file write are implemented by the virtual filesystem.
- Deleting a file or directory moves it to trash rather than immediately deleting its metadata.
- Writing over an existing file moves the old record to trash and creates a new upload. Append mode is only supported where the virtual filesystem
  allows it.
- Symbolic links are not exposed, and `chmod` is explicitly unsupported.
- FTP uploads are staged locally and passed into `core.transfer.file_upload()` when the staged file closes.
- FTP reads use a 1 MiB range buffer and the same signed-token streaming logic as the web interface.

The default listener is `0.0.0.0:2121`. The current server setup does not configure a dedicated passive FTP port range. Remote FTP access through NAT,
a firewall, or a TCP tunnel may therefore require extra passive-port configuration and network rules even if the client can connect and authenticate
to the command port.

**Security:** the server currently uses plain FTP unless TLS is configured elsewhere. Plain FTP does not encrypt usernames, passwords, or file
contents. Do not expose it on an untrusted network without adding a secure transport and correct firewall/tunnel configuration.

---

## Database model

The database schema is initialized by `backend/database/connection.py` when the module connects to PostgreSQL. The connection pool uses dictionary
rows, and schema creation is performed with `CREATE TABLE IF NOT EXISTS`.

### `users`

| Column                    | Purpose                                                                     |
|---------------------------|-----------------------------------------------------------------------------|
| `username`                | Primary key and account identifier.                                         |
| `password`                | Stored password hash, not intended to contain plaintext after registration. |
| `first_name`, `last_name` | Profile fields.                                                             |
| `created_at`              | Account creation timestamp.                                                 |

Registration validates the profile fields, hashes the password, and creates the user and `/home` directory in one transaction.

### `directories`

| Column        | Purpose                                                   |
|---------------|-----------------------------------------------------------|
| `id`          | Generated directory identifier.                           |
| `path`        | Logical absolute path, such as `/home` or `/home/photos`. |
| `modified_at` | Last metadata modification time.                          |
| `deleted_at`  | Nullable trash marker.                                    |
| `username`    | Owning account; foreign key to `users`.                   |

Directory paths are unique per username. Directory moves/renames must update descendant paths as well as the selected directory row.

### `files`

| Column         | Purpose                                                |
|----------------|--------------------------------------------------------|
| `id`           | Generated file identifier.                             |
| `directory_id` | Parent directory identifier.                           |
| `name`         | Logical file name.                                     |
| `type`         | MIME type.                                             |
| `size`         | Total file size in bytes.                              |
| `modified_at`  | Last metadata modification time.                       |
| `data_center`  | Provider name, currently `Discord` or `Telegram`.      |
| `links`        | Ordered PostgreSQL `TEXT[]` of remote file-part links. |
| `deleted_at`   | Nullable trash marker.                                 |
| `username`     | Owning account.                                        |

The file record points to remote bytes; it is not the full file content itself. Keep the links array ordered and consistent with the part size and
recorded file size.

### PostgreSQL and Redis roles

PostgreSQL is the durable source of account, directory, and file metadata. Redis stores frequently accessed model data and operational upload state,
including progress, chunk sessions, per-job ownership metadata, and the username-indexed upload-job registry used by the cross-device tray. Do not
treat Redis cache/state as a substitute for database backups; if the Redis deployment is ephemeral, tray metadata can be lost on reset.

---

## Frontend guide

The frontend is a React application built with React Router, Vite, and Tailwind-style utility classes. It uses a dark interface with a green accent, a
public landing page, separate authentication routes, a file-management dashboard, upload/import workflows, and a dedicated file viewer. Reusable
dashboard UI and data lifecycle logic are organized under `frontend/src/pages/dashboard/`.

### Browser routes

| Route          | Page / behavior                                                                                                   |
|----------------|--------------------------------------------------------------------------------------------------------------------|
| `/`            | Public product landing page (`Landing.jsx`) with product information and sign-in / registration links.              |
| `/login`       | Sign-in form (`Auth.jsx` with `initialMode="login"`).                                                              |
| `/signup`      | Registration form (`Auth.jsx` with `initialMode="signup"`).                                                       |
| `/home`        | Dashboard root.                                                                                                   |
| `/home/*`      | Dashboard at a nested folder route.                                                                               |
| `/dashboard`   | Legacy route that redirects to the matching `/home` route.                                                        |
| `/upload`      | Standalone upload/import route (`dashboard/components/UploadModal.jsx`).                                          |
| `/upload-tray` | Dedicated full-screen upload task tray (`UploadTrayPage.jsx`); minimize returns to the originating folder.       |
| `/view/:fileId` | File preview and media player (`dashboard/components/ViewFile.jsx`).                                               |

### Frontend module map

#### Entry and page modules

- `src/App.jsx` declares the landing, authentication, dashboard, upload, and file-view routes. The root route is the public landing page, not the
  login form.
- `src/main.jsx` mounts React into the HTML root element.
- `src/index.css` contains global styling, the slim green-accent scrollbar theme for page and nested scroll regions, reduced-motion
  accommodations, and touch-selection rules. `src/App.jsx` installs the app-managed top-edge refresh gesture: a deliberate upward mouse-wheel gesture
  while at the top, or a downward touch pull at the top of the scroll chain, reloads the current page.
- `src/pages/Landing.jsx` renders the public-facing product page, including the responsive navigation, product-preview illustration, feature section,
  “how it works” steps, and sign-up/login calls to action. The product preview is illustrative rather than live account data.
- `src/pages/Auth.jsx` handles both login and signup modes and stores the bearer token in `localStorage` under `token`.
- `src/pages/Dashboard.jsx` coordinates dashboard state and interactions: folder navigation, selection, search, context menus, mouse-based
  drag/marquee selection, touch long-press gestures, drag-and-drop moves, bulk actions, trash/restore, dialogs, and file actions.

#### Dashboard components and helpers

All dashboard-specific modules are under `src/pages/dashboard/`:

- `components/Sidebar.jsx` renders the application navigation, storage-used summary, and sign-out control.
- `components/DashboardWorkspace.jsx` renders the current folder breadcrumb, search field, refresh control, folder/file rows, empty/loading/error
  states, and the file-management workspace.
- `components/DashboardContextMenu.jsx` renders background and item context menus.
- `components/CreateFolderModal.jsx` and `components/RenameItemModal.jsx` provide directory creation and rename dialogs.
- `components/ItemDetailsModal.jsx` shows available metadata for a selected file or folder. Folder details include a recursively calculated total
  size.
- `components/BulkActionConfirmModal.jsx` handles confirmation for destructive or bulk actions.
- `components/MoveModal.jsx` browses destination folders, lets the user choose or deselect a destination, and validates moves before submission.
  Selecting a different directory changes the destination; selecting the current choice again toggles it off and returns the destination to the folder
  currently being browsed.
- `components/UploadModal.jsx` handles multi-file and nested-folder uploads, plus one-source-at-a-time HTTP(S), Google Drive, YouTube, magnet, and
  Telegram message-link imports. Local file/folder selections are registered as one batch; a remote URL is one import task. The dialog closes once the
  server has accepted the task, then work continues in the tray.
- `components/UploadJobsTray.jsx` renders the unified **Task tray** for Redis-backed upload/import jobs and browser-local resumable downloads. It supports aggregate and per-file progress, upload cancellation, task dismissal, clearing tasks, minimize/maximize, and the full-screen route. The tray keeps terminal upload history visible until dismissed. A single-file upload uses the compact row layout; folders and multi-file batches retain nested per-item status. Torrent items show queued/downloading/downloaded/uploading/completed/failed/cancelled states and per-file progress. YouTube headings use the resolved media filename when available, with separate video-download, audio-download, merge, and storage-upload stages. Torrent selection checkboxes determine the import set; keyboard focus alone does not select a file.
- `src/pages/UploadTrayPage.jsx` hosts the shared Task tray at `/upload-tray` and returns to the dashboard directory from which it was opened.
- `components/DownloadJobsTray.jsx` is the embedded browser-download list used by the Task tray; it provides pause/resume, progress, removal, and clearing completed downloads.
- `utils/resumableDownloads.js` stores resumable download metadata and browser file handles in IndexedDB.
- `uploadFiles.js` extracts files and relative paths from file drops, including supported dropped directory trees.
- `components/ViewFile.jsx` routes by file extension and streams the original video URL directly in the native browser player. Its **Copy stream URL** action gives VLC the original signed byte-range URL.
- `components/FileContentPreview.jsx` fetches text content through an authenticated signed stream and renders source/config files as plain text,
  Markdown with a small safe subset, PDFs through the browser's PDF viewer, and audio through the native audio player. Common extensions include
  `.txt`, `.log`, `.json`, `.yaml`, `.html`, `.css`, `.js`, `.jsx`, `.ts`, `.tsx`, `.py`, `.c`, `.h`, `.cpp`, `.hpp`, `.rs`, `.go`, `.java`, `.sql`,
  `.sh`, and `.md`.

Text/source previews intentionally use escaped plain text rather than a syntax-highlighting library, so uploaded content is not interpreted as
executable HTML. The viewer's dark background/pane owns vertical and horizontal scrolling, including long lines, rather than trapping scrolling in the
text element. Text preview is limited to 5 MB to avoid freezing the browser tab. Markdown supports common headings, paragraphs, inline emphasis/code,
fenced code blocks, lists, blockquotes, horizontal rules, and HTTP (S) links; it is not a full CommonMark implementation.

#### Video streaming and VLC

Video playback uses the original file's raw HTTP byte-range stream. StoreLimitless does not dynamically transcode media or extract separate audio and
subtitle tracks on the server. The custom player provides playback speeds from 0.25x through 2x, progress seeking, volume/mute, fullscreen, and keyboard
shortcuts; a double-click toggles fullscreen, and on touch devices a double-tap in the left/right zones seeks backward/forward by 10 seconds while a
tap in the center toggles playback. Use the **Copy stream URL** action to copy the signed `/public/stream/{token}` URL, then open it in VLC. Browser
preview depends on the browser's supported codecs; VLC can handle formats the browser cannot.

Treat the copied stream URL as a bearer link: anyone who has it can access the shared file, and the current signed-token format has no expiry timestamp.

- `constants.js` centralizes the API URL, navigation entries, and shared keys/styles.
- `utils.js` contains shared path, token, file-kind, route, and selection helpers. `getFileKind()` maps common code, configuration, text, Markdown,
  PDF, audio, and video extensions to their viewer types.

#### Dashboard hooks

- `hooks/useDashboardData.js` loads the account's file/folder tree, storage metadata, and trash; refreshes a targeted directory after mutations; and
  calculates recursive folder sizes.
- `hooks/useGlobalFileSearch.js` manages search results across the loaded file tree.
- `hooks/useUploadJobs.js` polls the account-shared batch endpoint, maps child progress into a batch row, sends cancellation and dismissal actions,
  and refreshes affected directories when a batch finishes.

The separation keeps `Dashboard.jsx` focused on coordinating interactions. When adding dashboard behavior, put reusable UI in `components/`, data
lifecycle logic in the appropriate hook, and shared helpers/constants in `constants.js` or `utils.js`.

### Directory-tree cache and refresh behavior

`useDashboardData.js` loads the full account tree through one authenticated `GET /auth/tree` request instead of repeatedly walking the tree with one
`GET /auth/directory` call per folder and separate requests for Trash and storage metadata. The endpoint returns active and trashed directories,
active
and trashed file metadata, and the total storage usage in one response. To keep the payload smaller, the full-tree response omits the remote provider
`links` for each file; those links are not needed to display or select files in the dashboard. The tree is held in a module-level in-memory cache
scoped
to the current account token.

- **Initial dashboard load:** when there is no matching in-memory cache for the signed-in account, the hook fetches `/auth/tree` once for the complete
  file/folder tree and storage total. The endpoint scopes its data to the authenticated username.
- **Folder navigation:** changing the `/home/...` route switches the visible directory slice from the cached tree instead of issuing another full-tree
  request.
- **Explicit Refresh:** the dashboard's Refresh control fetches `/auth/tree` again, replacing the cached tree with current server data.
- **Browser reload:** the cache exists only in JavaScript memory, not in `localStorage`; a hard reload such as F5 recreates the module state and
  triggers
  a fresh full-tree request.
- **Local changes and uploads:** operations that affect a single folder can still use `GET /auth/directory?directory=...` to refresh just that
  directory
  rather than reloading the entire account tree. Completed uploads are synchronized to their target directories by the upload-job hook.
- **Recursive folder size:** the hook sums file sizes in a folder and all descendants, using the cached file/folder records. Missing or invalid size
  values contribute zero. The value is exposed as `total_size_bytes` for the details dialog.

The single-tree endpoint reduces request count during initial/full loading, but does not eliminate other application requests such as upload-job
polling.
The cache is a performance optimization, not durable storage. PostgreSQL remains the source of truth for metadata. If a change bypasses the normal
refresh path, use the dashboard Refresh control to reconcile the client tree with server data.

### Upload progress and job behavior

The upload tray is account-synchronized rather than being backed by a browser-local job list. `useUploadJobs.js` polls `GET /auth/upload-batches`
about once per second. Redis stores each batch, every queued item, per-item state, and its child-job IDs under the authenticated username, so the same
account sees the same task tray across browsers/devices using the same backend and Redis instance. The browser's `localStorage` holds the
authentication
token, but it is not the source of truth for upload tasks.

The batch API supports these shared actions:

- **Create/list/update:** `POST /auth/upload-batches`, `GET /auth/upload-batches`, and `PATCH /auth/upload-batches/{batch_id}` register the whole
  selection, list batch history, and publish current item/progress updates.
- **Cancel:** `POST /auth/upload-batches/{batch_id}/cancel` marks the batch cancelled, attempts to stop the active child worker, and marks queued
  items as cancelled. The initiating browser also aborts active chunk `XMLHttpRequest`s immediately. A Redis finalization lock prevents an incomplete
  browser file from being saved in the database after cancellation wins the race.
- **Dismiss one task:** `DELETE /auth/upload-batches/{batch_id}` removes a batch's history and child-job records. Active batches must be cancelled
  first.
- **Clear all:** `DELETE /auth/upload-batches` cancels active batch children and removes all batch history for the account.
- **Report browser transfer failure:** `POST /auth/upload-jobs/{job_id}/fail` publishes local chunk-transfer failures to the child job's shared
  status.

Terminal batches are intentionally **not** automatically pruned. Successful, failed, and cancelled tasks remain in the tray until the user removes one
with its row-level **X** or selects **Clear all**. The tray refreshes the destination directory after a batch becomes terminal. All routes verify
ownership against the authenticated username.

**Deployment requirement:** every device must use the same API backend and the same Redis data store. A per-process `localhost` Redis instance does
not synchronize separate deployments. Redis is operational state rather than the durable file metadata database; if Redis is ephemeral or reset, the
shared job tray can be lost. In addition, background task handles are process-local, so a Redis job record alone cannot resume an interrupted transfer
after the backend process/runtime has stopped.

### Upload page keyboard shortcuts and link behavior

On the standalone upload page:

- **Escape** returns to the dashboard route for the current directory.
- **Backspace** returns to the dashboard when focus is not in an editable field; normal text editing is preserved.
- **Enter** triggers the upload/import action when it is enabled, except when focus is in a text-editing control. Key repeat is ignored so the same
  action is not accidentally started more than once.
- **Folder picker:** the Choose folder control uses `window.showDirectoryPicker()` when supported, recursively gathers files and records relative paths,
  then recreates those nested paths at upload time. Browsers/security contexts without that API use a hidden `webkitdirectory` input as a fallback;
  browser-owned multi-file confirmation prompts may therefore still appear on ordinary HTTP LAN origins.
- In **Link** mode, submit one publicly accessible HTTP(S) URL, one valid magnet URI, or one supported Telegram message link per task. Multi-link input
  is disabled. Begin another import to process another remote source.
- In **File** mode, users can select multiple local files or folders, or drop files/folders into the upload area. Transfers use the chunked upload
  workflow documented in [Upload and storage flow](#upload-and-storage-flow).

### Selection and move behavior

Dashboard selection is tracked separately for each view (folder route, active/trash tab, and search query) so selections can be restored when
navigating back through the app. On desktop, the workspace supports modifier-key multi-selection, a mouse-drag selection rectangle, context-menu
actions, and drag-and-drop move gestures.

On touch screens, the workspace uses explicit gestures because desktop drag and modifier-key behavior does not translate reliably to a phone:

- **Tap an item:** opens the file or folder in the normal browsing flow.
- **Long-press an item:** after approximately 430 ms, selects the item and enters touch multi-selection mode. While this mode is active, tapping other
  items adds them to the selection; tapping an already-selected item removes it. Clearing the selection exits the mode.
- **Long-press and drag an item:** after the long press selects the item, moving the finger starts a move preview. Drop the selected items on a valid
  folder or breadcrumb target to move them. Invalid destinations, including moving a folder into itself or a descendant, are rejected.
- **Long-press empty workspace and drag:** starts a selection rectangle for selecting multiple visible items.
- **Top-edge refresh:** when the scroll chain is already at the top, a deliberate downward touch pull reloads the current page. A deliberate upward
  mouse-wheel gesture at the top also reloads it. This is implemented by `src/App.jsx` rather than delegated to browser-native pull-to-refresh behavior.
- **Scroll normally:** finger movement greater than about 9 px before the long-press activates cancels the pending item/background gesture, leaving
  the
  browser's native scrolling behavior in control. When the dashboard is not at the top, pulling up continues normal content scrolling. A touch gesture
  is
  cancelled if it leaves the dashboard workspace or the browser emits `touchcancel`.

The touch behavior uses the workspace's touch events and global document listeners rather than relying on native HTML5 drag-and-drop. Top-edge refresh
uses a separate global listener and reloads the page only after the intentional gesture threshold is reached. The visible **Refresh** button is different:
it reloads dashboard file/folder data without requiring a full browser page reload.

The move dialog fetches immediate children as the user browses and caches each visited directory for the lifetime of that dialog. A destination must
pass the dashboard's move validation: moving a directory into itself or one of its descendants is rejected, as is a move with no effective destination
change. A selected directory's action toggles between **Choose** and **Deselect**; **Use this folder** selects the folder currently open in the
dialog. Partial failures are reported instead of being treated as a complete success.

#### Dashboard keyboard shortcuts

These shortcuts are handled by the dashboard when a text-editing control or an owning modal does not have keyboard input:

| Key / combination     | Action                                                                                                                    |
|-----------------------|---------------------------------------------------------------------------------------------------------------------------|
| `↑` / `↓` / `←` / `→` | Move the active dashboard selection spatially through visible items; in the dashboard this also selects the focused item. |
| `Enter`               | Open the single selected file or folder.                                                                                  |
| `Ctrl+A` / `Cmd+A`    | Select all selectable items in the current view.                                                                          |
| `Delete`              | Open the Move to Trash confirmation for selected items.                                                                   |
| `Shift+Delete`        | Open the permanent-delete confirmation for selected items outside Trash.                                                  |
| `F2`                  | Rename the single selected item when not in Trash.                                                                        |
| `Alt+Enter`           | Open details for the single selected item.                                                                                |
| `Backspace`           | Navigate to the parent folder (or dashboard root) when not editing text.                                                  |
| `Esc`                 | Close/cancel the active confirmation where applicable; otherwise clear dashboard selection.                               |

The torrent picker is intentionally an exception: its `↑` / `↓` keys move only the dialog's focus highlight, `Space` toggles the checked state,
`Enter` begins the selected import, and `Esc` closes the picker. The dialog stops those key events from also moving dashboard selection. Destructive
confirmation dialogs initially focus the destructive action; arrow keys can switch focus to Cancel before the user activates it.

### Global visual behavior

`src/index.css` applies a narrow scrollbar style to the page and nested scrollable elements. The thumb is dark green at rest and brightens on
hover/active state, with transparent tracks and scrollbar corners. It limits scroll chaining for common overflow utility classes and keeps horizontal
overscroll contained. Global styles also respect reduced-motion preferences. `src/App.jsx` listens for a deliberate upward mouse-wheel gesture at the top
or a downward touch pull at the top of the scroll chain and reloads the page once the relevant threshold is reached. Component-specific styling remains
in Tailwind utility classes.

### API URL resolution

The frontend resolves the service URL from `window.STORELIMITLESS_CONFIG?.API_URL` first, then falls back to Vite's `import.meta.env.VITE_API_URL`.
The packaged frontend service in `backend/server/frontend.py` exposes `/config.js` and reads `STORELIMITLESS_API_URL` at runtime. During development,
`core/main.py` passes `VITE_API_URL` to Vite and also sets the runtime URL variables.

When changing the API URL mechanism, keep the following aligned:

1. `core/main.py` service startup and environment variables.
2. `backend/server/frontend.py` runtime configuration.
3. `frontend/index.html` script-loading order, if it loads `/config.js`.
4. API URL resolution in the frontend modules, including the shared `pages/dashboard/constants.js` value used by dashboard hooks.
5. CORS origins in `backend/server/app.py` and the Cloud-mode middleware.

The browser-facing URL must be reachable from the browser itself. `127.0.0.1` always means the computer running the browser, not a remote host.

---

## Internal HTTP interface

This section documents the HTTP service that is part of the StoreLimitless application. It does **not** describe the separately packaged SDK under
`Store-Limitless-api`.

Most `/auth/...` routes require an `Authorization: Bearer <access_token>` header. Public upload-status and file-stream routes use the `/public/...`
namespace, although the chunk receiver itself also validates the user's bearer token in the current code.

The router-composition module determines how the individual router modules are mounted. The endpoint paths below reflect the route paths used by the
supplied frontend and server modules.

### Accounts

| Method and path                              | Purpose                                                                                                                                                                                    |
|----------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `POST /auth/register`                        | Validate details, hash the password, create the account, and create `/home`.                                                                                                               |
| `POST /auth/login?username=...&password=...` | Verify credentials and return the bearer token, profile, and home directory.                                                                                                               |
| `GET /auth/metadata`                         | Return account metadata including the calculated storage usage.                                                                                                                            |
| `GET /auth/tree`                             | Return the authenticated user's active/trashed directories and file metadata, plus total storage usage, in one response. Remote file-part `links` are omitted from this full-tree payload. |

Access tokens use HS256 and have a 24-hour lifetime in the current code. The login endpoint currently accepts credentials in query parameters;
see [Security notes](#security-notes).

### Directory and file management

| Method and path                                   | Purpose                                                                                                            |
|---------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| `POST /auth/create-folder?directory=...&name=...` | Create a directory under the requested parent.                                                                     |
| `GET /auth/directory?directory=...`               | Return the immediate folder and file entries for a directory; retained for targeted refreshes after local changes. |
| `GET /auth/search`                                | Search the user's file/directory metadata.                                                                         |
| `GET /auth/trash`                                 | Return trashed files and directories.                                                                              |
| `PATCH /auth/file/{fid}/rename?name=...`          | Rename a file.                                                                                                     |
| `PATCH /auth/directory/{did}/rename?name=...`     | Rename a directory and rewrite descendant paths.                                                                   |
| `PATCH /auth/file/{fid}/move?directory=...`       | Move a file to another directory.                                                                                  |
| `PATCH /auth/directory/{did}/move?directory=...`  | Move a directory and its descendants.                                                                              |
| `PATCH /auth/move`                                | Bulk move the specified file/folder IDs.                                                                           |
| `DELETE /auth/file/{fid}`                         | Move a file to trash.                                                                                              |
| `DELETE /auth/directory/{did}`                    | Move a directory to trash.                                                                                         |
| `POST /auth/trash/file/{fid}/restore`             | Restore a trashed file.                                                                                            |
| `POST /auth/trash/directory/{did}/restore`        | Restore a trashed directory.                                                                                       |
| `DELETE /auth/trash/file/{fid}`                   | Permanently delete a file record from trash.                                                                       |
| `DELETE /auth/trash/directory/{did}`              | Permanently delete a directory from trash.                                                                         |
| `POST /auth/file/{fid}/public-link`               | Create a signed stream/download token for a file.                                                                  |
| `POST /auth/files/zip-link`                        | Validate at least two active files and create a short-lived token for a streamed ZIP download.                     |
| `GET /public/download-zip/{token}`                 | Stream a ZIP64 archive directly from remote source files without creating a complete temporary archive.            |

The `/home` root is protected from being moved/renamed/deleted by the directory-management code. File and directory names are validated before path
changes.

### Upload and public-file routes

| Method and path                                                                                 | Purpose                                                                                                                                            |
|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| `POST /auth/upload-batches`                                                                     | Register one account-shared task with the full list of queued files/links.                                                                         |
| `GET /auth/upload-batches`                                                                      | List batch tasks and every per-item status for the authenticated account.                                                                          |
| `PATCH /auth/upload-batches/{batch_id}`                                                         | Update a batch's state, current child job, progress counters, or item status.                                                                      |
| `POST /auth/upload-batches/{batch_id}/cancel`                                                   | Cancel the active child and stop subsequent queued items.                                                                                          |
| `DELETE /auth/upload-batches/{batch_id}`                                                        | Remove one terminal batch and its child job records.                                                                                               |
| `DELETE /auth/upload-batches`                                                                   | Cancel active batches and clear all account tray history.                                                                                          |
| `POST /auth/upload`                                                                             | Create a browser-upload child job. Headers include `X-Data-Center`, `X-Directory`, `X-File-Name`, optional `X-File-Size`, and `X-Upload-Batch-Id`. |
| `PUT /public/upload/{job_id}/chunk?offset=...&total=...`                                        | Send bytes for a declared file range. Requires the owning user's bearer token.                                                                     |
| `GET /auth/torrent-preview?link=...`                                                            | Resolve a magnet link and return torrent file indexes, paths, and sizes before payload download. Requires authentication.                          |
| `POST /auth/upload-link?link=...&data_center=...&directory=...&batch_id=...&selected_files=...` | Create a child job to import a remote link; `selected_files` carries the selected torrent indexes when applicable.                                 |
| `GET /auth/upload-jobs`                                                                         | List child jobs for authenticated-account diagnostics/legacy consumers.                                                                            |
| `POST /auth/upload-jobs/{job_id}/cancel`                                                        | Cancel an owned child job and publish shared cancelled status.                                                                                     |
| `POST /auth/upload-jobs/{job_id}/fail`                                                          | Record a browser-side file-transfer failure on an owned child job.                                                                                 |
| `DELETE /auth/upload-jobs/{job_id}`                                                             | Dismiss a legacy standalone terminal child job.                                                                                                    |
| `GET /auth/upload-jobs/{job_id}/status`                                                         | Read status for a child job; requires the user's bearer token.                                                                                     |
| `GET /public/ready`                                                                             | Report whether the Discord storage client is ready.                                                                                                |
| `GET /public/upload/{job_id}/status`                                                            | Read an owned upload child's status/progress through the public-router namespace; a bearer token is still required.                                |
| `GET /public/stream/{token}`                                                                    | Stream a file and honor the HTTP `Range` header.                                                                                                   |
| `GET /public/download/{token}`                                                                  | Download a file as an attachment with byte ranges and validators.                                                                                  |
| `HEAD /public/download/{token}`                                                                 | Return download metadata such as content length and ETag without the response body.                                                                |

These tables describe the routes relevant to the supplied source; they are not intended to replace generated OpenAPI documentation from a running
instance.

---

## Vercel frontend and stable API proxy

The current deployment separates the static React frontend from the Python API:

| Component             | URL / location                                          | Role                                                                                              |
|-----------------------|---------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| Frontend              | `https://storelimitless.vercel.app`                     | Public landing page, authentication UI, and dashboard                                             |
| Stable API proxy      | `https://storelimitlessbackend.vercel.app`              | Stable browser-facing API origin; forwards requests to the currently registered Cloudflare tunnel |
| Cloud-mode API tunnel | Generated at runtime                                    | Temporary HTTPS upstream to the Python API on port `8000`                                         |
| Tunnel registry       | Upstash Redis, configured in the Next.js Vercel project | Stores the active tunnel URL and last registration timestamp                                      |

When the Python application starts in `HOST="CLOUD"`, it creates a Cloudflare Quick Tunnel for `http://127.0.0.1:8000`, then attempts to register
the generated HTTPS URL with `POST /api/register` on the stable API proxy. The registration endpoint expects a bearer token matching
`STORELIMITLESS_PROXY_TOKEN`; it accepts only HTTPS `*.trycloudflare.com` URLs and stores the current upstream in Upstash Redis. Registration
failures are logged and do not necessarily stop the local services, so a running Colab/server does not by itself prove that the stable proxy is
registered.

The proxy's `/health` endpoint reports whether an upstream is registered and the last update timestamp. It reports registry state, not whether every
API route is healthy. If browser API requests return `503`, check the health endpoint, confirm the cloud runtime is still running, inspect the
registration logs, and verify that the Vercel and Python runtime use the same `STORELIMITLESS_PROXY_TOKEN` and valid Upstash Redis environment
variables. Public stream, file-download, and ZIP-download responses also emit CORS headers for the production frontend origin because browsers may
follow the stable proxy redirect to the current Cloudflare tunnel. If you change the frontend origin, update the allowlist in
`backend/server/routers/public_routes.py` as well as the general FastAPI CORS configuration. If a request fails with a browser CORS error, inspect the
actual final response and `OPTIONS` preflight; a proxy-generated `503` is not the same as a response from FastAPI with its CORS headers.

The Next.js proxy project lives in `backend/storelimitlessbackend/`. Its `.next/`, `node_modules/`, `.turbo/`, and `coverage/` directories are
generated artifacts and are removed by the successful root build cleanup when present. Keep `package.json`, `package-lock.json`, `app/`, `proxy.ts`,
and configuration files; these are required to rebuild and deploy the proxy.

## Configuration

### Main configuration locations

| File or setting                    | Responsibility                                                                    |
|------------------------------------|-----------------------------------------------------------------------------------|
| `core/config.py`                   | `HOST`, path locations, provider allowlist, logging, frontend distribution paths. |
| `core/utils/env.py`                | Current database/JWT/provider/API credential constants.                           |
| `backend/database/connection.py`   | Builds the PostgreSQL connection pool and creates tables.                         |
| `backend/database/redis_server.py` | Connects to Redis at `redis://localhost:6379`.                                    |
| `backend/server/frontend.py`       | Exposes runtime frontend configuration from `STORELIMITLESS_API_URL`.             |
| `core/main.py`                     | Determines local/cloud URLs, starts services, and passes `VITE_API_URL`.          |
| `backend/server/app.py`            | FastAPI CORS configuration.                                                       |

### Hosting mode

`HOST` is declared in `core/config.py` as either `LOCAL` or `CLOUD`.

- **`LOCAL`**: use the detected LAN address for the API and website URLs. The API listens on `0.0.0.0:8000`, Vite or the packaged frontend on
  `0.0.0.0:5173`, and FTP on port `2121`. Source-mode startup installs frontend dependencies if Vite is missing, then launches the Vite dev server.
- **`CLOUD`**: start a Cloudflare Quick Tunnel for the local API on port `8000`. The public frontend is hosted separately at
  `https://storelimitless.vercel.app`; Cloud mode does not start Vite or bundle frontend assets into its executable. The current tunnel URL is
  registered with the stable Vercel API proxy at `https://storelimitlessbackend.vercel.app`, which stores the active upstream URL in Upstash Redis.
  The proxy forwards API requests to that registered tunnel.

The current Cloud-mode code does not create a public FTP tunnel. FTP still listens locally on port `2121`, and Redis is intended to remain private.
The Vercel proxy depends on its Upstash Redis environment configuration and the `STORELIMITLESS_PROXY_TOKEN` secret being configured consistently
with the StoreLimitless runtime.

### Runtime paths

`core/config.py` creates or selects these paths:

- `TRANSFER_PATH`: temporary downloads, staging files, and the disposable part cache.
- `BACKUP_DIR`: CSV backup output directory under `backend/database/backup`.
- `LOG_PATH` / `LOG_LOCK_PATH`: application log and file lock (`data/logs.log` and `data/logs.lock` in source mode). Application logs, discord.py
  client/gateway messages, and stdout/stderr from the Redis process launched by StoreLimitless are written to `LOG_PATH` rather than the interactive
  console. A Redis instance started independently by a system service keeps its own service-managed log destination.
- `TELEGRAM_SESSION`: Telethon session file path, normally under the base data directory.
- `TELEGRAM_USER_SESSION`: separate user-account session file at `BASE_DIR/telegram_user.session`, used only for forwarding source messages to the processing bot.
- `FRONTEND_DIST`: built frontend assets used by packaged execution.

When frozen, the base data directory is under `%LOCALAPPDATA%/StoreLimitless` on Windows, or `$XDG_DATA_HOME/storeLimitless` /
`~/.local/share/storeLimitless` on Linux. In source mode, the base directory is `<repository>/data`. The source-mode `data/transfer/` subdirectory
holds transient downloads, staging files, and cached provider parts; it is not durable storage.

### Credentials and secrets

The uploaded source defines sensitive configuration values directly in `core/utils/env.py`, including the database connection string, JWT signing
secret, Google API key, and Discord/Telegram credentials. Do not copy those values into documentation, commit them to a public repository, or ship
them to other users.

**Treat credentials included in a shared archive as exposed. Rotate the affected database password, API key, bot token, and signing secret before
deployment or publication.** Then move configuration to environment variables or a secret manager and read those variables at startup. Keep secrets
out of logs and build artifacts.

A proper deployment should validate required configuration at startup and fail with a clear message when a required setting is missing, rather than
silently running with an invalid value.

---

## Development setup

### Prerequisites

- Python 3.12 is the best match for the uploaded working tree; the source uses modern typing and Python features.
- Node.js and npm for the Vite/React frontend.
- A PostgreSQL database reachable by the application.
- Redis on `127.0.0.1:6379`. The launcher attempts to use an existing server or launch one when needed.
- Discord bot credentials and access to the configured storage/dump channel.
- Telegram API credentials and a configured destination for stored documents.
- FFmpeg for YouTube media downloads. The binary helper attempts installation on supported systems if it is missing.
- The Python `libtorrent` module for magnet-link imports. Install a `python-libtorrent` build compatible with the operating system and Python version
  used to run StoreLimitless.
- Network access to the configured database, Discord, Telegram, Google Drive, YouTube, torrent trackers/DHT/peers, and any configured tunnel provider.

### Create a Python environment

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` includes `libtorrent`, which is used for magnet-link imports. Choose a compatible wheel/build for
the Python version and operating system used by StoreLimitless; the available builds differ across platforms.

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Install frontend dependencies

```bash
cd frontend
npm ci
cd ..
```

`npm ci` uses the committed lockfile and is preferable for reproducible local setup. If you intentionally change dependencies, use npm's normal
install workflow and commit both the manifest and lockfile.

### Configure services before launching

1. Set or replace the database connection details with a PostgreSQL database that the application is allowed to use.
2. Configure a strong, private JWT/public-token signing secret.
3. Configure valid Discord bot credentials and the correct dump channel.
4. Configure valid Telegram API credentials, bot token, and dump destination.
5. Confirm that Discord and Telegram clients have the permissions they need.
6. Ensure Redis is available on the configured local address.
7. Rotate any credentials that were present in a shared version of the source.

The current source keeps several values directly in `core/utils/env.py`, so changing only a `.env` file will not automatically change those constants
unless the code is updated to read that file/environment. Verify the configuration loader after moving secrets.

---

## Running StoreLimitless

### Development mode

From the repository root with the Python environment activated:

```bash
python -m core.main
```

`core.main` launches the local service stack. In source mode it automatically checks whether frontend dependencies are installed and runs `npm ci`
(when
`package-lock.json` exists) or `npm install` (when it does not) if Vite is missing. Node.js and npm must be installed on the machine for this recovery
to
work. The build script also removes `frontend/node_modules/` after a successful package build, so the next source-mode startup reinstalls it as
needed.

The launcher starts Vite itself, so do **not** start a second Vite instance on port `5173` at the same time. Frozen/packaged execution serves the
bundled
frontend and does not run npm at startup.

The startup summary prints the API, website, FTP, and Redis addresses. For another device on the same LAN, use the machine's reachable LAN address
rather than `127.0.0.1`. Cloud mode sets the runtime API URL from the public API tunnel.

### Individual frontend commands

From `frontend/`, the typical Vite scripts are defined by `package.json`. The application launcher specifically invokes:

```bash
npm run dev -- --host 0.0.0.0 --port 5173
```

For a standalone production frontend build, use the scripts declared in the actual `package.json`, normally:

```bash
npm run build
```

Return to the repository root before launching `python -m core.main`, because module imports and paths assume the project root is the current working
directory.

### Default listener summary

| Service                  | Default address                 | Notes                                                                 |
|--------------------------|---------------------------------|-----------------------------------------------------------------------|
| Internal FastAPI service | `0.0.0.0:8000`                  | Browser HTTP requests, account actions, upload routes, stream routes. |
| Website                  | `0.0.0.0:5173`                  | Vite in source mode; bundled frontend server in frozen mode.          |
| FTP                      | `0.0.0.0:2121`                  | Plain FTP in the current server setup.                                |
| Redis                    | `127.0.0.1:6379`                | Internal cache/session/progress service; do not expose publicly.      |
| PostgreSQL               | From database connection config | The app connects using `DATABASE_URL` from its configuration module.  |

A service listening on `0.0.0.0` accepts connections through available local interfaces; firewall, router, tunnel, and host rules still determine
whether those connections are reachable from other machines.

---

## Build and packaged distributions

The root `build.sh` builds the React frontend once, then packages the Python application with PyInstaller. Each selected operating-system target is
built twice: once with `HOST = "LOCAL"` and once with `HOST = "CLOUD"` in `core/config.py`. The variants use separate PyInstaller work/output
folders so one mode cannot accidentally reuse the other's intermediate files.

### Build commands

Run the script from the repository root (or another directory; the script resolves paths relative to itself):

```bash
# No arguments: build only the Linux CLOUD executable (default)
./build.sh

# Build both LOCAL and CLOUD Linux executables (2 files)
./build.sh 1 0

# Build both LOCAL and CLOUD Windows executables (2 files; requires Wine on Linux)
./build.sh 0 1

# Build both variants for both operating systems (4 files)
./build.sh 1 1

# Choose a release version label for all output filenames
VERSION=0.2.0 ./build.sh 1 1
```

With no arguments, the script builds only the Linux `CLOUD` executable. When arguments are supplied, provide both flags: the first selects Linux and
the second selects Windows. Each argument must be `0` or `1`, and at least one must be `1`; every selected operating system then builds both `LOCAL`
and `CLOUD` variants. The `VERSION` environment variable defaults to `0.1.0` and is incorporated into output filenames.

The script requires Python 3, `file`, and npm. For a Windows build it also requires Wine, `wineboot`, `winepath`, and `wget`; it installs a 64-bit
Windows Python runtime inside its Wine prefix. If pip is unavailable after installing Windows Python, the script tries `ensurepip` and then the
official
`get-pip.py` bootstrap before installing dependencies.

### Build variants and output layout

The script writes selected platform variants directly to the repository-root `releases/linux` and `releases/windows` directories. A no-argument run
produces only the Linux `CLOUD` file. With the default `VERSION=0.1.0`, the
four possible outputs are:

```text
.
├── linux/
│   ├── StoreLimitlessServer.CLOUD._0.1.0
│   └── StoreLimitlessServer.LOCAL._0.1.0
└── windows/
    ├── StoreLimitlessServer.CLOUD._0.1.0.exe
    └── StoreLimitlessServer.LOCAL_0.1.0.exe
```

The filename spelling is intentionally preserved, including the slight difference in the Windows `LOCAL` name. The files are standalone PyInstaller
executables for their respective operating systems; Windows executables are produced through Wine when building on Linux.

### How HOST is set for each build

The script expects `core/config.py` to contain the following setting:

```python
HOST: Literal["LOCAL", "CLOUD"] = "LOCAL"
```

Before each PyInstaller run, it updates this assignment to the variant being built. It builds `LOCAL`, then `CLOUD`, for each selected operating
system. A backup of `core/config.py` is restored when the script exits, including on a failed build, so the working source configuration is not left
set to `CLOUD` by the packaging process.

The frontend is built once before packaging, but **it is bundled only into `LOCAL` executables**. The PyInstaller spec reads the
`INCLUDE_FRONTEND` environment variable:

```bash
# LOCAL executable: include frontend assets
INCLUDE_FRONTEND=1 python -m PyInstaller storelimitless-backend.spec

# CLOUD executable: exclude frontend assets
INCLUDE_FRONTEND=0 python -m PyInstaller storelimitless-backend.spec
```

`build.sh` sets this variable automatically for every Linux and Windows build. `storelimitless-backend.spec` defaults to including the frontend when
`INCLUDE_FRONTEND` is unset, preserving the expected behavior for direct/manual builds. This is separate from `HOST`: `HOST` determines startup
behavior, while `INCLUDE_FRONTEND` determines which static frontend files PyInstaller packages.

`LOCAL` serves the bundled frontend in a frozen executable. `CLOUD` uses the Vercel-hosted website and only tunnels the API. Neither variant creates
a public FTP tunnel.

### SDK binary copies

For each selected operating system, the script copies the `LOCAL` executable into the SDK's generated binary directory under
`Store-Limitless-api/src/storelimitless/bin/`. The standard SDK filename omits the `LOCAL`/`CLOUD` suffix:

```text
Store-Limitless-api/src/storelimitless/bin/
├── linux/
│   └── StoreLimitlessServer_0.1.0
└── windows/
    └── StoreLimitlessServer_0.1.0.exe
```

These SDK copies are generated artifacts, not the versioned root-level release files. The script never copies the `CLOUD` executable into the SDK
`bin` directory. Before copying, it removes stale `StoreLimitlessServer*` files in the selected platform's `bin` directory. An OS that was not
selected is not rebuilt.

### Build cleanup

Only after every selected build succeeds, `build.sh` removes regeneratable artifacts:

- `build/`, including temporary virtual environments, the Wine prefix, downloaded Windows Python installers, and PyInstaller work directories.
- `frontend/node_modules/` and `frontend/dist/`.
- `backend/storelimitlessbackend/.next/`, `node_modules/`, `.turbo/`, and `coverage/`, when that Next.js project directory exists.
- Project `__pycache__/` directories and `.pyc`/`.pyo` files, while pruning `.git`, `.venv`, `venv`, `node_modules`, and `.next` directories.
- `build.log`, removed last after the successful build output has been printed.

If a build fails, the final cleanup phase is skipped so intermediate artifacts and `build.log` remain available for diagnosing the failure. The script
preserves release executables and generated SDK binaries. It clears the contents of the release root and SDK binary root at the beginning of a build,
then recreates the expected platform directories; therefore, existing release outputs are replaced by the results of the selected build run.

Since the generated frontend assets and dependency directories are removed after a successful build, source-mode startup or the next build reinstalls
frontend dependencies and regenerates `frontend/dist`. The Next.js dependencies/build output can likewise be recreated from `package-lock.json`.

### Frontend runtime by build mode

- **LOCAL frozen executable:** PyInstaller includes `frontend/dist`; `backend/server/frontend.py` serves the SPA entry point and static assets, and
  `/config.js` exposes `window.STORELIMITLESS_CONFIG.API_URL` at runtime.
- **LOCAL source mode:** FastAPI runs alongside the Vite development server on port `5173`. Missing frontend dependencies are installed on startup.
- **CLOUD executable:** frontend assets are excluded by setting `INCLUDE_FRONTEND=0`. The application points users to the Vercel-hosted website and
  starts a Cloudflare tunnel only for the API. It must not attempt to serve a missing local `frontend/dist`. The separate Next.js proxy in
  `backend/storelimitlessbackend/` uses Upstash Redis to route requests to the latest registered tunnel.

Do not hand-edit generated JavaScript/CSS in `frontend/dist`; make changes under `frontend/src/` and rebuild. Packaging is platform-specific, so build
and smoke-test each target on its intended operating system where possible. A successful PyInstaller build alone does not prove that credentials,
writable directories, Redis, media tools, tunnel registration, or provider authentication work at runtime.
---

## Backups and runtime data

### Database backup utility

`backend/database/backup.py` exports base tables in the PostgreSQL `public` schema to `BACKUP_DIR`. Its command-line format argument supports CSV (the
default) and pretty-formatted terminal output.

From the repository root, with the application's Python dependencies and database configuration available:

```bash
python -m backend.database.backup
python -m backend.database.backup pretty
```

CSV output is useful for inspection and external archiving. This utility is an export script; do not assume it implements a full restore or migration
workflow. Test any restore procedure against a separate database before relying on it.

### What needs backing up

- PostgreSQL data, especially `users`, `directories`, and `files`.
- Configuration secrets, stored securely outside source control.
- Any required local operational state such as Telegram session files, if needed to re-authenticate the client.
- Build/release artifacts if you need to preserve a particular published release.

The part cache and transfer staging directory are disposable and should not be used as a backup source. Remote Discord/Telegram destination content is
the backing store for file bytes; keep access to those destinations and the metadata database aligned.

The repository tree includes CSV exports and runtime log/session files. Check these before publishing a repository: they may contain usernames,
filenames, metadata, tokens, logs, or session material. Do not assume a file is safe to commit just because it is not Python source.

---

## Security notes

This project should be treated as a self-hosted application that requires security hardening before public deployment.

1. **Rotate committed secrets.** The shared source contains hardcoded credentials/configuration values. Treat them as exposed, rotate them, and
   replace hardcoded constants with environment-based configuration or a secret manager.
2. **Move login credentials out of query strings.** The current frontend sends the username and password as query parameters for the login request.
   URLs may be retained by browser history, proxy/server logs, and monitoring systems. Change login to accept a JSON request body and update the
   frontend and backend together.
3. **Keep Redis private.** Redis listens on the local host and is an internal coordination service. Do not expose it directly to the internet.
4. **Do not use plain FTP over untrusted networks.** Add FTPS or another secure transport and configure passive data ports/firewall rules correctly
   before public use.
5. **Treat public links as secrets.** Signed public-link tokens do not currently contain an expiry. Add expiry and a revocation strategy if links must
   be time-limited.
6. **Use HTTPS in public deployments.** The frontend, bearer tokens, account data, and file links should not cross untrusted networks over plain HTTP.
7. **Review CORS and hosting mode.** `backend/server/app.py` has development-oriented local origins, and Cloud mode adds the selected website origin.
   Use an explicit allowlist appropriate for the deployment.
8. **Apply authorization checks to every item operation.** IDs from a client request are not proof of ownership. Keep ownership checks in the backend
   models/routes and FTP virtual filesystem.
9. **Protect backups and logs.** Database exports, logs, session files, and local transfer data may contain sensitive information.
10. **Validate the transfer lifecycle.** Monitor temporary disk usage and ensure failed/cancelled transfer jobs clean up their session keys and
    staging files.

---

## Troubleshooting

### The app never prints “Servers started”

- Check that PostgreSQL is reachable using the configured connection information.
- Check Redis on `127.0.0.1:6379`.
- Verify that Discord credentials are valid and the bot can access the configured dump channel.
- Check the log file configured by `core/config.py`.
- Remember that current startup waits for `Discord.ready`; a Discord startup failure can prevent the ready message even if another service has already
  started.

### The browser opens the landing page instead of login

- This is the current route design: `/` is the public product landing page.
- Open `/login` for sign-in or `/signup` for registration.

### Dashboard folder contents appear stale

- Use the dashboard's **Refresh** control to reload the complete directory tree through `GET /auth/tree`.
- A hard browser reload (F5) also clears the module-level in-memory tree cache and triggers an initial reload.
- If a completed upload is missing, check its tray/status and server logs; successful jobs normally refresh their destination directory automatically.

### The page loads but requests fail

- Confirm the API is listening on port `8000`.
- Check `VITE_API_URL` in development or `STORELIMITLESS_API_URL`/`/config.js` in packaged mode.
- If browsing from another device, do not use `127.0.0.1` as the server address.
- Confirm the website origin is permitted by CORS.
- Check that the bearer token in browser storage is valid and has not expired.

### Uploads stall or fail

- Check free disk space under `TRANSFER_PATH`.
- Confirm the selected data center name is exactly `Discord` or `Telegram`.
- Verify access to the configured remote dump destination.
- Check whether the temporary file still exists and whether the Redis upload session expired.
- For remote imports, submit one source per task. Direct links must return downloadable file content; Google Drive imports require an accessible file
  and configured Google API access; YouTube imports require `yt-dlp` and FFmpeg; magnet imports require `libtorrent` and reachable peers.
- For `t.me` message links, configure `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, start `@PremiumFiletoLink_Bot`, and authenticate the separate user
  session with `telegram-login`. Confirm the account can open and forward the source message. Read import-stage/error details in the upload tray.
- Inspect the returned upload status and server logs for the background transfer error.
- If Chrome displays **“Upload N files to this site?”** after choosing a folder, check the address first. The modern `showDirectoryPicker()` API is
  restricted to secure contexts; an HTTP LAN URL such as `http://192.168.x.x:5173` typically falls back to the legacy `webkitdirectory` input, whose
  confirmation is browser-controlled. Use HTTPS for the LAN website (or use a browser-supported secure context); JavaScript cannot reliably dismiss
  the browser's security prompt.

### Streaming or seeking fails

- Check that the database `size` and `links` list match the expected number of provider-sized parts.
- Check that every remote part link is still accessible to the configured provider account.
- Check the response status and `Content-Range` for range requests.
- A cache miss is normal; persistent download errors usually point to invalid metadata or provider access.
- Remember that the cache is cleared at startup, so the first read after a restart may need to fetch each required part again.

### FTP login succeeds but directory listing or transfers fail

- Confirm the FTP account exists in StoreLimitless and the password is correct.
- Confirm the client's root is the StoreLimitless virtual `/`, not the host operating system's root.
- For remote clients, configure passive FTP ports and their firewall/tunnel mappings. Reaching port `2121` alone does not guarantee that data
  connections will work.
- Check `TRANSFER_PATH` permissions and the configured storage provider.
- Do not troubleshoot remote passive transfers by exposing Redis or the PostgreSQL port.

### The packaged application cannot find the frontend

- Confirm the frontend build has been generated in `frontend/dist`.
- Confirm PyInstaller bundles the expected `frontend/dist` content.
- Check the runtime `BASE_DIR`/resource path logic in `core/config.py` and `backend/server/frontend.py`.
- Verify that `/config.js` is loaded before the compiled frontend bundle and that the API URL is correct for the target machine.

---

## Developer guide

### Where to make changes

- **Public landing/authentication or route behavior:** `frontend/src/pages/Landing.jsx`, `frontend/src/pages/Auth.jsx`, and `frontend/src/App.jsx`.
  The root route `/` is the landing page; `/login` and `/signup` open authentication directly.
- **New folder/file operation:** the corresponding router in `backend/server/routers/directories.py`, plus model methods as required.
- **New account behavior:** `backend/server/routers/accounts.py`, `backend/server/jwt_handler.py`, `backend/server/security.py`, and
  `backend/database/models/user.py`.
- **Browser upload behavior and cross-device tray synchronization:** `frontend/src/pages/dashboard/components/UploadModal.jsx`,
  `frontend/src/pages/dashboard/hooks/useUploadJobs.js`, `backend/server/routers/uploads.py`, `backend/server/routers/upload_state.py`,
  `backend/server/routers/public_routes.py`, and `core/utils/progress.py`. Keep the username-to-job Redis index and ownership checks aligned with the
  API contract.
- **New storage provider:** implement the provider interface in a module under `core/utils/`, register it in the `DataCenter` provider factory, add
  its name/configuration, and test upload and download paths.
- **Dashboard directory data, refresh, and recursive sizes:** `frontend/src/pages/dashboard/hooks/useDashboardData.js` and the authenticated
  `GET /auth/tree` endpoint in `backend/server/routers/directories.py`. Keep the full-tree response fields (`directories`, `files`, and
  `storage_used_bytes`) aligned with the hook. Preserve `GET /auth/directory` for targeted updates after local mutations.
- **Chunk layout or streaming behavior:** `core/data_center.py`, `core/transfer.py`, `core/utils/progress.py`, and `core/stream.py`. Keep upload chunk
  sizing and stream part calculations consistent.
- **FTP semantics:** `backend/ftp/filesystem/filesystem.py` and its upload/download adapters.
- **Runtime URLs or packaging:** `core/main.py`, `core/config.py`, `backend/server/frontend.py`, `frontend/src/App.jsx` and its API URL resolution,
  and `storelimitless-backend.spec`.
- **Global scrollbar or motion styling:** `frontend/src/index.css`. Keep global CSS aligned with any new scrollable components and reduced-motion
  requirements.

### Invariants worth preserving

- A `File` belongs to a user and a parent `Directory`.
- Directory and file lookups must always be scoped to the authenticated username.
- The `/home` directory is the logical root and must not become an ordinary movable/deletable folder.
- `File.links` order must match the order of chunks used by the transfer/stream logic.
- `file.size`, `DataCenter.MAX_SIZE`, and the number of remote links must stay consistent.
- Upload job metadata, the username-indexed job set, progress state, and active chunk-session state use different Redis keys; keep them consistent and
  ownership-scoped. Do not assume Redis job records alone can resume process-local background tasks after a runtime restart.
- Folder move/rename operations must update descendant paths.
- Public stream tokens and bearer tokens have distinct purposes: bearer tokens authorize account actions; public tokens authorize access to a specific
  file stream/download.
- `data/transfer/` in source mode (or the equivalent frozen data-directory path) is temporary. Canonical file bytes live in the configured storage
  provider, and canonical metadata lives in PostgreSQL.

### Python typing and linting

The project targets Python 3.13. Ruff is configured with the `ANN`, `UP`, and `TCH` rule families, and mypy is configured in strict mode for `backend/` and `core/`. Install the development tools with `python -m pip install -r requirements-dev.txt`, then run:

```bash
python -m compileall -q backend core
ruff check --select ANN .
ruff check .
mypy --strict backend core
```

`typing-checklist.md` inventories the tracked Python files. The strict mypy baseline still has unresolved diagnostics; do not treat the typing work as complete until those have been reviewed and the checks pass. Keep annotation changes behavior-preserving, particularly around FastAPI/Pydantic runtime annotations, async generators, upload/download coordination, and filesystem operations.

### Recommended verification after a change

At minimum, test the affected workflow through the same path a user uses:

1. Start the services with `python -m core.main`.
2. Register a test account and log in.
3. Create a nested directory and upload a small file.
4. Check the file metadata and download the file back.
5. Upload a larger file and seek within streamed media to exercise multiple stored parts and range handling.
6. Submit a test magnet link with reachable peers. Verify peer-search progress, download progress, sequential uploads, and the final file records.
7. Test a multi-file torrent and confirm its root folder and nested paths are recreated under the selected destination.
8. Try rename/move/trash/restore using the web UI and verify that the virtual FTP view sees the same state.
9. Test FTP list/download/upload with a client, including passive mode where remote access is required.
10. Restart the server and repeat a read to confirm that storage reconstruction does not depend on the disposable cache.
11. Verify `/` opens the public landing page, `/login` and `/signup` open the corresponding auth modes, and nested `/home/...` routes still work.
12. Navigate across several nested folders and confirm navigation uses the in-memory tree. Verify an uncached dashboard load, explicit Refresh,
    and hard reload each fetch the complete account tree through one `GET /auth/tree` request. Confirm the response includes active/trashed
    directories
    and files plus `storage_used_bytes`, is scoped to the authenticated account, and omits provider `links` from the full-tree payload. Also verify
    that
    local mutations can still refresh only the affected directory through `GET /auth/directory`.
13. Test move destination selection and deselection, including attempts to move a folder into itself or a descendant.
14. On a touch device, verify that a quick tap opens an item, a long-press enters selection mode, subsequent taps add/remove selected items,
    long-pressing
    empty workspace starts marquee selection, and long-press-and-drag moves items to valid folders/breadcrumbs. Confirm that ordinary finger movement
    still
    scrolls the workspace and that cancelled/out-of-bounds gestures do not trigger a move.
15. At the top of the scroll chain, test the app-managed refresh gesture with a deliberate upward mouse-wheel gesture and a downward touch pull; verify
    ordinary scrolling does not trigger it and that the dashboard's explicit Refresh button still reloads file/folder data.
16. With at least two active files selected, create a named ZIP download and verify the browser receives a valid archive while the backend streams source
    files without staging a complete ZIP on disk. Check that an expired ZIP token is rejected and that the browser-facing response includes the required
    CORS headers in CLOUD mode.
17. Exercise upload progress, cancellation, failure reporting, dismissal, and automatic directory synchronization after completion. Open the same
    account on a second browser/device and verify that jobs and terminal actions are synchronized through the shared API and Redis instance. Expand the
    upload tray, then minimize it and confirm it returns to the originating directory.
18. If Telegram message-link import is enabled, authenticate the user session, submit an accessible test `t.me` file-message URL, and verify bot
    resolution, download, storage upload, and failure behavior when the source or processing bot is unavailable.
19. Run the frontend lint/build scripts that are actually declared in `frontend/package.json`.
20. Test the packaged executable separately from development mode.

Automated tests should be added for range parsing, chunk-count validation, upload-range merging, folder path rewriting, ownership/authorization,
public-token expiry, and FTP seek behavior.

---

## Known boundaries and verification notes

- The separate SDK source package is not part of the tracked application tree documented here. `build.sh` generates versioned `LOCAL` and `CLOUD`
  executables under the repository-root `releases/linux` and `releases/windows` directories, and copies only the selected platform's `LOCAL`
  executable to the SDK's
  generated `Store-Limitless-api` directory using the standard `StoreLimitlessServer_<VERSION>` name.
- URL imports support YouTube processing, Google Drive links, and direct HTTP (S) download URLs. A syntactically valid HTTP (S) URL is not guaranteed
  to
  be a downloadable file; ordinary HTML pages and sources requiring unavailable authentication can fail.
- The configured storage backends in the current source are Discord and Telegram; this is not a generic plug-and-play cloud-provider system yet.
- The FTP server is a virtual filesystem adapter, not a full local filesystem shell. Symbolic links and chmod are unsupported.
- Cloud mode tunnels the API and uses the separately hosted Vercel frontend; it does not start a public FTP tunnel or configure FTP passive-port
  ranges. Cross-device upload-tray synchronization also requires all clients to share the same API backend and Redis data store.
- The database backup helper exports data; a matching tested restore/migration process is not established by the supplied code.
- The README describes the supplied source and repository tree, including the single-request full-tree endpoint (`GET /auth/tree`), targeted directory
  refreshes, current landing/authentication routes, dashboard cache/refresh behavior, move dialog, and mobile touch gestures. Gesture descriptions
  document
  the current implementation; they do not imply physical-device testing or guarantee identical behavior across all mobile browsers. Not every
  deployment
  mode or workflow has been integration-tested.

For licensing terms, see the repository's `LICENSE` file.

### Recent reliability updates

- Torrent selection tasks now expose per-file download progress and per-file sequential upload progress in the upload tray. The selection dialog
  closes as soon as the first selected torrent job is accepted, while the task continues in the background.
- Upload concurrency is configured per storage provider through `DataCenter.MAX_PARALLEL_UPLOADS`: the source currently sets Discord to two concurrent
  part uploads and Telegram to eight. The README value must be kept in sync with the provider class constants.
- Torrent file-level progress is persisted with the child upload status so the tray can show individual status, byte counts, and progress bars rather
  than only a batch-level percentage.
- Streaming storage-chunk reads use a bounded long timeout and retry policy, Discord CDN reads retry incomplete responses without a fixed total-read
  timeout, and the stream iterator prefetches the next storage part in the background. The stream endpoint continues to support HTTP byte ranges,
  seeking, HEAD/ETag-assisted downloads, and resumable range transfer.
- YouTube imports publish separate video download, audio download, merge, and storage-upload stages; the UI normalizes transient URL/ID placeholders
  so a resolved title can replace the source URL as the display name.
- Torrent selection uses checkboxes as the source of truth for which files to import. All files start checked by default; keyboard focus highlighting
  is separate, and the picker isolates arrow-key navigation from the dashboard.
- A Telegram `t.me` file-message import runs as a background task, forwards the source media message through the separately authenticated user session,
  extracts the actual `FAST DOWNLOAD` target from the processing bot response, follows redirects, and then enters the normal download/upload workflow.
  It fails closed if the original message, session, bot reply, or download target cannot be resolved.
- Direct HTTP(S) imports can resume interrupted server-side downloads with validated HTTP byte-range requests. Data is appended only when the upstream
  confirms the requested offset, so unsupported range servers fail instead of silently producing a corrupted file.
- Multiple selected active files can be downloaded as a named ZIP. The ZIP64 writer streams from remote file readers without first staging source files or
  a complete archive on the server; its signed download token is short-lived.
- Uploads/imports and browser downloads now appear together in a single **Task tray** that can be minimized or maximized. The full-screen `/upload-tray` route returns to its originating dashboard folder. Upload and torrent items retain per-file progress, while supported browser downloads expose resumable progress and pause/resume controls. Sidebar Upload controls open the upload dialog. An app-managed top-edge gesture reloads the page after an intentional upward mouse-wheel gesture or downward touch pull at the top of the scroll chain.
- The custom raw-stream video player exposes playback speed from 0.25x to 2x, progress/volume/fullscreen controls, keyboard shortcuts, and mobile double-tap seeking by 10 seconds in the left or right screen zones. Media stays in the original byte-range stream rather than being transcoded on the server.
- Google Drive file imports use a growing unbuffered destination for concurrent download/upload. Google Drive folder imports use bounded parallel file downloads and submit each ready file for upload as it completes, with per-file progress instead of waiting for the whole folder to finish.
- Public file, stream, and ZIP responses include production-origin CORS headers so browser downloads can follow the stable Vercel proxy to the current
  Cloudflare tunnel. The allowlist is in `backend/server/routers/public_routes.py` and must be kept in sync with the deployed frontend origin.
- The Redis process started by StoreLimitless writes stdout/stderr to `logs.log`; discord.py's default console logging handler is disabled so client and
  gateway messages can flow through the application's file logger. Expected Cloudflare disconnect noise is filtered from application logs.
- Running `./build.sh` without arguments now builds only the Linux `CLOUD` executable. Supplying the two platform flags retains the explicit multi-platform
  build workflow.
