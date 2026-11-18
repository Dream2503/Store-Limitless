import {useCallback, useEffect, useRef, useState} from "react";
import Hls from "hls.js";
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
 * @property {string} publicToken
 * @property {string} [heightClass]
 */
/** @typedef {{streamUrl: string, publicToken: string}} VideoSource */


const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

const TRACK_LANGUAGES = [
    ["English", /\b(?:english|eng)\b/i],
    ["Hindi", /\b(?:hindi|hin)\b/i],
    ["German", /\b(?:german|deu|ger)\b/i],
    ["French", /\b(?:french|fra|fre)\b/i],
    ["Portuguese", /\b(?:portuguese|por)\b/i],
    ["Spanish", /\b(?:spanish|spa)\b/i],
    ["Korean", /\b(?:korean|kor)\b/i],
    ["Chinese", /\b(?:chinese|zho|chi|cmn)\b/i],
    ["Polish", /\b(?:polish|pol)\b/i],
    ["Russian", /\b(?:russian|rus)\b/i],
    ["Japanese", /\b(?:japanese|jpn)\b/i],
    ["Italian", /\b(?:italian|ita)\b/i],
    ["Arabic", /\b(?:arabic|ara)\b/i],
    ["Tamil", /\b(?:tamil|tam)\b/i],
    ["Telugu", /\b(?:telugu|tel)\b/i],
    ["Bengali", /\b(?:bengali|ben)\b/i],
    ["Turkish", /\b(?:turkish|tur)\b/i],
    ["Dutch", /\b(?:dutch|nld|dut)\b/i],
    ["Swedish", /\b(?:swedish|swe)\b/i],
    ["Ukrainian", /\b(?:ukrainian|ukr)\b/i],
];

/** Show only a recognized spoken language, not release-group and codec metadata. */
function getTrackLanguage(track, kind = "Audio") {
    const candidates = [track?.language, track?.label].filter(Boolean).join(" ");
    for (const [language, pattern] of TRACK_LANGUAGES) {
        if (pattern.test(candidates)) return language;
    }
    return kind + " " + (Number(track?.index) >= 0 ? Number(track.index) + 1 : 1);
}

/** Convert SRT or WebVTT cue timestamps into seconds. */
function subtitleTimeToSeconds(value) {
    const parts = String(value || "").trim().replace(",", ".").split(":");
    if (parts.length < 2 || parts.length > 3) return NaN;
    const seconds = Number(parts.pop());
    const minutes = Number(parts.pop());
    const hours = parts.length ? Number(parts.pop()) : 0;
    if (![hours, minutes, seconds].every(Number.isFinite)) return NaN;
    return hours * 3600 + minutes * 60 + seconds;
}

