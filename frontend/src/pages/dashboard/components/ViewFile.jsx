import {useCallback, useEffect, useRef, useState} from "react";
import {useNavigate, useParams, useSearchParams} from "react-router-dom";
import FileContentPreview, {MediaPreview} from "./FileContentPreview.jsx";

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


/** @type {string | undefined} */
const API_URL = window.STORELIMITLESS_CONFIG?.API_URL ?? import.meta.env.VITE_API_URL;
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

const ctrlBtn = "flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[#fafafa]/90 transition-colors " + "hover:bg-white/10 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]";

/**
 * Format a playback position as mm:ss or hh:mm:ss.
 * @param {number} seconds
 * @returns {string}
 */
const fmtTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60).toString().padStart(2, "0");
    return hours > 0 ? `${hours}:${minutes.toString().padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
};

/**
 * Render a custom HTML video player with seeking, volume, speed, fullscreen, and keyboard controls.
 * @param {VideoPlayerProps} props
 * @returns {import("react").ReactElement}
 */
function VideoPlayer({file, streamUrl, heightClass = "h-[70vh]"}) {
    const wrapRef = useRef(null);
    const videoRef = useRef(null);
    const hideTimer = useRef(null);
    const stallTimerRef = useRef(null);
    const menuRef = useRef(null);

    const [playing, setPlaying] = useState(false);
    const [buffering, setBuffering] = useState(true);
    const [current, setCurrent] = useState(0);
    const [duration, setDuration] = useState(0);
    const [buffered, setBuffered] = useState(0);
    const [volume, setVolume] = useState(1);
    const [muted, setMuted] = useState(false);
    const [rate, setRate] = useState(1);
    const [isFs, setIsFs] = useState(false);
    const [showUi, setShowUi] = useState(true);
    /** @type {["speed" | null, import("react").Dispatch<import("react").SetStateAction<"speed" | null>>]} */
    const [menu, setMenu] = useState(null);
    /** @type {[string | null, import("react").Dispatch<import("react").SetStateAction<string | null>>]} */
    const [error, setError] = useState(null);
    const [streamRetryNonce, setStreamRetryNonce] = useState(0);
    const retryCountRef = useRef(0);
    const resumeTimeRef = useRef(0);
    const retryCheckpointRef = useRef(0);

    menuRef.current = menu;

    // A media request can be interrupted by a transient tunnel/proxy reset.
    // Add a unique cache-busting query for each retry and resume at the last position.
    const playbackUrl = streamRetryNonce > 0 ? `${streamUrl}${streamUrl.includes("?") ? "&" : "?"}_sl_stream_retry=${streamRetryNonce}` : streamUrl;

    useEffect(() => {
        retryCountRef.current = 0;
        resumeTimeRef.current = 0;
        retryCheckpointRef.current = 0;
        if (stallTimerRef.current !== null) window.clearTimeout(stallTimerRef.current);
        stallTimerRef.current = null;
    }, [streamUrl]);

    useEffect(() => {
        const video = videoRef.current;
        if (!video) return;
        setError(null);
        setBuffering(true);
        video.load();
        video.play().catch(() => {
            // Some browsers require a user gesture before playback.
        });
        return () => {
            video.pause();
        };
    }, [playbackUrl]);

    const recoverStream = useCallback((video) => {
        if (!video) return;
        if (retryCountRef.current >= 3) {
            setBuffering(false);
            setError("The stream connection kept interrupting and could not recover after three retries. Reopen the stream to try again.");
            return;
        }

        resumeTimeRef.current = Number.isFinite(video.currentTime) ? video.currentTime : 0;
        retryCheckpointRef.current = resumeTimeRef.current;
        retryCountRef.current += 1;
        if (stallTimerRef.current !== null) window.clearTimeout(stallTimerRef.current);
        stallTimerRef.current = null;
        setError(null);
        setBuffering(true);
        setStreamRetryNonce((nonce) => nonce + 1);
    }, []);

    const handleMediaError = useCallback((event) => {
        const video = event.currentTarget;

        // MEDIA_ERR_NETWORK is recoverable; codec/decode errors usually are not.
        if (video.error?.code === 2) {
            recoverStream(video);
            return;
        }

        setError("This browser can't play this video format. Copy the stream link and open it in a compatible media player.");
    }, [recoverStream]);

    const togglePlay = useCallback(() => {
        const video = videoRef.current;
        if (!video) return;
        if (video.paused) video.play().catch(() => {
        }); else video.pause();
    }, []);

    const seekBy = useCallback((amount) => {
        const video = videoRef.current;
        if (!video) return;
        const max = Number.isFinite(video.duration) ? video.duration : Infinity;
        video.currentTime = Math.min(Math.max(video.currentTime + amount, 0), max);
    }, []);

    const changeVolume = useCallback((amount) => {
        const video = videoRef.current;
        if (!video) return;
        video.volume = Math.min(Math.max(video.volume + amount, 0), 1);
        if (video.volume > 0) video.muted = false;
    }, []);

    const toggleFs = useCallback(() => {
        if (document.fullscreenElement) document.exitFullscreen?.(); else wrapRef.current?.requestFullscreen?.();
    }, []);

    /**
     * Toggle browser picture-in-picture playback when supported.
     * @returns {void}
     */
    const togglePip = () => {
        const video = videoRef.current;
        if (!video) return;
        if (document.pictureInPictureElement) {
            document.exitPictureInPicture?.().catch(() => {
                // The browser may reject picture-in-picture if the state changes mid-request.
            });
        } else {
            video.requestPictureInPicture?.().catch(() => {
                // Picture-in-picture may be unavailable for the current media state.
            });
        }
    };

    /**
     * Apply a playback rate and close the speed menu.
     * @param {number} playbackRate
     * @returns {void}
     */
    const changeRate = (playbackRate) => {
        if (videoRef.current) videoRef.current.playbackRate = playbackRate;
        setMenu(null);
    };

    const poke = useCallback(() => {
        setShowUi(true);
        clearTimeout(hideTimer.current);
        hideTimer.current = setTimeout(() => setShowUi(false), 2800);
    }, []);

    useEffect(() => () => {
        clearTimeout(hideTimer.current);
        if (stallTimerRef.current !== null) window.clearTimeout(stallTimerRef.current);
    }, []);

    useEffect(() => {
        const onFullscreenChange = () => setIsFs(document.fullscreenElement === wrapRef.current);
        document.addEventListener("fullscreenchange", onFullscreenChange);
        return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
    }, []);

    useEffect(() => {
        /**
         * Handle video-player keyboard shortcuts.
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        const onKey = (event) => {
            const tag = event.target?.tagName;
            if ((tag === "INPUT" && event.target.type !== "range") || tag === "TEXTAREA") return;
            if (event.key === "Escape" && menuRef.current) {
                event.stopImmediatePropagation();
                setMenu(null);
                return;
            }

            const step = event.shiftKey ? 10 : 5;
            switch (event.key.toLowerCase()) {
                case " ":
                case "k":
                    togglePlay();
                    break;
                case "arrowleft":
                    seekBy(-step);
                    break;
                case "arrowright":
                    seekBy(step);
                    break;
                case "j":
                    seekBy(-10);
                    break;
                case "l":
                    seekBy(10);
                    break;
                case "arrowup":
                    changeVolume(0.05);
                    break;
                case "arrowdown":
                    changeVolume(-0.05);
                    break;
                case "m":
                    if (videoRef.current) videoRef.current.muted = !videoRef.current.muted;
                    break;
                case "f":
                    toggleFs();
                    break;
                default:
                    return;
            }
            event.preventDefault();
            poke();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [togglePlay, seekBy, changeVolume, toggleFs, poke]);

    /**
     * Update the visible buffered endpoint for the current playback position.
     * @returns {void}
     */
    const updateBuffered = () => {
        const video = videoRef.current;
        if (!video) return;
        const ranges = video.buffered;
        for (let i = 0; i < ranges.length; i++) {
            if (ranges.start(i) <= video.currentTime && video.currentTime <= ranges.end(i)) {
                setBuffered(ranges.end(i));
                return;
            }
        }
        setBuffered(0);
    };

    const controlsVisible = showUi || !playing || menu !== null;
    /**
     * Convert a time value to a clamped timeline percentage.
     * @param {number} value
     * @returns {number}
     */
    const percent = (value) => duration ? Math.min((value / duration) * 100, 100) : 0;

    return (<div
        ref={wrapRef}
        onMouseMove={poke}
        onMouseLeave={() => playing && setShowUi(false)}
        className={`relative ${heightClass} w-full select-none overflow-hidden bg-black ${controlsVisible ? "" : "cursor-none"}`}
    >
        <video
            ref={videoRef}
            src={playbackUrl}
            autoPlay
            playsInline
            className="h-full w-full object-contain"
            onClick={() => {
                setMenu(null);
                togglePlay();
            }}
            onDoubleClick={toggleFs}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onPlaying={() => {
                setPlaying(true);
                setBuffering(false);
                if (stallTimerRef.current !== null) window.clearTimeout(stallTimerRef.current);
                stallTimerRef.current = null;
            }}
            onCanPlay={() => {
                setBuffering(false);
                if (stallTimerRef.current !== null) window.clearTimeout(stallTimerRef.current);
                stallTimerRef.current = null;
            }}
            onLoadedMetadata={(event) => {
                const video = event.currentTarget;
                const resumeAt = resumeTimeRef.current;
                if (resumeAt > 0 && Number.isFinite(video.duration)) {
                    video.currentTime = Math.min(resumeAt, Math.max(0, video.duration - 0.25));
                    resumeTimeRef.current = 0;
                }
            }}
            onWaiting={(event) => {
                setBuffering(true);
                const video = event.currentTarget;
                if (video.paused || stallTimerRef.current !== null) return;
                // Recover from a silent stall too; browsers do not always emit `error`
                // when a long-lived proxy/tunnel stream stops delivering bytes.
                stallTimerRef.current = window.setTimeout(() => {
                    stallTimerRef.current = null;
                    const currentVideo = videoRef.current;
                    if (currentVideo && !currentVideo.paused && currentVideo.readyState < 3) recoverStream(currentVideo);
                }, 20000);
            }}
            onTimeUpdate={(event) => {
                const video = event.currentTarget;
                setCurrent(video.currentTime);
                if (retryCountRef.current > 0 && video.currentTime >= retryCheckpointRef.current + 10) {
                    retryCountRef.current = 0;
                }
                updateBuffered();
            }}
            onProgress={updateBuffered}
            onDurationChange={(event) => setDuration(event.currentTarget.duration)}
            onVolumeChange={(event) => {
                setVolume(event.currentTarget.volume);
                setMuted(event.currentTarget.muted || event.currentTarget.volume === 0);
            }}
            onRateChange={(event) => setRate(event.currentTarget.playbackRate)}
            onError={handleMediaError}
        />

        {buffering && !error && (<div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <span className="material-symbols-outlined animate-spin text-[40px] text-[#3ecf8e]" aria-label="Loading">
                        progress_activity
                    </span>
        </div>)}

        {error && (<div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/80 px-8 text-center">
            <span className="material-symbols-outlined text-[32px] text-[#f87171]" aria-hidden="true">error</span>
            <p className="max-w-md text-sm text-[#fafafa]">{error}</p>
        </div>)}

        {isFs && (<div
            className={`pointer-events-none absolute inset-x-0 top-0 bg-gradient-to-b from-black/80 to-transparent px-5 pb-8 pt-4 transition-opacity duration-200 ${controlsVisible ? "opacity-100" : "opacity-0"}`}>
            <p className="truncate text-sm font-medium text-[#fafafa]">{file.name}</p>
        </div>)}

        {menu === "speed" && (<div
            className="absolute bottom-16 right-3 max-h-64 w-52 overflow-auto rounded-md border border-[#2e2e2e] bg-[#1c1c1c]/95 p-1.5 shadow-xl backdrop-blur"
            onClick={(event) => event.stopPropagation()}>
            <p className="px-2 pb-1 pt-2 text-[11px] text-[#8b8b8b]">Playback speed</p>
            {SPEEDS.map((speed) => (<button
                key={speed}
                type="button"
                onClick={() => changeRate(speed)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#fafafa] hover:bg-white/10"
            >
                <span className="inline-block w-4 text-[#3ecf8e]">{speed === rate ? "✓" : ""}</span>
                <span className="flex-1">{speed === 1 ? "Normal" : `${speed}×`}</span>
            </button>))}
        </div>)}

        <div
            className={`absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/60 to-transparent px-3 pb-2 pt-10 transition-opacity duration-200 ${controlsVisible ? "opacity-100" : "pointer-events-none opacity-0"}`}
            onClick={(event) => event.stopPropagation()}
        >
            <div className="group relative mb-1 h-4">
                <div
                    className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 overflow-hidden rounded-full bg-white/20 transition-[height] group-hover:h-1.5">
                    <div className="absolute inset-y-0 left-0 bg-white/30" style={{width: `${percent(buffered)}%`}}/>
                    <div className="absolute inset-y-0 left-0 bg-[#3ecf8e]" style={{width: `${percent(current)}%`}}/>
                </div>
                <div
                    className="pointer-events-none absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#3ecf8e] opacity-0 transition-opacity group-hover:opacity-100"
                    style={{left: `${percent(current)}%`}}/>
                <input
                    type="range"
                    min={0}
                    max={duration || 0}
                    step={0.1}
                    value={Math.min(current, duration || 0)}
                    aria-label="Seek"
                    onChange={(event) => {
                        const time = Number(event.target.value);
                        if (videoRef.current) videoRef.current.currentTime = time;
                        setCurrent(time);
                    }}
                    className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                />
            </div>

            <div className="flex items-center gap-1">
                <button type="button" className={ctrlBtn} onClick={togglePlay} aria-label={playing ? "Pause" : "Play"}>
                    <span className="material-symbols-outlined text-[24px]">{playing ? "pause" : "play_arrow"}</span>
                </button>
                <button type="button" className={ctrlBtn} onClick={() => seekBy(-10)} aria-label="Back 10 seconds">
                    <span className="material-symbols-outlined text-[20px]">replay_10</span>
                </button>
                <button type="button" className={ctrlBtn} onClick={() => seekBy(10)} aria-label="Forward 10 seconds">
                    <span className="material-symbols-outlined text-[20px]">forward_10</span>
                </button>

                <div className="ml-1 flex items-center gap-1">
                    <button
                        type="button"
                        className={ctrlBtn}
                        aria-label={muted ? "Unmute" : "Mute"}
                        onClick={() => videoRef.current && (videoRef.current.muted = !videoRef.current.muted)}
                    >
                            <span
                                className="material-symbols-outlined text-[20px]">{muted ? "volume_off" : volume < 0.5 ? "volume_down" : "volume_up"}</span>
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
                        className="h-1 w-20 cursor-pointer accent-[#3ecf8e]"
                    />
                </div>

                <span className="ml-2 text-xs tabular-nums text-[#fafafa]/80">{fmtTime(current)} / {fmtTime(duration)}</span>

                <div className="ml-auto flex items-center gap-1">
                    <button
                        type="button"
                        className={`${ctrlBtn} w-auto px-2 text-xs tabular-nums ${menu === "speed" ? "bg-white/10" : ""}`}
                        aria-label="Playback speed"
                        aria-expanded={menu === "speed"}
                        onClick={() => setMenu(menu === "speed" ? null : "speed")}
                    >
                        {rate}×
                    </button>
                    {document.pictureInPictureEnabled && (
                        <button type="button" className={ctrlBtn} onClick={togglePip} aria-label="Picture in picture">
                            <span className="material-symbols-outlined text-[20px]">picture_in_picture_alt</span>
                        </button>)}
                    <button type="button" className={ctrlBtn} onClick={toggleFs} aria-label={isFs ? "Exit full screen" : "Full screen"}>
                        <span className="material-symbols-outlined text-[20px]">{isFs ? "fullscreen_exit" : "fullscreen"}</span>
                    </button>
                </div>
            </div>
        </div>
    </div>);
}

/**
 * Resolve a signed stream URL and render the selected preview.
 * @returns {import("react").ReactElement}
 */
export default function ViewFile() {
    const {fileId} = useParams();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const name = searchParams.get("name") || "Untitled";
    const kind = searchParams.get("kind");

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
        const token = localStorage.getItem("token");
        if (!token) {
            navigate("/");
            return;
        }

        let cancelled = false;
        setSource(null);
        setStatus("Preparing video…");
        setError("");

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
    }, [fileId, kind, navigate]);

    return (<div className="flex h-screen flex-col bg-[#171717] text-[#fafafa]"
                 style={{fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"}}>
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[#2e2e2e] px-5">
            <div
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[#3ecf8e]/20 bg-[#3ecf8e]/10 text-[#3ecf8e]">
                <span className="material-symbols-outlined text-[19px]"
                      aria-hidden="true">{kind === "pdf" ? "picture_as_pdf" : kind === "text" ? "code" : kind === "markdown" ? "article" : kind === "audio" ? "audio_file" : "movie"}</span>
            </div>
            <h1 className="min-w-0 truncate text-sm font-medium">{name}</h1>
            {source?.streamUrl && kind === "video" && (<button
                type="button"
                onClick={copyStreamLink}
                title="Copy the stream URL to your clipboard"
                aria-live="polite"
                className="ml-auto flex shrink-0 items-center gap-2 rounded-md border border-[#3a3a3a] px-3 py-1.5 text-xs text-[#fafafa] transition-colors hover:bg-[#2a2a2a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
            >
                <span className="material-symbols-outlined text-[17px]"
                      aria-hidden="true">{copyStatus === "Copied!" ? "check" : copyStatus === "Copy failed" ? "error" : "content_copy"}</span>
                {copyStatus || "Copy stream link"}
            </button>)}
        </header>

        <main className="min-h-0 flex-1 overflow-hidden p-0">
            {kind === "text" || kind === "markdown" ? <FileContentPreview fileId={fileId} kind={kind} navigate={navigate}/> : kind === "pdf" ?
                <MediaPreview fileId={fileId} navigate={navigate} type="pdf"/> : kind === "audio" ?
                    <MediaPreview fileId={fileId} navigate={navigate} type="audio"/> : kind !== "video" ? (
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
    </div>);
}
