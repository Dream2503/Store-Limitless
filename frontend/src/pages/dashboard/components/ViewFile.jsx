import {useCallback, useEffect, useRef, useState} from "react";
import {useNavigate, useParams, useSearchParams} from "react-router-dom";
import FileContentPreview, {MediaPreview} from "./FileContentPreview.jsx";
import UploadJobsTray from "./UploadJobsTray.jsx";
import {createUniqueId, getFileKind} from "../utils.js";
import {listResumableDownloadChunks, removeResumableDownloadChunks, removeResumableDownloadChunksFrom, saveResumableDownloadChunk} from "../utils/resumableDownloads.js";
import {API_URL} from "../../../api.js";

/**
 * @typedef {{name: string}} PreviewFile
 */
/**
 * @typedef {Object} VideoPlayerProps
 * @property {PreviewFile} file
 * @property {string} streamUrl
 * @property {string} [heightClass]
 */
/** @typedef {{streamUrl: string}} VideoSource */


/**
 * Play the original stored file directly with lightweight custom controls.
 * @param {VideoPlayerProps} props
 * @returns {import("react").ReactElement}
 */
function VideoPlayer({file, streamUrl, heightClass = "h-[70vh]"}) {
    const wrapRef = useRef(null);
    const videoRef = useRef(null);
    const lastSideTapRef = useRef({side: "", time: 0});
    const feedbackTimeoutRef = useRef(null);

    const [playing, setPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [volume, setVolume] = useState(1);
    const [muted, setMuted] = useState(false);
    const [playbackRate, setPlaybackRate] = useState(1);
    const [fullscreen, setFullscreen] = useState(false);
    const [seekFeedback, setSeekFeedback] = useState("");

    const formatTime = (value) => {
        if (!Number.isFinite(value) || value < 0) return "0:00";
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor((value % 3600) / 60);
        const seconds = Math.floor(value % 60).toString().padStart(2, "0");
        return hours > 0
            ? `${hours}:${minutes.toString().padStart(2, "0")}:${seconds}`
            : `${minutes}:${seconds}`;
    };

    const togglePlayback = () => {
        const video = videoRef.current;
        if (!video) return;
        if (video.paused) {
            void video.play().catch(() => {});
        } else {
            video.pause();
        }
    };

    const seekBy = (seconds) => {
        const video = videoRef.current;
        if (!video || !Number.isFinite(video.duration)) return;
        video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + seconds));
        setCurrentTime(video.currentTime);
    };

    const showSeekFeedback = (text) => {
        setSeekFeedback(text);
        window.clearTimeout(feedbackTimeoutRef.current);
        feedbackTimeoutRef.current = window.setTimeout(() => setSeekFeedback(""), 650);
    };

    const handleVideoPointerUp = (event) => {
        const video = videoRef.current;
        if (!video) return;

        const bounds = video.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;

        const x = (event.clientX - bounds.left) / bounds.width;
        const y = (event.clientY - bounds.top) / bounds.height;
        const centerTap = x >= 0.33 && x <= 0.67 && y >= 0.25 && y <= 0.75;

        if (event.pointerType === "touch") {
            if (centerTap) {
                lastSideTapRef.current = {side: "", time: 0};
                togglePlayback();
                return;
            }

            const side = x < 0.33 ? "left" : x > 0.67 ? "right" : "";
            if (!side) {
                lastSideTapRef.current = {side: "", time: 0};
                return;
            }

            const now = event.timeStamp;
            const lastTap = lastSideTapRef.current;
            if (lastTap.side === side && now - lastTap.time <= 320) {
                const seconds = side === "right" ? 10 : -10;
                seekBy(seconds);
                showSeekFeedback(side === "right" ? "+10s" : "−10s");
                lastSideTapRef.current = {side: "", time: 0};
            } else {
                // A single tap on either side does nothing. Only the second tap seeks.
                lastSideTapRef.current = {side, time: now};
            }
            return;
        }

        // Desktop clicks only toggle play/pause in the center, never on the edges.
        if (event.pointerType === "mouse" && event.button === 0 && centerTap) {
            togglePlayback();
        }
    };

    const toggleFullscreen = async () => {
        const target = wrapRef.current;
        if (!target) return;
        try {
            if (document.fullscreenElement) {
                await document.exitFullscreen();
            } else {
                await target.requestFullscreen();
            }
        } catch {
            // Fullscreen is optional and may be blocked by the browser.
        }
    };

    useEffect(() => {
        const handleFullscreenChange = () => setFullscreen(document.fullscreenElement === wrapRef.current);
        document.addEventListener("fullscreenchange", handleFullscreenChange);
        return () => {
            document.removeEventListener("fullscreenchange", handleFullscreenChange);
            window.clearTimeout(feedbackTimeoutRef.current);
        };
    }, []);

    return (
        <div
            ref={wrapRef}
            className={`relative ${heightClass} min-h-0 w-full overflow-hidden bg-black text-white select-none`}
        >
            <video
                ref={videoRef}
                className="absolute inset-0 h-full w-full bg-black object-contain"
                src={streamUrl}
                preload="metadata"
                playsInline
                disablePictureInPicture
                aria-label={file?.name || "Video preview"}
                onPointerUp={handleVideoPointerUp}
                onContextMenu={(event) => event.preventDefault()}
                onLoadedMetadata={(event) => {
                    setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
                    setCurrentTime(event.currentTarget.currentTime || 0);
                    setVolume(event.currentTarget.volume);
                    setMuted(event.currentTarget.muted);
                    setPlaybackRate(event.currentTarget.playbackRate);
                }}
                onDurationChange={(event) => {
                    if (Number.isFinite(event.currentTarget.duration)) setDuration(event.currentTarget.duration);
                }}
                onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime || 0)}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onVolumeChange={(event) => {
                    setVolume(event.currentTarget.volume);
                    setMuted(event.currentTarget.muted);
                }}
                onRateChange={(event) => setPlaybackRate(event.currentTarget.playbackRate)}
            />

            {seekFeedback && (
                <div className={`pointer-events-none absolute inset-y-0 flex w-1/3 items-center justify-center ${seekFeedback.startsWith("+") ? "right-0" : "left-0"}`}>
                    <span className="rounded-full bg-black/70 px-4 py-2 text-lg font-semibold">{seekFeedback}</span>
                </div>
            )}

            <div className="absolute inset-x-0 bottom-6 bg-gradient-to-t from-black/95 via-black/65 to-transparent px-3 pb-3 pt-12 sm:px-5">
                <div className="mb-2 flex items-center gap-3">
                    <span className="w-11 shrink-0 text-right text-xs tabular-nums text-white/90">{formatTime(currentTime)}</span>
                    <input
                        type="range"
                        min={0}
                        max={duration || 0}
                        step={0.1}
                        value={Math.min(currentTime, duration || 0)}
                        aria-label="Seek video"
                        onChange={(event) => {
                            const video = videoRef.current;
                            if (!video) return;
                            video.currentTime = Number(event.target.value);
                            setCurrentTime(video.currentTime);
                        }}
                        className="h-1.5 min-w-0 flex-1 cursor-pointer accent-[#3ecf8e]"
                    />
                    <span className="w-11 shrink-0 text-xs tabular-nums text-white/90">{formatTime(duration)}</span>
                </div>

                <div className="flex min-w-0 items-center gap-2 sm:gap-3">
                    <button
                        type="button"
                        onClick={togglePlayback}
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-white transition-colors hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                        aria-label={playing ? "Pause video" : "Play video"}
                        title={playing ? "Pause" : "Play"}
                    >
                        <span className="material-symbols-outlined text-[25px]" aria-hidden="true">{playing ? "pause" : "play_arrow"}</span>
                    </button>

                    <button
                        type="button"
                        onClick={() => seekBy(-10)}
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-white transition-colors hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                        aria-label="Back 10 seconds"
                        title="Back 10 seconds"
                    >
                        <span className="material-symbols-outlined text-[22px]" aria-hidden="true">replay_10</span>
                    </button>
                    <button
                        type="button"
                        onClick={() => seekBy(10)}
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-white transition-colors hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                        aria-label="Forward 10 seconds"
                        title="Forward 10 seconds"
                    >
                        <span className="material-symbols-outlined text-[22px]" aria-hidden="true">forward_10</span>
                    </button>

                    <div className="ml-auto flex min-w-0 items-center gap-2 sm:gap-3">
                        <button
                            type="button"
                            onClick={() => {
                                const video = videoRef.current;
                                if (!video) return;
                                video.muted = !video.muted;
                            }}
                            className="flex h-9 w-8 shrink-0 items-center justify-center rounded-md text-white transition-colors hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                            aria-label={muted || volume === 0 ? "Unmute video" : "Mute video"}
                            title={muted || volume === 0 ? "Unmute" : "Mute"}
                        >
                            <span className="material-symbols-outlined text-[21px]" aria-hidden="true">{muted || volume === 0 ? "volume_off" : volume < 0.5 ? "volume_down" : "volume_up"}</span>
                        </button>
                        <input
                            type="range"
                            min={0}
                            max={1}
                            step={0.05}
                            value={muted ? 0 : volume}
                            aria-label="Volume"
                            onChange={(event) => {
                                const video = videoRef.current;
                                if (!video) return;
                                video.volume = Number(event.target.value);
                                video.muted = video.volume === 0;
                            }}
                            className="hidden w-20 cursor-pointer accent-[#3ecf8e] sm:block"
                        />

                        <label className="flex shrink-0 items-center gap-1.5 text-xs text-white/90">
                            <span className="hidden sm:inline">Speed</span>
                            <select
                                value={playbackRate}
                                aria-label="Playback speed"
                                onChange={(event) => {
                                    const video = videoRef.current;
                                    const nextRate = Number(event.target.value);
                                    setPlaybackRate(nextRate);
                                    if (video) video.playbackRate = nextRate;
                                }}
                                className="h-8 rounded-md border border-white/20 bg-[#171717] px-1.5 text-xs text-white outline-none focus:border-[#3ecf8e]"
                            >
                                {[0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2].map((rate) => (
                                    <option key={rate} value={rate}>{rate}×</option>
                                ))}
                            </select>
                        </label>

                        <button
                            type="button"
                            onClick={() => void toggleFullscreen()}
                            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-white transition-colors hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                            aria-label={fullscreen ? "Exit fullscreen" : "Enter fullscreen"}
                            title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
                        >
                            <span className="material-symbols-outlined text-[22px]" aria-hidden="true">{fullscreen ? "fullscreen_exit" : "fullscreen"}</span>
                        </button>
                    </div>
                </div>
            </div>

            <p className="pointer-events-none absolute inset-x-0 bottom-0 border-t border-white/10 bg-[#171717]/95 px-4 py-1.5 text-center text-[11px] text-[#a7a7a7]">
                If this format does not play in your browser, copy the stream URL above and open it in VLC.
            </p>
        </div>
    );
}