/** Parse complete or progressively received WebVTT/SRT subtitle cues. */
function parseSubtitleText(source) {
    const lines = String(source || "").replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").split("\n");
    const cues = [];

    for (let index = 0; index < lines.length; index += 1) {
        if (!lines[index].includes("-->")) continue;
        const timing = lines[index].split("-->");
        if (timing.length !== 2) continue;

        const start = subtitleTimeToSeconds(timing[0]);
        const end = subtitleTimeToSeconds(timing[1].trim().split(/\s+/)[0]);
        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;

        const bodyLines = [];
        for (let bodyIndex = index + 1; bodyIndex < lines.length; bodyIndex += 1) {
            if (lines[bodyIndex].includes("-->") || (!lines[bodyIndex].trim() && bodyLines.length)) break;
            if (lines[bodyIndex].trim() || bodyLines.length) bodyLines.push(lines[bodyIndex]);
        }

        const body = bodyLines.join("\n")
            .replace(/\{\\[^}]+\}/g, "")
            .replace(/<[^>]*>/g, "")
            .replace(/\\[Nn]/g, "\n")
            .replace(/&amp;/gi, "&")
            .replace(/&lt;/gi, "<")
            .replace(/&gt;/gi, ">")
            .replace(/&quot;/gi, '"')
            .replace(/&#39;|&apos;/gi, "'")
            .replace(/&nbsp;/gi, "\u00a0")
            .trim();

        if (body) cues.push({start, end, text: body});
    }

    return cues.sort((left, right) => left.start - right.start);
}

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
function VideoPlayer({file, streamUrl, publicToken, heightClass = "h-[70vh]"}) {
    const wrapRef = useRef(null);
    const videoRef = useRef(null);
    const hlsRef = useRef(null);
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
    const [audioTracks, setAudioTracks] = useState([]);
    const [selectedAudioTrack, setSelectedAudioTrack] = useState(-1);
    const [subtitleTracks, setSubtitleTracks] = useState([]);
    const [selectedSubtitleTrack, setSelectedSubtitleTrack] = useState(-1);
    const [subtitleCues, setSubtitleCues] = useState([]);
    const [subtitleLoadError, setSubtitleLoadError] = useState("");
    const [isFs, setIsFs] = useState(false);
    const [showUi, setShowUi] = useState(true);
    /** @type {["speed" | "audio" | "subtitles" | null, import("react").Dispatch<import("react").SetStateAction<"speed" | "audio" | "subtitles" | null>>]} */
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
    const hlsUrl = publicToken ? API_URL + "/public/hls/" + encodeURIComponent(publicToken) + "/master.m3u8" : "";
    const tracksUrl = publicToken ? API_URL + "/public/tracks/" + encodeURIComponent(publicToken) : "";
    // Embedded subtitles are requested in bounded windows, not by scanning the whole
    // media file into one response. This lets long videos produce useful captions promptly.
    const subtitleWindowStart = Math.floor(Math.max(0, current) / 120) * 120;

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

        let cancelled = false;
        let hlsInstance = null;

        setError(null);
        setBuffering(true);
        setAudioTracks([]);
        setSelectedAudioTrack(-1);
        setSubtitleTracks([]);
        setSelectedSubtitleTrack(-1);
        video.removeAttribute("src");
        video.load();

        const useOriginalStream = () => {
            if (cancelled) return;
            video.src = playbackUrl;
            video.load();
            video.play().catch(() => {
                // Some browsers require a user gesture before playback.
            });
        };

        const setupPlayback = async () => {
            if (!publicToken) {
                useOriginalStream();
                return;
            }

            try {
                const response = await fetch(tracksUrl, {cache: "no-store"});
                if (!response.ok) throw new Error("Track metadata is unavailable.");
                const trackInfo = await response.json();
                if (cancelled) return;

                setSubtitleTracks((trackInfo.subtitles || []).filter((track) => track.selectable));
                setAudioTracks((trackInfo.audio || []).map((track, index) => ({
                    index,
                    label: track.label || track.language || ("Audio " + (index + 1)),
                    language: track.language || "",
                    default: Boolean(track.default),
                })));

                if (Hls.isSupported()) {
                    hlsInstance = new Hls({
                        enableWorker: true,
                        maxBufferLength: 30,
                        backBufferLength: 30,
                        // StoreLimitless generates each HLS segment on demand using FFmpeg.
                        // The segment endpoint buffers the encoded segment before responding,
                        // so on a cold remote-storage cache the first byte can take >10 seconds.
                        fragLoadPolicy: {
                            default: {
                                maxTimeToFirstByteMs: 90000,
                                maxLoadTimeMs: 120000,
                                timeoutRetry: {
                                    maxNumRetry: 4,
                                    retryDelayMs: 1000,
                                    maxRetryDelayMs: 8000,
                                    backoff: "linear",
                                },
                                errorRetry: {
                                    maxNumRetry: 5,
                                    retryDelayMs: 1000,
                                    maxRetryDelayMs: 8000,
                                    backoff: "exponential",
                                },
                            },
                        },
                    });
                    hlsRef.current = hlsInstance;

                    const syncAudioTracks = () => {
                        if (cancelled || !hlsInstance) return;
                        const available = hlsInstance.audioTracks.map((track, index) => ({
                            index,
                            label: track.name || track.lang || ("Audio " + (index + 1)),
                            language: track.lang || "",
                            default: Boolean(track.default),
                        }));
                        setAudioTracks(available);

                        if (available.length) {
                            const currentIndex = hlsInstance.audioTrack;
                            const defaultIndex = available.findIndex((track) => track.default);
                            const selectedIndex = currentIndex >= 0
                                ? currentIndex
                                : defaultIndex >= 0 ? defaultIndex : 0;
                            hlsInstance.audioTrack = selectedIndex;
                            setSelectedAudioTrack(selectedIndex);
                        }
                    };

                    hlsInstance.on(Hls.Events.MEDIA_ATTACHED, () => {
                        if (!cancelled) hlsInstance.loadSource(hlsUrl);
                    });
                    hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
                        syncAudioTracks();
                        setBuffering(false);
                        video.play().catch(() => {
                            // Some browsers require a user gesture before playback.
                        });
                    });
                    hlsInstance.on(Hls.Events.AUDIO_TRACKS_UPDATED, syncAudioTracks);
                    hlsInstance.on(Hls.Events.AUDIO_TRACK_SWITCHED, (_event, data) => {
                        setSelectedAudioTrack(data.id);
                    });
                    hlsInstance.on(Hls.Events.ERROR, (_event, data) => {
                        if (!data.fatal) return;

                        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && retryCountRef.current < 3) {
                            retryCountRef.current += 1;
                            hlsInstance.startLoad();
                        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                            hlsInstance.recoverMediaError();
                        } else {
                            setBuffering(false);
                            setError("Track-aware streaming could not load a video or audio segment. Reopen the stream to try again.");
                        }
                    });
                    hlsInstance.attachMedia(video);
                    return;
                }

                if (video.canPlayType("application/vnd.apple.mpegurl")) {
                    video.src = hlsUrl;
                    video.load();
                    video.play().catch(() => {
                        // Some browsers require a user gesture before playback.
                    });
                    return;
                }

                useOriginalStream();
            } catch {
                // Keep existing direct streaming working if track-aware playback is unavailable.
                useOriginalStream();
            }
        };

        void setupPlayback();

        return () => {
            cancelled = true;
            if (hlsInstance) hlsInstance.destroy();
            if (hlsRef.current === hlsInstance) hlsRef.current = null;
            video.pause();
            video.removeAttribute("src");
            video.load();
        };
    }, [hlsUrl, playbackUrl, publicToken, tracksUrl]);

    useEffect(() => {
        let cancelled = false;
        const controller = new AbortController();
        setSubtitleCues([]);
        setSubtitleLoadError("");

        if (!publicToken || selectedSubtitleTrack < 0) return () => controller.abort();

        const subtitleUrl = API_URL + "/public/hls/" + encodeURIComponent(publicToken)
            + "/subtitles/" + selectedSubtitleTrack + ".srt?start_seconds=" + subtitleWindowStart
            + "&duration_seconds=120";

        (async () => {
            let reader = null;
            try {
                const response = await fetch(subtitleUrl, {
                    cache: "no-store",
                    signal: controller.signal,
                });
                if (!response.ok) {
                    let detail = "";
                    try {
                        const payload = await response.json();
                        detail = payload?.detail ? " " + payload.detail : "";
                    } catch {
                        // The response body may not be JSON.
                    }
                    throw new Error("Subtitle request failed (HTTP " + response.status + ")." + detail);
                }

                if (!response.body) {
                    const text = await response.text();
                    if (!text.trimStart().startsWith("WEBVTT")) {
                        throw new Error("The server response is not valid WebVTT subtitle data.");
                    }
                    if (!cancelled) setSubtitleCues(parseSubtitleText(text));
                    return;
                }

                reader = response.body.getReader();
                const decoder = new TextDecoder();
                let subtitleText = "";

                while (true) {
                    const {value, done} = await reader.read();
                    if (done) {
                        subtitleText += decoder.decode();
                        break;
                    }

                    subtitleText += decoder.decode(value, {stream: true});
                    if (cancelled) return;

                    const trimmed = subtitleText.trimStart();
                    if (trimmed && !trimmed.startsWith("WEBVTT")) {
                        throw new Error("The server response is not valid WebVTT subtitle data.");
                    }

                    // Parse received bytes immediately so captions can appear while
                    // FFmpeg continues scanning the source video for later cues.
                    if (trimmed.startsWith("WEBVTT")) {
                        setSubtitleCues(parseSubtitleText(subtitleText));
                    }
                }

                if (cancelled) return;
                const trimmed = subtitleText.trimStart();
                if (!trimmed.startsWith("WEBVTT")) {
                    throw new Error("The server returned an empty or invalid WebVTT subtitle stream.");
                }

                // An interval without dialogue is valid. Later playback windows will
                // request their own cues as the timeline advances.
                setSubtitleCues(parseSubtitleText(subtitleText));
            } catch (loadError) {
                if (!cancelled && loadError?.name !== "AbortError") {
                    setSubtitleLoadError(loadError instanceof Error ? loadError.message : "Could not load this subtitle track.");
                }
            } finally {
                if (reader) {
                    try { reader.releaseLock(); } catch { /* The stream may already be closed. */ }
                }
            }
        })();

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [publicToken, selectedSubtitleTrack, subtitleWindowStart]);



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
        if (hlsRef.current) return;

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

    const selectAudioTrack = (index) => {
        if (hlsRef.current) {
            hlsRef.current.audioTrack = index;
        } else if (videoRef.current?.audioTracks && videoRef.current.audioTracks[index]) {
            for (let trackIndex = 0; trackIndex < videoRef.current.audioTracks.length; trackIndex += 1) {
                videoRef.current.audioTracks[trackIndex].enabled = trackIndex === index;
            }
        }
        setSelectedAudioTrack(index);
        setMenu(null);
    };

    const selectSubtitleTrack = (index) => {
        setSelectedSubtitleTrack(index);
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

    const activeSubtitleText = subtitleCues
        .filter((cue) => current >= cue.start && current < cue.end)
        .map((cue) => cue.text)
        .join("\n");
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
            crossOrigin="anonymous"
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
        {activeSubtitleText && (<div
            aria-live="off"
            className="pointer-events-none absolute inset-x-4 bottom-[5.5rem] z-10 flex justify-center px-4 text-center"
        >
            <span className="max-w-[90%] whitespace-pre-line rounded bg-black/75 px-3 py-1.5 text-base font-semibold leading-relaxed text-white [text-shadow:0_1px_2px_#000,1px_0_2px_#000,-1px_0_2px_#000] md:text-xl">
                {activeSubtitleText}
            </span>
        </div>)}
        {subtitleLoadError && selectedSubtitleTrack >= 0 && (<div className="pointer-events-none absolute inset-x-4 bottom-20 z-10 flex justify-center">
            <span className="rounded bg-black/80 px-2 py-1 text-xs text-red-300">{subtitleLoadError}</span>
        </div>)}

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

        {menu === "audio" && (<div
            className="absolute bottom-16 right-3 z-20 max-h-64 w-56 overflow-auto rounded-md border border-[#2e2e2e] bg-[#1c1c1c]/95 p-1.5 shadow-xl backdrop-blur"
            onClick={(event) => event.stopPropagation()}>
            <p className="px-2 pb-1 pt-2 text-[11px] text-[#8b8b8b]">Audio track</p>
            {audioTracks.map((track) => (<button
                key={"audio-" + track.index}
                type="button"
                onClick={() => selectAudioTrack(track.index)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#fafafa] hover:bg-white/10"
            >
                <span className="inline-block w-4 text-[#3ecf8e]">{selectedAudioTrack === track.index ? "✓" : ""}</span>
                <span className="flex-1 truncate">{getTrackLanguage(track, "Audio")}</span>
            </button>))}
        </div>)}

        {menu === "subtitles" && (<div
            className="absolute bottom-16 right-3 z-20 max-h-64 w-56 overflow-auto rounded-md border border-[#2e2e2e] bg-[#1c1c1c]/95 p-1.5 shadow-xl backdrop-blur"
            onClick={(event) => event.stopPropagation()}>
            <p className="px-2 pb-1 pt-2 text-[11px] text-[#8b8b8b]">Subtitles</p>
            <button
                type="button"
                onClick={() => selectSubtitleTrack(-1)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#fafafa] hover:bg-white/10"
            >
                <span className="inline-block w-4 text-[#3ecf8e]">{selectedSubtitleTrack === -1 ? "✓" : ""}</span>
                <span className="flex-1">Off</span>
            </button>
            {subtitleTracks.map((track) => (<button
                key={"subtitle-" + track.index}
                type="button"
                onClick={() => selectSubtitleTrack(track.index)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#fafafa] hover:bg-white/10"
            >
                <span className="inline-block w-4 text-[#3ecf8e]">{selectedSubtitleTrack === track.index ? "✓" : ""}</span>
                <span className="flex-1 truncate">{getTrackLanguage(track, "Subtitle")}</span>
            </button>))}
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
                    {audioTracks.length > 1 && (<button
                        type="button"
                        className={"flex h-8 shrink-0 items-center justify-center rounded-md px-2 text-xs text-[#fafafa]/90 transition-colors hover:bg-white/10 " + (menu === "audio" ? "bg-white/10" : "")}
                        aria-label="Audio tracks"
                        aria-expanded={menu === "audio"}
                        onClick={() => setMenu(menu === "audio" ? null : "audio")}
                    >
                        <span className="material-symbols-outlined mr-1 text-[17px]" aria-hidden="true">audio_file</span>
                        Audio
                    </button>)}
                    {subtitleTracks.length > 0 && (<button
                        type="button"
                        className={"flex h-8 shrink-0 items-center justify-center rounded-md px-2 text-xs text-[#fafafa]/90 transition-colors hover:bg-white/10 " + (menu === "subtitles" ? "bg-white/10" : "")}
                        aria-label="Subtitle tracks"
                        aria-expanded={menu === "subtitles"}
                        onClick={() => setMenu(menu === "subtitles" ? null : "subtitles")}
                    >
                        <span className="material-symbols-outlined mr-1 text-[17px]" aria-hidden="true">closed_caption</span>
                        Subs
                    </button>)}
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
        const streamUrl = publicToken ? window.location.href : source?.streamUrl;
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
    }, [source?.streamUrl, publicToken]);

    useEffect(() => () => clearTimeout(copyTimer.current), []);

    useEffect(() => {
        if (kind !== "video") return;

        let cancelled = false;
        setSource(null);
        setStatus("Preparing video…");
        setError("");

        if (publicToken) {
            setSource({streamUrl: `${API_URL}/public/stream/${encodeURIComponent(publicToken)}`, publicToken});
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
                if (!cancelled) setSource({streamUrl: `${API_URL}/public/stream/${data.public_token}`, publicToken: data.public_token});
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
                    title={publicToken ? "Copy the public view link to your clipboard" : "Copy the stream URL to your clipboard"}
                    aria-live="polite"
                    className="flex shrink-0 items-center gap-2 rounded-md border border-[#3a3a3a] px-3 py-1.5 text-xs text-[#fafafa] transition-colors hover:bg-[#2a2a2a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                >
                    <span className="material-symbols-outlined text-[17px]"
                          aria-hidden="true">{copyStatus === "Copied!" ? "check" : copyStatus === "Copy failed" ? "error" : "content_copy"}</span>
                    {copyStatus || (publicToken ? "Copy share link" : "Copy stream link")}
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
                            <VideoPlayer file={{name}} streamUrl={source.streamUrl} publicToken={source.publicToken} heightClass="h-full"/>
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
