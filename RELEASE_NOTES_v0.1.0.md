# StoreLimitless Server v0.1.0

The first packaged release of **StoreLimitless Server**, a self-hosted personal cloud-storage application with a web dashboard, FastAPI backend, PostgreSQL metadata, Redis-backed task coordination, remote file storage, and an FTP-compatible virtual filesystem.

This release includes four prebuilt executables for **Linux and Windows**, with **LOCAL** and **CLOUD** deployment modes. The filenames below match the uploaded release assets exactly.

## Downloads

| Platform | Configuration | Release asset |
|---|---|---|
| Linux | LOCAL | [`StoreLimitlessServer.LOCAL._0.1.0`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.LOCAL._0.1.0) |
| Linux | CLOUD | [`StoreLimitlessServer.CLOUD._0.1.0`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.CLOUD._0.1.0) |
| Windows | LOCAL | [`StoreLimitlessServer.LOCAL_0.1.0.exe`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.LOCAL_0.1.0.exe) |
| Windows | CLOUD | [`StoreLimitlessServer.CLOUD._0.1.0.exe`](https://github.com/Dream2503/Store-Limitless/releases/download/v0.1.0/StoreLimitlessServer.CLOUD._0.1.0.exe) |

- **LOCAL** bundles the web frontend with the server executable.
- **CLOUD** excludes the frontend bundle and uses the hosted StoreLimitless website with the configured API tunnel/proxy setup.

## Features

- **Web dashboard and accounts** for signing in, browsing a virtual home directory, and organizing nested folders.
- **Multi-file and folder uploads** that preserve relative paths for nested directory structures.
- **One remote source per import task**, supporting direct HTTP(S) downloads, Google Drive files and public folders, YouTube URLs, magnet torrents with file selection, and supported Telegram file-message links. Multiple local files/folders can still be submitted as a batch; newline-separated remote URLs are not treated as a multi-link batch.
- **Google Drive ingestion progress** with a growing-file path for single-file concurrent download/upload, plus bounded parallel folder downloads that submit each ready file for upload without waiting for every file in the folder.
- **Torrent file selection and progress**, including per-file download/upload status, queued items, and cancellation/recovery states in the Task tray.
- **Unified Task tray** for uploads, remote imports, and browser downloads, with live progress, per-file status, task dismissal, upload cancellation, and minimize/maximize/full-screen controls. Supported browser downloads also expose pause/resume.
- **Resumable downloads** using validated HTTP byte ranges and browser storage where the browser supports the File System Access API. Browser download recovery depends on browser permissions and support.
- **Streaming ZIP downloads** for selected files and folders. The backend streams a ZIP64 archive from remote storage rather than staging every source file or a complete archive on disk.
- **Raw video streaming** through HTTP byte ranges for seeking, with no server-side video transcoding or separate audio/subtitle extraction. The custom player includes playback speed from 0.25x to 2x, seeking, volume, fullscreen, keyboard shortcuts, and mobile double-tap seeking by 10 seconds on the left/right sides.
- **File previews** for supported audio, PDF, Markdown, and common text/source/configuration formats.
- **Trash and file management** including rename, move, restore, permanent deletion, bulk actions, keyboard shortcuts, and mouse/touch selection workflows.
- **FTP-compatible virtual filesystem** backed by the same account and directory metadata as the web application.
- **Remote storage providers** with Discord and Telegram storing file bytes, PostgreSQL storing metadata, and Redis coordinating background task/progress state.
- **Cross-platform packaged builds** for Linux and Windows in LOCAL and CLOUD configurations.

## Getting Started

1. Download the executable matching your operating system and preferred deployment mode from the table above.
2. Review the project's [README.md](https://github.com/Dream2503/Store-Limitless/blob/main/README.md) for service setup, configuration variables, startup behavior, and troubleshooting.
3. Configure PostgreSQL, Redis, provider credentials, and required network access before launching the server.
4. For CLOUD mode, ensure the hosted frontend and Cloudflare tunnel/Upstash proxy setup are configured. CLOUD mode does not bundle or serve the frontend locally and does not expose a public FTP tunnel.
5. Start the executable, review its startup output, and open the web dashboard at the printed or configured address.

## Requirements and Compatibility Notes

- A packaged executable does not automatically provision PostgreSQL, Redis, Discord/Telegram credentials, Google Drive API access, or deployment networking.
- Google Drive imports depend on link permissions and supported Google Drive behavior. YouTube imports require the associated download/media dependencies, including `yt-dlp` and FFmpeg. Magnet torrents require libtorrent and network access to peers/trackers/DHT. Telegram message-link imports require a configured and separately authenticated Telethon user session.
- File playback in a browser depends on the browser's codec support. The **Copy stream URL** action can be used to open the raw stream in VLC; treat the signed URL as a bearer link.
- FTP is a virtual filesystem adapter, not a full host-filesystem shell. Configure FTP reachability and passive-mode networking separately when needed.
- This is an early **v0.1.0** release. Some source sites, browser APIs, network conditions, and external-service configurations can limit specific workflows.

## Security and Data

- Keep API secrets, provider tokens, Telegram session files, database credentials, and Redis credentials private.
- Restrict public access to the service, configure authentication and network exposure deliberately, and do not share signed stream/download links for sensitive files.
- Back up database metadata and runtime configuration before upgrading or changing deployments. The backup utility exports data, but a fully tested automatic restore/migration process is not established by the current project.

For full installation instructions, configuration details, API routes, build instructions, and troubleshooting, see the [project README](https://github.com/Dream2503/Store-Limitless/blob/main/README.md).