/**
 * Resolve a signed stream URL and render the selected preview.
 * @returns {import("react").ReactElement}
 */
export default function ViewFile() {
    const {fileId: routeId} = useParams();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const isNumericFileId = /^\d+$/.test(String(routeId || ""));
    const fileId = isNumericFileId ? routeId : null;
    // Keep old query-token links working while accepting the shorter /view/<token> form.
    const publicToken = searchParams.get("public_token") || (!isNumericFileId ? routeId : null);
    const [publicFile, setPublicFile] = useState(null);
    const [publicFileError, setPublicFileError] = useState("");
    const [privateFileMetadata, setPrivateFileMetadata] = useState(null);
    const [privateFilePending, setPrivateFilePending] = useState(true);
    const [privateFileError, setPrivateFileError] = useState("");
    const [downloadUrl, setDownloadUrl] = useState("");
    const [downloadPending, setDownloadPending] = useState(true);
    const [downloadError, setDownloadError] = useState("");
    const [downloadState, setDownloadState] = useState({status: "idle", transferred: 0, total: 0, message: ""});
    const downloadControllerRef = useRef(null);
    const downloadMountedRef = useRef(true);
    const downloadDismissedRef = useRef(false);
    const downloadSessionRef = useRef(null);
    if (!downloadSessionRef.current) {
        downloadSessionRef.current = {
            id: `public-view-${createUniqueId()}`,
            tempName: "",
            total: 0,
            etag: "",
            transferred: 0,
        };
    }
    const downloadTrayJobId = `view-file-download-${fileId || publicToken || "shared"}`;
    const privateFile = privateFileMetadata?.routeId === String(fileId) ? privateFileMetadata : null;
    const name = searchParams.get("name") || publicFile?.name || privateFile?.name || "Untitled";
    const kind = searchParams.get("kind") || getFileKind(publicFile?.name || privateFile?.name || name);
    const downloadTrayJobs = downloadError || downloadState.status !== "idle" ? [{
        id: downloadTrayJobId,
        name,
        status: downloadError && downloadState.status === "idle" ? "error" : downloadState.status,
        transferred: downloadState.transferred,
        total: downloadState.total,
        message: downloadError || downloadState.message || "Preparing StoreLimitless download",
        viewerDownload: true,
    }] : [];

    useEffect(() => {
        if (publicToken || !fileId) {
            setPrivateFilePending(false);
            setPrivateFileError("");
            return undefined;
        }

        let cancelled = false;
        const controller = new AbortController();
        const sessionToken = localStorage.getItem("token");
        setPrivateFilePending(true);
        setPrivateFileError("");
        setPrivateFileMetadata(null);

        if (!sessionToken) {
            setPrivateFileError("Sign in to open this file.");
            setPrivateFilePending(false);
            return () => controller.abort();
        }

        (async () => {
            try {
                const response = await fetch(
                    `${API_URL}/auth/file/${encodeURIComponent(fileId)}/metadata`,
                    {
                        headers: {Authorization: `Bearer ${sessionToken}`},
                        signal: controller.signal,
                        cache: "no-store",
                    },
                );
                const data = await response.json().catch(() => ({}));
                if (response.status === 401) {
                    localStorage.removeItem("token");
                    navigate("/");
                    return;
                }
                if (!response.ok) throw new Error(data.detail || "Could not load this file's metadata.");
                if (!data.name) throw new Error("The server did not return file metadata.");
                if (!cancelled) setPrivateFileMetadata({...data, routeId: String(fileId)});
            } catch (error) {
                if (!cancelled && error?.name !== "AbortError") {
                    setPrivateFileError(error instanceof Error ? error.message : "Could not load this file's metadata.");
                }
            } finally {
                if (!cancelled) setPrivateFilePending(false);
            }
        })();

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [fileId, navigate, publicToken]);

    useEffect(() => {
        if (!publicToken) {
            setPublicFile(null);
            setPublicFileError("");
            return;
        }

        let cancelled = false;
        setPublicFile(null);
        setPublicFileError("");

        (async () => {
            try {
                const response = await fetch(`${API_URL}/public/metadata/${encodeURIComponent(publicToken)}`);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Could not open this shared file.");
                if (!data.name) throw new Error("The public link did not return file information.");
                if (!cancelled) setPublicFile(data);
            } catch (err) {
                if (!cancelled) setPublicFileError(err instanceof Error ? err.message : "Could not open this shared file.");
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [publicToken]);

    // Prepare a public download URL for both compact share links and normal signed-in views.
    // The backend sends Content-Disposition: attachment, so this works for every file type.
    useEffect(() => {
        let cancelled = false;
        setDownloadUrl("");
        setDownloadError("");

        if (publicToken) {
            if (publicFile) {
                setDownloadUrl(`${API_URL}/public/download/${encodeURIComponent(publicToken)}`);
                setDownloadPending(false);
            } else if (publicFileError) {
                setDownloadError(publicFileError);
                setDownloadPending(false);
            } else {
                setDownloadPending(true);
            }
            return () => {
                cancelled = true;
            };
        }

        if (!fileId) {
            setDownloadError("No file was selected.");
            setDownloadPending(false);
            return () => {
                cancelled = true;
            };
        }

        const sessionToken = localStorage.getItem("token");
        if (!sessionToken) {
            setDownloadError("Sign in to download this file.");
            setDownloadPending(false);
            return () => {
                cancelled = true;
            };
        }

        setDownloadPending(true);
        (async () => {
            try {
                const response = await fetch(`${API_URL}/auth/file/${encodeURIComponent(fileId)}/public-link`, {
                    method: "POST",
                    headers: {Authorization: `Bearer ${sessionToken}`},
                });
                const data = await response.json().catch(() => ({}));
                if (response.status === 401) {
                    localStorage.removeItem("token");
                    throw new Error("Your session has expired. Sign in again to download this file.");
                }
                if (!response.ok) throw new Error(data.detail || "Could not prepare the download.");
                if (!data.public_token) throw new Error("The server did not return a download token.");
                if (!cancelled) setDownloadUrl(`${API_URL}/public/download/${encodeURIComponent(data.public_token)}`);
            } catch (err) {
                if (!cancelled) setDownloadError(err instanceof Error ? err.message : "Could not prepare the download.");
            } finally {
                if (!cancelled) setDownloadPending(false);
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [fileId, navigate, publicFile, publicFileError, publicToken]);

    useEffect(() => {
        downloadMountedRef.current = true;
        return () => {
            downloadMountedRef.current = false;
            downloadDismissedRef.current = true;
            downloadControllerRef.current?.abort();
            const session = downloadSessionRef.current;
            if (session.tempName && typeof navigator.storage?.getDirectory === "function") {
                navigator.storage.getDirectory().then((root) => root.removeEntry(session.tempName)).catch(() => {});
            }
            removeResumableDownloadChunks(session.id).catch(() => {});
        };
    }, []);

    /**
     * Stage the file in StoreLimitless temporary storage before triggering the browser save.
     * This deliberately avoids a direct link to the download endpoint.
     * @returns {Promise<void>}
     */
    const startStagedDownload = useCallback(async () => {
        if (!downloadUrl || downloadControllerRef.current) return;
        downloadDismissedRef.current = false;
        const controller = new AbortController();
        downloadControllerRef.current = controller;
        let root = null;
        let tempName = "";
        let totalForTask = 0;
        let offsetForTask = 0;
        const session = downloadSessionRef.current;
        const updateState = (next) => {
            if (downloadMountedRef.current && !downloadDismissedRef.current) setDownloadState(next);
        };

        updateState({status: "downloading", transferred: session.transferred || 0, total: session.total || 0, message: "Preparing StoreLimitless download"});
        try {
            if (typeof navigator.storage?.getDirectory !== "function") {
                // Stage verified ranges in IndexedDB when OPFS is unavailable. Keep
                // completed chunks across Pause so Resume continues from the last range.
                const stagedDownloadId = session.id;
                const head = await fetch(downloadUrl, {method: "HEAD", signal: controller.signal, cache: "no-store"});
                if (!head.ok) throw new Error(`Could not prepare download (${head.status}).`);

                const sizeHeader = head.headers.get("content-length");
                const total = sizeHeader !== null ? Number(sizeHeader) : Number(publicFile?.size);
                if (!Number.isSafeInteger(total) || total < 0 || (sizeHeader === null && publicFile?.size == null)) {
                    throw new Error("The server did not provide a valid file size for staging this download.");
                }

                const etag = head.headers.get("etag") || "";
                const contentType = head.headers.get("content-type") || "application/octet-stream";
                const chunkSize = 8 * 1024 * 1024;
                let savedChunks = await listResumableDownloadChunks(stagedDownloadId);
                const sourceChanged = (Boolean(session.etag && etag) && session.etag !== etag)
                    || (Number(session.total) > 0 && Number(session.total) !== total);
                if (sourceChanged) {
                    await removeResumableDownloadChunks(stagedDownloadId);
                    savedChunks = [];
                    session.transferred = 0;
                }
                session.total = total;
                session.etag = etag;
                totalForTask = total;

                const chunksByOffset = new Map(savedChunks.map((chunk) => [Number(chunk.offset), chunk]));
                let offset = 0;
                while (offset < total) {
                    const savedChunk = chunksByOffset.get(offset);
                    if (!savedChunk) break;
                    const length = savedChunk.bytes?.byteLength ?? savedChunk.bytes?.length ?? 0;
                    if (length !== Math.min(chunkSize, total - offset)) break;
                    offset += length;
                }
                if (savedChunks.some((chunk) => Number(chunk.offset) >= offset)) {
                    await removeResumableDownloadChunksFrom(stagedDownloadId, offset);
                    savedChunks = savedChunks.filter((chunk) => Number(chunk.offset) < offset);
                }

                offsetForTask = offset;
                session.transferred = offset;
                updateState({
                    status: "downloading", transferred: offset, total,
                    message: offset ? "Resuming from saved progress" : "Downloading · staging in browser storage",
                });

                while (offset < total) {
                    if (controller.signal.aborted) throw new DOMException("Download paused", "AbortError");
                    const end = Math.min(offset + chunkSize, total) - 1;
                    let chunk = null;
                    let lastError = null;
                    for (let attempt = 0; attempt < 5; attempt += 1) {
                        try {
                            // Keep this to a single CORS-safelisted byte-range header.
                            const headers = {Range: `bytes=${offset}-${end}`};
                            const requestSignal = typeof AbortSignal.any === "function" && typeof AbortSignal.timeout === "function"
                                ? AbortSignal.any([controller.signal, AbortSignal.timeout(180000)])
                                : controller.signal;
                            const response = await fetch(downloadUrl, {headers, signal: requestSignal, cache: "no-store"});
                            if (response.status !== 206) {
                                await response.body?.cancel();
                                throw new Error(response.status === 200
                                    ? "The server did not honor the byte-range request; refusing an unsafe staged download."
                                    : `Download range failed (${response.status}).`);
                            }
                            const responseEtag = response.headers.get("etag") || "";
                            if (etag && responseEtag && responseEtag !== etag) {
                                await response.body?.cancel();
                                throw new Error("The file changed during download. Retry to fetch a consistent copy.");
                            }
                            const expectedRange = `bytes ${offset}-${end}/${total}`;
                            const receivedRange = response.headers.get("content-range") || "";
                            if (receivedRange !== expectedRange) {
                                await response.body?.cancel();
                                throw new Error(`Unexpected Content-Range: ${receivedRange || "missing"}`);
                            }
                            const bytes = await response.arrayBuffer();
                            if (bytes.byteLength !== end - offset + 1) {
                                throw new Error(`Incomplete range: received ${bytes.byteLength} bytes.`);
                            }
                            chunk = bytes;
                            break;
                        } catch (error) {
                            if (controller.signal.aborted || error?.name === "AbortError") throw error;
                            lastError = error;
                            if (attempt < 4) {
                                await new Promise((resolve, reject) => {
                                    let timeout;
                                    const onAbort = () => {
                                        window.clearTimeout(timeout);
                                        reject(new DOMException("Download paused", "AbortError"));
                                    };
                                    timeout = window.setTimeout(() => {
                                        controller.signal.removeEventListener("abort", onAbort);
                                        resolve();
                                    }, Math.min(1000 * (2 ** attempt), 8000));
                                    controller.signal.addEventListener("abort", onAbort, {once: true});
                                });
                            }
                        }
                    }
                    if (!chunk) throw lastError || new Error("Download failed after repeated attempts.");
                    await saveResumableDownloadChunk(stagedDownloadId, offset, chunk);
                    offset += chunk.byteLength;
                    offsetForTask = offset;
                    session.transferred = offset;
                    updateState({status: "downloading", transferred: offset, total, message: "Downloading · staging in browser storage"});
                }

                savedChunks = await listResumableDownloadChunks(stagedDownloadId);
                const blobParts = [];
                let verifiedOffset = 0;
                for (const savedChunk of savedChunks) {
                    if (Number(savedChunk.offset) !== verifiedOffset) {
                        throw new Error("The staged download has a gap. Resume the download to repair it.");
                    }
                    const bytes = savedChunk.bytes;
                    const length = bytes?.byteLength ?? bytes?.length ?? 0;
                    if (length !== Math.min(chunkSize, total - verifiedOffset)) {
                        throw new Error("A staged download range is incomplete. Resume the download to repair it.");
                    }
                    blobParts.push(bytes);
                    verifiedOffset += length;
                    if (verifiedOffset === total) break;
                }
                if (verifiedOffset !== total) throw new Error(`Incomplete download: received ${verifiedOffset} of ${total} bytes.`);

                const blob = new Blob(blobParts, {type: contentType});
                const objectUrl = URL.createObjectURL(blob);
                const anchor = document.createElement("a");
                anchor.href = objectUrl;
                anchor.download = name || "download";
                anchor.rel = "noreferrer";
                document.body.appendChild(anchor);
                anchor.click();
                anchor.remove();
                window.setTimeout(() => URL.revokeObjectURL(objectUrl), 5 * 60 * 1000);
                await removeResumableDownloadChunks(stagedDownloadId).catch((error) => {
                    console.warn("Could not clear completed public-download chunks:", error);
                });
                session.transferred = total;
                updateState({status: "completed", transferred: total, total, message: "Download complete · browser download started"});
                return;
            }
            const head = await fetch(downloadUrl, {method: "HEAD", signal: controller.signal, cache: "no-store"});
            if (!head.ok) throw new Error(`Could not prepare download (${head.status}).`);

            const sizeHeader = head.headers.get("content-length");
            const total = sizeHeader !== null ? Number(sizeHeader) : Number(publicFile?.size || 0);
            if (!Number.isFinite(total) || total < 0 || (sizeHeader === null && publicFile?.size == null)) {
                throw new Error("The server did not provide the file size needed to stage this download safely.");
            }
            const etag = head.headers.get("etag") || "";
            const sourceChanged = (Boolean(session.etag && etag) && session.etag !== etag)
                || (Number(session.total) > 0 && Number(session.total) !== total);
            if (sourceChanged && session.tempName) {
                root = await navigator.storage.getDirectory();
                await root.removeEntry(session.tempName).catch(() => {});
                session.tempName = "";
                session.transferred = 0;
            }
            session.total = total;
            session.etag = etag;
            totalForTask = total;
            const estimate = await navigator.storage.estimate().catch(() => ({}));
            if (total > 0 && Number.isFinite(estimate.quota) && Number.isFinite(estimate.usage) && estimate.quota - estimate.usage < total) {
                throw new Error("There is not enough browser temporary storage to stage this file. Free up disk space and try again.");
            }
            if (typeof navigator.storage.persist === "function") {
                await navigator.storage.persist().catch(() => false);
            }

            root = await navigator.storage.getDirectory();
            const reusingTemp = Boolean(session.tempName);
            tempName = session.tempName || `storelimitless-view-${createUniqueId()}.download`;
            session.tempName = tempName;
            const handle = await root.getFileHandle(tempName, {create: true});
            if (!reusingTemp) {
                const initialWriter = await handle.createWritable();
                await initialWriter.truncate(0);
                await initialWriter.close();
            }
            const existingFile = await handle.getFile();
            let offset = Math.min(existingFile.size, total);
            if (existingFile.size > offset) {
                const truncateWriter = await handle.createWritable({keepExistingData: true});
                await truncateWriter.truncate(offset);
                await truncateWriter.close();
            }
            offsetForTask = offset;
            session.transferred = offset;
            updateState({
                status: "downloading", transferred: offset, total,
                message: offset ? "Resuming from saved progress" : "Downloading to StoreLimitless temporary storage",
            });
            const chunkSize = 8 * 1024 * 1024;
            while (offset < total) {
                if (controller.signal.aborted) throw new DOMException("Download cancelled", "AbortError");
                const end = Math.min(offset + chunkSize, total) - 1;
                let chunk = null;
                let lastError = null;

                for (let attempt = 0; attempt < 5; attempt += 1) {
                    try {
                        // Range is CORS-safelisted for a single byte range. Avoid If-Range,
                        // which triggers a preflight before the API's cross-origin redirect.
                        const headers = {Range: `bytes=${offset}-${end}`};
                        const requestSignal = typeof AbortSignal.any === "function" && typeof AbortSignal.timeout === "function"
                            ? AbortSignal.any([controller.signal, AbortSignal.timeout(180000)])
                            : controller.signal;
                        const response = await fetch(downloadUrl, {headers, signal: requestSignal, cache: "no-store"});
                        if (response.status !== 206) {
                            await response.body?.cancel();
                            throw new Error(response.status === 200
                                ? "The server did not honor the byte-range request; refusing an unsafe staged download."
                                : `Download range failed (${response.status}).`);
                        }
                        const responseEtag = response.headers.get("etag") || "";
                        if (etag && responseEtag && responseEtag !== etag) {
                            await response.body?.cancel();
                            throw new Error("The file changed during download. Retry to fetch a consistent copy.");
                        }
                        const expectedRange = `bytes ${offset}-${end}/${total}`;
                        const receivedRange = response.headers.get("content-range") || "";
                        if (receivedRange !== expectedRange) throw new Error(`Unexpected Content-Range: ${receivedRange || "missing"}`);
                        const bytes = await response.arrayBuffer();
                        if (bytes.byteLength !== end - offset + 1) throw new Error(`Incomplete range: received ${bytes.byteLength} bytes.`);
                        chunk = bytes;
                        break;
                    } catch (error) {
                        if (controller.signal.aborted || error?.name === "AbortError") throw error;
                        lastError = error;
                        if (attempt < 4) {
                            await new Promise((resolve, reject) => {
                                let timeout;
                                const onAbort = () => {
                                    window.clearTimeout(timeout);
                                    reject(new DOMException("Download cancelled", "AbortError"));
                                };
                                timeout = window.setTimeout(() => {
                                    controller.signal.removeEventListener("abort", onAbort);
                                    resolve();
                                }, Math.min(1000 * (2 ** attempt), 8000));
                                controller.signal.addEventListener("abort", onAbort, {once: true});
                            });
                        }
                    }
                }

                if (!chunk) throw lastError || new Error("Download failed after repeated attempts.");
                const writer = await handle.createWritable({keepExistingData: true});
                await writer.seek(offset);
                await writer.write(chunk);
                await writer.close();
                offset += chunk.byteLength;
                offsetForTask = offset;
                session.transferred = offset;
                updateState({
                    status: "downloading", transferred: offset, total,
                    message: "Downloading to StoreLimitless temporary storage",
                });
            }

            const stagedFile = await handle.getFile();
            const objectUrl = URL.createObjectURL(stagedFile);
            const anchor = document.createElement("a");
            anchor.href = objectUrl;
            anchor.download = name || stagedFile.name || "download";
            anchor.rel = "noreferrer";
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            window.setTimeout(() => {
                URL.revokeObjectURL(objectUrl);
                root?.removeEntry(tempName).catch(() => {});
            }, 5 * 60 * 1000);
            updateState({
                status: "completed",
                transferred: total,
                total,
                message: "StoreLimitless download complete. Browser download started.",
            });
        } catch (error) {
            if (controller.signal.aborted || error?.name === "AbortError") {
                updateState({
                    status: "paused",
                    transferred: session.transferred || offsetForTask || 0,
                    total: session.total || totalForTask || 0,
                    message: "Paused · resume continues from saved progress",
                });
            } else {
                // Preserve verified progress so Retry can resume after transient errors.
                updateState({
                    status: "error",
                    transferred: session.transferred || offsetForTask || 0,
                    total: session.total || totalForTask || 0,
                    message: error?.message || "Download failed",
                });
            }
        } finally {
            if (downloadControllerRef.current === controller) downloadControllerRef.current = null;
        }
    }, [downloadUrl, name, publicFile?.size]);

    /** @type {[VideoSource | null, import("react").Dispatch<import("react").SetStateAction<VideoSource | null>>]} */
    const [source, setSource] = useState(null);
    const [status, setStatus] = useState("Preparing video…");
    const [error, setError] = useState("");
    const [copyStatus, setCopyStatus] = useState("");
    const copyTimer = useRef(null);

    // Backspace or Escape returns to the previous page/folder, unless the user is editing text.
    useEffect(() => {
        /**
         * Navigate back from the viewer unless a text-editing control has focus.
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        const handleKeyDown = (event) => {
            if (!["Backspace", "Escape"].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;

            const target = event.target;
            const tagName = target?.tagName?.toLowerCase();
            const isTextEntry = tagName === "input" || tagName === "textarea" || tagName === "select" || target?.isContentEditable;

            if (isTextEntry) return;

            event.preventDefault();
            navigate(-1);
        };

        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [navigate]);

    const copyStreamLink = useCallback(async () => {
        const streamUrl = source?.streamUrl;
        if (!streamUrl) return;

        const copyUsingFallback = () => {
            const textArea = document.createElement("textarea");
            textArea.value = streamUrl;
            textArea.setAttribute("readonly", "");
            textArea.style.position = "fixed";
            textArea.style.opacity = "0";
            textArea.style.pointerEvents = "none";
            document.body.appendChild(textArea);
            textArea.select();

            let copied = false;
            try {
                copied = document.execCommand("copy");
            } catch {
                copied = false;
            } finally {
                textArea.remove();
            }
            return copied;
        };

        let copied = false;
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(streamUrl);
                copied = true;
            } else {
                copied = copyUsingFallback();
            }
        } catch {
            // Clipboard API can be unavailable in insecure contexts or denied by browser permissions.
            copied = copyUsingFallback();
        }

        setCopyStatus(copied ? "Copied!" : "Copy failed");
        clearTimeout(copyTimer.current);
        copyTimer.current = window.setTimeout(() => setCopyStatus(""), 2500);
    }, [source?.streamUrl]);

    useEffect(() => () => clearTimeout(copyTimer.current), []);

    useEffect(() => {
        if (kind !== "video") return;

        let cancelled = false;
        setSource(null);
        setStatus("Preparing video…");
        setError("");

        if (publicToken) {
            setSource({streamUrl: `${API_URL}/public/stream/${encodeURIComponent(publicToken)}`});
            return () => {
                cancelled = true;
            };
        }

        const token = localStorage.getItem("token");
        if (!token) {
            navigate("/");
            return () => {
                cancelled = true;
            };
        }

        (async () => {
            try {
                const response = await fetch(`${API_URL}/auth/file/${fileId}/public-link`, {
                    method: "POST", headers: {Authorization: `Bearer ${token}`},
                });
                if (response.status === 401) {
                    localStorage.removeItem("token");
                    navigate("/");
                    return;
                }
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || "Could not open this file.");
                if (!data.public_token) throw new Error("The server did not return a public stream token.");
                if (!cancelled) setSource({streamUrl: `${API_URL}/public/stream/${data.public_token}`});
            } catch (err) {
                if (!cancelled) setError(err.message || "Could not open this file.");
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [fileId, kind, navigate, publicToken]);

    return (<div className="flex h-screen flex-col bg-[#171717] text-[#fafafa]"
                 style={{fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"}}>
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[#2e2e2e] px-5">
            <div
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[#3ecf8e]/20 bg-[#3ecf8e]/10 text-[#3ecf8e]">
                <span className="material-symbols-outlined text-[19px]"
                      aria-hidden="true">{kind === "pdf" ? "picture_as_pdf" : kind === "text" ? "code" : kind === "markdown" ? "article" : kind === "audio" ? "audio_file" : "movie"}</span>
            </div>
            <h1 className="min-w-0 truncate text-sm font-medium">{name}</h1>
            <div className="ml-auto flex shrink-0 items-center gap-2">
                <button
                    type="button"
                    onClick={() => void startStagedDownload()}
                    disabled={downloadPending || !downloadUrl || downloadState.status === "downloading"}
                    title={downloadError || (downloadPending ? "Preparing download" : "Download this file through StoreLimitless")}
                    className={`flex shrink-0 items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e] ${downloadUrl && !downloadPending && downloadState.status !== "downloading" ? "border-[#2e8058] bg-[#17643d] text-white hover:bg-[#1d794a]" : "cursor-not-allowed border-[#3a3a3a] bg-[#252525] text-[#808080]"}`}
                >
                    <span className={`material-symbols-outlined text-[17px]${downloadPending || downloadState.status === "downloading" ? " animate-spin" : ""}`} aria-hidden="true">{downloadPending || downloadState.status === "downloading" ? "progress_activity" : downloadState.status === "completed" ? "check_circle" : downloadState.status === "error" ? "error" : "download"}</span>
                    {downloadPending ? "Preparing…" : downloadState.status === "downloading" ? `Downloading ${downloadState.total > 0 ? Math.floor((downloadState.transferred / downloadState.total) * 100) : 0}%` : downloadState.status === "completed" ? "Download again" : downloadState.status === "error" ? "Retry download" : "Download"}
                </button>
                {source?.streamUrl && kind === "video" && (<button
                    type="button"
                    onClick={copyStreamLink}
                    title="Copy the raw stream URL to open in VLC"
                    aria-live="polite"
                    className="flex shrink-0 items-center gap-2 rounded-md border border-[#3a3a3a] px-3 py-1.5 text-xs text-[#fafafa] transition-colors hover:bg-[#2a2a2a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                >
                    <span className="material-symbols-outlined text-[17px]"
                          aria-hidden="true">{copyStatus === "Copied!" ? "check" : copyStatus === "Copy failed" ? "error" : "content_copy"}</span>
                    {copyStatus || "Copy stream URL"}
                </button>)}
            </div>
        </header>

        <main className="min-h-0 flex-1 overflow-hidden p-0">
            {fileId && !publicToken && privateFilePending ? (
                <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-[#898989]">
                    <span className="material-symbols-outlined animate-spin text-[32px]" aria-hidden="true">progress_activity</span>
                    Preparing file…
                </div>
            ) : fileId && !publicToken && privateFileError ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
                    <span className="material-symbols-outlined text-[40px] text-[#ff6369]" aria-hidden="true">error</span>
                    <p className="text-sm text-[#ff6369]">{privateFileError}</p>
                </div>
            ) : publicToken && !publicFile && !publicFileError ? (
                <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-[#898989]">
                    <span className="material-symbols-outlined animate-spin text-[32px]" aria-hidden="true">progress_activity</span>
                    Preparing shared file…
                </div>
            ) : publicToken && publicFileError ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
                    <span className="material-symbols-outlined text-[40px] text-[#ff6369]" aria-hidden="true">error</span>
                    <p className="text-sm text-[#ff6369]">{publicFileError}</p>
                </div>
            ) : kind === "text" || kind === "markdown" ? <FileContentPreview fileId={fileId} kind={kind} navigate={navigate} publicToken={publicToken}/> : kind === "pdf" ?
                <MediaPreview fileId={fileId} navigate={navigate} type="pdf" publicToken={publicToken}/> : kind === "audio" ?
                    <MediaPreview fileId={fileId} navigate={navigate} type="audio" publicToken={publicToken}/> : kind !== "video" ? (
                        <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-[#898989]">
                            <span className="material-symbols-outlined text-[40px] text-[#4d4d4d]" aria-hidden="true">visibility_off</span>
                            <p className="text-sm text-[#fafafa]">Preview isn't available for this file type yet.</p>
                        </div>) : error ? (<div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                        <span className="material-symbols-outlined text-[40px] text-[#ff6369]" aria-hidden="true">error</span>
                        <p className="text-sm text-[#ff6369]">{error}</p>
                    </div>) : !source ? (<div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-[#898989]">
                        <span className="material-symbols-outlined animate-spin text-[32px]" aria-hidden="true">progress_activity</span>
                        {status}
                    </div>) : (<div className="flex h-full w-full min-h-0 flex-col">
                        <div className="min-h-0 min-w-0 flex-1">
                            <VideoPlayer file={{name}} streamUrl={source.streamUrl} heightClass="h-full"/>
                        </div>
                    </div>)}
        </main>
        <UploadJobsTray
            uploadJobs={[]}
            downloadJobs={downloadTrayJobs}
            pauseDownload={(jobId) => {
                if (jobId === downloadTrayJobId) downloadControllerRef.current?.abort();
            }}
            resumeDownload={(jobId) => {
                if (jobId === downloadTrayJobId && ["paused", "error"].includes(downloadState.status)) void startStagedDownload();
            }}
            removeDownload={(jobId) => {
                if (jobId !== downloadTrayJobId) return;
                downloadDismissedRef.current = true;
                downloadControllerRef.current?.abort();
                setDownloadError("");
                setDownloadState({status: "idle", transferred: 0, total: 0, message: ""});
                const session = downloadSessionRef.current;
                if (session.tempName && typeof navigator.storage?.getDirectory === "function") {
                    navigator.storage.getDirectory().then((root) => root.removeEntry(session.tempName)).catch(() => {});
                }
                session.tempName = "";
                session.total = 0;
                session.etag = "";
                session.transferred = 0;
                removeResumableDownloadChunks(session.id).catch(() => {});
            }}
            clearCompletedDownloads={() => {
                if (!["completed", "error"].includes(downloadState.status) && !downloadError) return;
                downloadDismissedRef.current = true;
                downloadControllerRef.current?.abort();
                setDownloadError("");
                setDownloadState({status: "idle", transferred: 0, total: 0, message: ""});
                const session = downloadSessionRef.current;
                if (session.tempName && typeof navigator.storage?.getDirectory === "function") {
                    navigator.storage.getDirectory().then((root) => root.removeEntry(session.tempName)).catch(() => {});
                }
                session.tempName = "";
                session.total = 0;
                session.etag = "";
                session.transferred = 0;
                removeResumableDownloadChunks(session.id).catch(() => {});
            }}
        />
    </div>);
}
