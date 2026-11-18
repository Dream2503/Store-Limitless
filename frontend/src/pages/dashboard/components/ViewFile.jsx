import {useCallback, useEffect, useRef, useState} from "react";
import {useLocation, useNavigate} from "react-router-dom";
import FileContentPreview, {MediaPreview} from "./FileContentPreview.jsx";
import UploadJobsTray from "./UploadJobsTray.jsx";
import {AUTH_SESSION_END_EVENT, createUniqueId, endAuthSession, getFileKind, getToken} from "../utils.js";
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
const PLAYER_STYLES = ".ytp-player{--accent:#3ecf8e;position:relative;isolation:isolate;display:block;width:100%;max-height:100%;aspect-ratio:16/9;overflow:hidden;border-radius:12px;background:#000;color:#fff;font-family:Roboto,Arial,system-ui,sans-serif;user-select:none;outline:none;box-shadow:0 12px 38px #0003;margin:auto}.ytp-player:focus-visible{outline:2px solid #fff;outline-offset:3px}.ytp-player:fullscreen,.ytp-player:-webkit-full-screen{width:100vw;height:100vh;max-height:none;max-width:none;aspect-ratio:auto;border-radius:0;margin:0}.ytp-video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000;cursor:pointer}.ytp-controls{position:absolute;z-index:20;inset:auto 0 0;padding:24px 12px 8px;background:linear-gradient(0deg,rgba(0,0,0,.94),rgba(0,0,0,.66) 42%,transparent);opacity:1;transition:opacity .22s ease;pointer-events:auto}.ytp-controls-hidden .ytp-controls{opacity:0;pointer-events:none}.ytp-controls-hidden,.ytp-controls-hidden .ytp-video{cursor:none}.ytp-controls-row{display:flex;align-items:center;gap:8px;min-width:0}.ytp-button{width:34px;height:34px;flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:4px;color:#fff;background:transparent;cursor:pointer;transition:background .16s ease;outline:none;padding:0}.ytp-button:hover{background:#3ecf8e1a}.ytp-button:focus-visible,.ytp-speed:focus-visible,.ytp-quality:focus-visible{outline:2px solid #3ecf8e;outline-offset:2px}.ytp-button:disabled{opacity:.35;cursor:not-allowed}.ytp-volume{display:flex;align-items:center;gap:2px;flex:0 0 auto}.ytp-volume-slider{width:0;max-width:0;opacity:0;overflow:hidden;accent-color:#fff;transition:width .18s ease,max-width .18s ease,opacity .18s ease;cursor:pointer;height:3px}.ytp-volume:hover .ytp-volume-slider,.ytp-volume:focus-within .ytp-volume-slider,.ytp-player.ytp-volume-expanded .ytp-volume-slider{width:68px;max-width:68px;opacity:1}.ytp-time{font-size:12px;white-space:nowrap;color:#eee;font-variant-numeric:tabular-nums;margin-left:2px}.ytp-spacer{flex:1 1 auto;min-width:3px}.ytp-progress{position:relative;height:14px;margin:0 5px 7px;display:flex;align-items:center;cursor:pointer;touch-action:none;outline:none}.ytp-track{position:absolute;inset-inline:0;height:3px;background:#ffffff47;transition:height .12s ease}.ytp-progress:hover .ytp-track,.ytp-progress:focus-visible .ytp-track,.ytp-progress.ytp-dragging .ytp-track{height:5px}.ytp-buffered,.ytp-played{position:absolute;left:0;top:0;height:100%;pointer-events:none}.ytp-buffered{background:#ffffff85}.ytp-played{background:var(--accent)}.ytp-knob{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;background:var(--accent);transform:translate(-50%,-50%) scale(0);transition:transform .12s ease;pointer-events:none}.ytp-progress:hover .ytp-knob,.ytp-progress:focus-visible .ytp-knob,.ytp-progress.ytp-dragging .ytp-knob{transform:translate(-50%,-50%) scale(1)}.ytp-tooltip{position:absolute;bottom:19px;transform:translateX(-50%);padding:5px 7px;border-radius:3px;background:#101611f5;color:#fff;font-size:12px;white-space:nowrap;box-shadow:0 2px 8px #0008;pointer-events:none;font-variant-numeric:tabular-nums}.ytp-center{position:absolute;z-index:8;left:50%;top:50%;width:66px;height:66px;display:flex;align-items:center;justify-content:center;transform:translate(-50%,-50%);border:1px solid #ffffff36;border-radius:50%;background:#111a;box-shadow:0 4px 24px #0007;backdrop-filter:blur(4px);color:#fff;cursor:pointer;transition:opacity .2s ease,transform .2s ease,background .18s ease;opacity:0}.ytp-center:hover{background:#303030e8;transform:translate(-50%,-50%) scale(1.06)}.ytp-center:focus-visible{opacity:1;outline:2px solid #fff;outline-offset:3px}.ytp-player:not(.ytp-playing) .ytp-center{opacity:1}.ytp-player.ytp-controls-hidden.ytp-playing .ytp-center{pointer-events:none}.ytp-feedback{position:absolute;z-index:12;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;align-items:center;justify-content:center;gap:9px;min-width:80px;min-height:80px;padding:14px;border-radius:50%;background:#0009;box-shadow:0 5px 28px #0005;animation:ytp-feedback .75s ease both;pointer-events:none}.ytp-feedback span{font-size:14px;font-weight:600;white-space:nowrap}.ytp-seek{position:absolute;z-index:10;top:50%;transform:translateY(-50%);width:30%;min-width:90px;height:42%;display:flex;align-items:center;justify-content:center;gap:8px;border-radius:50%;background:radial-gradient(ellipse at center,#0009 0,transparent 72%);pointer-events:none;animation:ytp-feedback .78s ease both}.ytp-seek.left{left:2%}.ytp-seek.right{right:2%}.ytp-seek span{font-size:15px;font-weight:600;text-shadow:0 2px 4px #000}.ytp-spinner{position:absolute;z-index:11;left:50%;top:50%;width:44px;height:44px;margin:-22px;border:4px solid #ffffff36;border-top-color:#fff;border-radius:50%;animation:ytp-spin .8s linear infinite;pointer-events:none}.ytp-settings-area{position:relative;display:flex;align-items:center}.ytp-settings-menu{position:absolute;right:0;bottom:43px;z-index:35;width:248px;max-height:min(62vh,420px);overflow:auto;border-radius:10px;background:#111713f8;box-shadow:0 8px 30px #0009;padding:12px;color:#fff;border:1px solid #ffffff12}.ytp-menu-heading{font-size:13px;font-weight:600;color:#e4eee8;margin:2px 0 8px}.ytp-menu-section+.ytp-menu-section{margin-top:12px;padding-top:11px;border-top:1px solid #ffffff1f}.ytp-speed-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}.ytp-speed,.ytp-quality{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;padding:7px 8px;border:0;border-radius:5px;background:transparent;color:#eee;font-size:12px;cursor:pointer;text-align:left}.ytp-speed{justify-content:center}.ytp-speed:hover,.ytp-quality:hover{background:#ffffff19}.ytp-speed[aria-pressed=true],.ytp-quality[aria-pressed=true]{background:#3ecf8e1c;color:#3ecf8e}.ytp-quality-list{display:grid;gap:2px}.ytp-modal-backdrop{position:absolute;z-index:50;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;background:#000b;backdrop-filter:blur(3px)}.ytp-modal{width:min(590px,100%);max-height:90%;overflow:auto;padding:18px 20px;border:1px solid #ffffff20;border-radius:12px;background:#111713;color:#fff;box-shadow:0 16px 50px #000a}.ytp-modal-header{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:13px}.ytp-modal-title{font-size:18px;font-weight:600}.ytp-shortcuts{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}.ytp-shortcut{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid #ffffff12;font-size:12px;color:#e6e6e6}.ytp-keys{display:flex;gap:4px;flex:0 0 auto}.ytp-keys kbd{min-width:23px;padding:4px 6px;border:1px solid #ffffff2e;border-radius:4px;background:#ffffff12;color:#fff;text-align:center;font:600 11px Roboto,Arial,sans-serif}@keyframes ytp-spin{to{transform:rotate(360deg)}}@keyframes ytp-feedback{0%{opacity:0;scale:.88}16%{opacity:1;scale:1}68%{opacity:1;scale:1}100%{opacity:0;scale:1.04}}@media(max-width:560px){.ytp-player{border-radius:7px}.ytp-controls{padding:21px 6px 5px}.ytp-controls-row{gap:2px}.ytp-button{width:30px;height:32px}.ytp-time{font-size:10px;margin-left:1px}.ytp-volume-slider{width:44px;max-width:44px;opacity:1}.ytp-volume:hover .ytp-volume-slider,.ytp-volume:focus-within .ytp-volume-slider,.ytp-player.ytp-volume-expanded .ytp-volume-slider{width:44px;max-width:44px}.ytp-center{width:56px;height:56px}.ytp-feedback{min-width:68px;min-height:68px}.ytp-shortcuts{grid-template-columns:1fr}.ytp-modal{padding:14px}.ytp-progress{margin-inline:3px}.ytp-settings-menu{right:-4px;width:228px}}@media(max-width:390px){.ytp-time{display:none}.ytp-controls-row{gap:1px}.ytp-volume-slider{width:34px;max-width:34px}.ytp-volume:hover .ytp-volume-slider,.ytp-volume:focus-within .ytp-volume-slider,.ytp-player.ytp-volume-expanded .ytp-volume-slider{width:34px;max-width:34px}.ytp-button{width:28px}}@media(prefers-reduced-motion:reduce){.ytp-player *{animation-duration:.01ms!important;transition-duration:.01ms!important}}";

const PLAYER_SPEED_OPTIONS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

function VideoPlayer({file, streamUrl, active = true, sources = Array.isArray(file?.sources) ? file.sources : []}) {
    const wrapRef = useRef(null);
    const videoRef = useRef(null);
    const progressRef = useRef(null);
    const settingsRef = useRef(null);
    const controlsTimerRef = useRef(null);
    const feedbackTimerRef = useRef(null);
    const seekTimerRef = useRef(null);
    const clickTimerRef = useRef(null);
    const ignoreClickTimerRef = useRef(null);
    const lastTouchTapRef = useRef({side: "", time: 0});
    const ignoreNextClickRef = useRef(false);
    const draggingRef = useRef(false);
    const restorePlaybackRef = useRef(null);

    const readSavedNumber = (key, fallback, min, max) => {
        try {
            const saved = window.localStorage.getItem(key);
            const value = Number(saved);
            return saved !== null && Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback;
        } catch {
            return fallback;
        }
    };
    const readSavedBoolean = (key, fallback) => {
        try {
            const value = window.localStorage.getItem(key);
            return value === null ? fallback : value === "true";
        } catch {
            return fallback;
        }
    };

    const [playing, setPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [volume, setVolume] = useState(() => readSavedNumber("storelimitless-player-volume", 1, 0, 1));
    const [muted, setMuted] = useState(() => readSavedBoolean("storelimitless-player-muted", false));
    const [playbackRate, setPlaybackRate] = useState(() => readSavedNumber("storelimitless-player-speed", 1, 0.25, 2));
    const [fullscreen, setFullscreen] = useState(false);
    const [controlsVisible, setControlsVisible] = useState(true);
    const [volumeExpanded, setVolumeExpanded] = useState(false);
    const [buffering, setBuffering] = useState(false);
    const [showSettings, setShowSettings] = useState(false);
    const [showShortcuts, setShowShortcuts] = useState(false);
    const [textTracks, setTextTracks] = useState([]);
    const [captionsOn, setCaptionsOn] = useState(false);
    const [captionFontSize, setCaptionFontSize] = useState(16);
    const [bufferedPercent, setBufferedPercent] = useState(0);
    const [hoverProgress, setHoverProgress] = useState({x: 0, time: 0, visible: false});
    const [centerFeedback, setCenterFeedback] = useState(null);
    const [seekFeedback, setSeekFeedback] = useState(null);
    const [activeSource, setActiveSource] = useState(streamUrl);

    const qualityOptions = (Array.isArray(sources) ? sources : [])
        .map((source, index) => ({
            label: source.label || source.quality || source.resolution || ("Quality " + (index + 1)),
            src: source.src || source.url,
        }))
        .filter((source) => typeof source.src === "string" && source.src.length > 0);
    const formatTime = (value) => {
        if (!Number.isFinite(value) || value < 0) return "0:00";
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor((value % 3600) / 60);
        const seconds = Math.floor(value % 60).toString().padStart(2, "0");
        return hours > 0 ? hours + ":" + minutes.toString().padStart(2, "0") + ":" + seconds : minutes + ":" + seconds;
    };

    const updateBuffered = useCallback(() => {
        const video = videoRef.current;
        if (!video || !Number.isFinite(video.duration) || video.duration <= 0) { setBufferedPercent(0); return; }
        let end = 0;
        for (let index = 0; index < video.buffered.length; index += 1) end = Math.max(end, video.buffered.end(index));
        setBufferedPercent(Math.max(0, Math.min(100, (end / video.duration) * 100)));
    }, []);

    const showCenterFeedback = useCallback((kind, label = "") => {
        setCenterFeedback({kind, label});
        window.clearTimeout(feedbackTimerRef.current);
        feedbackTimerRef.current = window.setTimeout(() => setCenterFeedback(null), 760);
    }, []);

    const showSeekFeedback = useCallback((seconds) => {
        setSeekFeedback({side: seconds > 0 ? "right" : "left", label: (seconds > 0 ? "+" : "") + seconds + "s"});
        window.clearTimeout(seekTimerRef.current);
        seekTimerRef.current = window.setTimeout(() => setSeekFeedback(null), 820);
    }, []);

    const revealControls = useCallback(() => {
        setControlsVisible(true);
        window.clearTimeout(controlsTimerRef.current);
        if (videoRef.current && !videoRef.current.paused && !showShortcuts && !showSettings) {
            controlsTimerRef.current = window.setTimeout(() => {
                if (videoRef.current && !videoRef.current.paused && !showShortcuts && !showSettings) setControlsVisible(false);
            }, 3000);
        }
    }, [showShortcuts, showSettings]);

    const togglePlayback = useCallback(() => {
        const video = videoRef.current;
        if (!video) return;
        if (video.paused || video.ended) {
            void video.play().then(() => showCenterFeedback("play")).catch(() => {});
        } else {
            video.pause();
            showCenterFeedback("pause");
        }
        revealControls();
    }, [revealControls, showCenterFeedback]);

    // Keep hidden persistent viewers silent while their download tray remains mounted.
    useEffect(() => {
        if (!active) {
            videoRef.current?.pause();
            setControlsVisible(true);
        }
    }, [active]);

    const seekTo = useCallback((time) => {
        const video = videoRef.current;
        if (!video || !Number.isFinite(video.duration)) return false;
        video.currentTime = Math.max(0, Math.min(video.duration, time));
        setCurrentTime(video.currentTime);
        updateBuffered();
        return true;
    }, [updateBuffered]);

    const seekBy = useCallback((seconds, feedback = true) => {
        const video = videoRef.current;
        if (!video || !Number.isFinite(video.duration)) return;
        const next = Math.max(0, Math.min(video.duration, video.currentTime + seconds));
        video.currentTime = next;
        setCurrentTime(next);
        if (feedback) showSeekFeedback(seconds);
    }, [showSeekFeedback]);

    const changeVolume = useCallback((nextVolume, feedback = true) => {
        const video = videoRef.current;
        const next = Math.max(0, Math.min(1, Math.round(nextVolume * 100) / 100));
        setVolume(next);
        if (video) { video.volume = next; video.muted = next === 0; }
        setMuted(next === 0);
        if (feedback) showCenterFeedback("volume", Math.round(next * 100) + "%");
    }, [showCenterFeedback]);

    const toggleMute = useCallback(() => {
        const video = videoRef.current;
        const nextMuted = video ? !video.muted : !muted;
        if (video) video.muted = nextMuted;
        setMuted(nextMuted);
        showCenterFeedback(nextMuted ? "muted" : "volume", nextMuted ? "Muted" : Math.round((video?.volume ?? volume) * 100) + "%");
    }, [muted, showCenterFeedback, volume]);

    const toggleFullscreen = useCallback(async () => {
        const target = wrapRef.current;
        if (!target) return;
        try {
            if (document.fullscreenElement) await document.exitFullscreen();
            else if (target.requestFullscreen) await target.requestFullscreen();
            else if (target.webkitRequestFullscreen) target.webkitRequestFullscreen();
        } catch {
            // Fullscreen can be unavailable in embedded or permission-restricted browsers.
        }
    }, []);

    const toggleCaptions = useCallback(() => {
        const video = videoRef.current;
        if (!video?.textTracks) return;
        const tracks = Array.from(video.textTracks).filter((track) => track.kind === "captions" || track.kind === "subtitles");
        if (!tracks.length) return;
        const nextOn = !tracks.some((track) => track.mode === "showing");
        tracks.forEach((track, index) => { track.mode = nextOn && index === 0 ? "showing" : "disabled"; });
        setCaptionsOn(nextOn);
    }, []);

    const updateTrackState = useCallback(() => {
        const tracks = Array.from(videoRef.current?.textTracks || []).filter((track) => track.kind === "captions" || track.kind === "subtitles");
        setTextTracks(tracks);
        setCaptionsOn(tracks.some((track) => track.mode === "showing"));
    }, []);

    const handleLoadedMetadata = useCallback((event) => {
        const video = event.currentTarget;
        setDuration(Number.isFinite(video.duration) ? video.duration : 0);
        setCurrentTime(video.currentTime || 0);
        video.volume = volume;
        video.muted = muted;
        video.playbackRate = playbackRate;
        updateBuffered();
        updateTrackState();
        const pending = restorePlaybackRef.current;
        if (pending) {
            try { video.currentTime = Math.min(pending.time, Number.isFinite(video.duration) ? video.duration : pending.time); } catch { /* Stream may not be seekable yet. */ }
            restorePlaybackRef.current = null;
            if (pending.playing) void video.play().catch(() => {});
        }
    }, [muted, playbackRate, updateBuffered, updateTrackState, volume]);

    const handleSurfacePointerUp = useCallback((event) => {
        if (event.pointerType !== "touch") return;
        const video = videoRef.current;
        if (!video) return;
        const bounds = video.getBoundingClientRect();
        if (!bounds.width) return;
        const x = (event.clientX - bounds.left) / bounds.width;
        const side = x < 1 / 3 ? "left" : x > 2 / 3 ? "right" : "center";
        const now = event.timeStamp;
        ignoreNextClickRef.current = true;
        window.clearTimeout(ignoreClickTimerRef.current);
        ignoreClickTimerRef.current = window.setTimeout(() => { ignoreNextClickRef.current = false; }, 500);
        revealControls();
        if (side === "center") {
            lastTouchTapRef.current = {side: "", time: 0};
            togglePlayback();
            return;
        }
        const lastTap = lastTouchTapRef.current;
        if (lastTap.side === side && now - lastTap.time <= 320) {
            seekBy(side === "right" ? 10 : -10);
            lastTouchTapRef.current = {side: "", time: 0};
        } else lastTouchTapRef.current = {side, time: now};
    }, [revealControls, seekBy, togglePlayback]);

    const handleVideoClick = useCallback((event) => {
        if (ignoreNextClickRef.current) {
            ignoreNextClickRef.current = false;
            window.clearTimeout(ignoreClickTimerRef.current);
            return;
        }
        if (event.detail > 1) {
            window.clearTimeout(clickTimerRef.current);
            clickTimerRef.current = null;
            return;
        }
        window.clearTimeout(clickTimerRef.current);
        clickTimerRef.current = window.setTimeout(() => {
            togglePlayback();
            clickTimerRef.current = null;
        }, 260);
    }, [togglePlayback]);

    const handleVideoDoubleClick = useCallback(() => {
        window.clearTimeout(clickTimerRef.current);
        clickTimerRef.current = null;
        void toggleFullscreen();
    }, [toggleFullscreen]);

    const setProgressFromPointer = useCallback((event) => {
        const bar = progressRef.current;
        if (!bar || !duration) return;
        const rect = bar.getBoundingClientRect();
        if (!rect.width) return;
        const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        setHoverProgress({x: fraction * rect.width, time: fraction * duration, visible: true});
        if (draggingRef.current) seekTo(fraction * duration);
    }, [duration, seekTo]);

    const handleProgressDown = useCallback((event) => {
        if (event.button !== undefined && event.button !== 0) return;
        event.preventDefault();
        draggingRef.current = true;
        progressRef.current?.classList.add("ytp-dragging");
        try { progressRef.current?.setPointerCapture(event.pointerId); } catch { /* Older browsers may not support capture. */ }
        setProgressFromPointer(event);
        revealControls();
    }, [revealControls, setProgressFromPointer]);

    const handleProgressUp = useCallback((event) => {
        if (!draggingRef.current) return;
        setProgressFromPointer(event);
        draggingRef.current = false;
        progressRef.current?.classList.remove("ytp-dragging");
        try {
            if (progressRef.current?.hasPointerCapture(event.pointerId)) progressRef.current.releasePointerCapture(event.pointerId);
        } catch { /* Pointer capture may already have been released. */ }
    }, [setProgressFromPointer]);

    const jumpChapter = useCallback((direction) => {
        const video = videoRef.current;
        const track = Array.from(video?.textTracks || []).find((item) => item.kind === "chapters" && item.cues?.length);
        if (!video || !track) return false;
        const cues = Array.from(track.cues).sort((a, b) => a.startTime - b.startTime);
        if (direction < 0) {
            let previous = cues[0];
            for (const cue of cues) { if (cue.startTime < video.currentTime - 0.5) previous = cue; else break; }
            return seekTo(previous.startTime);
        }
        const next = cues.find((cue) => cue.startTime > video.currentTime + 0.5);
        return next ? seekTo(next.startTime) : false;
    }, [seekTo]);

    const handleKeyboardShortcut = useCallback((event) => {
        if (!active) return;
        const target = event.target;
        const tagName = target?.tagName?.toLowerCase();
        if (tagName === "input" || tagName === "textarea" || tagName === "select" || target?.isContentEditable) return;
        const key = event.key;
        if (showShortcuts && key !== "Escape") return;
        revealControls();
        const lower = key.toLowerCase();
        const video = videoRef.current;
        if (!video) return;

        if (event.ctrlKey && !event.metaKey && !event.altKey) {
            if ((key === "ArrowLeft" || key === "ArrowRight") && jumpChapter(key === "ArrowLeft" ? -1 : 1)) event.preventDefault();
            return;
        }
        if (event.ctrlKey || event.metaKey || event.altKey) return;

        if (key === "Escape") {
            if (showShortcuts) { event.preventDefault(); setShowShortcuts(false); }
            else if (showSettings) { event.preventDefault(); setShowSettings(false); }
            else if (fullscreen || document.fullscreenElement) { event.preventDefault(); void toggleFullscreen(); }
            return;
        }
        if (event.shiftKey && (key === "?" || key === "/")) {
            event.preventDefault();
            setShowShortcuts(true);
            setShowSettings(false);
            setControlsVisible(true);
            return;
        }
        if (event.shiftKey && (key === "<" || event.code === "Comma")) {
            event.preventDefault();
            const index = PLAYER_SPEED_OPTIONS.indexOf(playbackRate);
            const next = PLAYER_SPEED_OPTIONS[Math.max(0, index - 1)];
            setPlaybackRate(next);
            video.playbackRate = next;
            showCenterFeedback("speed", next + "×");
            return;
        }
        if (event.shiftKey && (key === ">" || event.code === "Period")) {
            event.preventDefault();
            const index = PLAYER_SPEED_OPTIONS.indexOf(playbackRate);
            const next = PLAYER_SPEED_OPTIONS[Math.min(PLAYER_SPEED_OPTIONS.length - 1, index < 0 ? 0 : index + 1)];
            setPlaybackRate(next);
            video.playbackRate = next;
            showCenterFeedback("speed", next + "×");
            return;
        }
        if (captionsOn && (key === "+" || (key === "=" && event.shiftKey))) {
            event.preventDefault();
            setCaptionFontSize((size) => Math.min(42, size + 2));
            return;
        }
        if (captionsOn && key === "-") {
            event.preventDefault();
            setCaptionFontSize((size) => Math.max(10, size - 2));
            return;
        }
        if ((key === " " || event.code === "Space") && target?.closest?.("button")) return;
        if (key === " " || event.code === "Space" || lower === "k") { event.preventDefault(); togglePlayback(); return; }
        if (lower === "j") { event.preventDefault(); seekBy(-10); return; }
        if (lower === "l") { event.preventDefault(); seekBy(10); return; }
        if (key === "ArrowLeft") { event.preventDefault(); seekBy(-5); return; }
        if (key === "ArrowRight") { event.preventDefault(); seekBy(5); return; }
        if (key === "ArrowUp") { event.preventDefault(); changeVolume((video.muted ? 0 : video.volume) + 0.05); return; }
        if (key === "ArrowDown") { event.preventDefault(); changeVolume((video.muted ? 0 : video.volume) - 0.05); return; }
        if (lower === "m") { event.preventDefault(); toggleMute(); return; }
        if (lower === "f") { event.preventDefault(); void toggleFullscreen(); return; }
        if (lower === "c") {
            if (!textTracks.length) return;
            event.preventDefault();
            toggleCaptions();
            return;
        }
        let digit = /^[0-9]$/.test(key) ? key : "";
        if (!digit && /^Numpad[0-9]$/.test(event.code)) digit = event.code.slice(-1);
        if (digit) {
            event.preventDefault();
            if (Number.isFinite(video.duration) && video.duration > 0) seekTo((Number(digit) / 10) * video.duration);
            return;
        }
        if (key === "Home") { event.preventDefault(); seekTo(0); return; }
        if (key === "End") { event.preventDefault(); seekTo(duration || video.duration || 0); return; }
        if (key === "," && video.paused) { event.preventDefault(); seekBy(-1 / 30, false); return; }
        if (key === "." && video.paused) event.preventDefault(), seekBy(1 / 30, false);
    }, [
        active, revealControls, seekBy, seekTo, showSettings, showShortcuts, showCenterFeedback,
        textTracks.length, toggleCaptions, toggleFullscreen, toggleMute,
    ]);

    useEffect(() => {
        window.addEventListener("keydown", handleKeyboardShortcut);
        return () => window.removeEventListener("keydown", handleKeyboardShortcut);
    }, [handleKeyboardShortcut]);

    useEffect(() => {
        const tracks = videoRef.current?.textTracks;
        if (!tracks) return undefined;
        tracks.addEventListener?.("addtrack", updateTrackState);
        tracks.addEventListener?.("removetrack", updateTrackState);
        tracks.addEventListener?.("change", updateTrackState);
        updateTrackState();
        return () => {
            tracks.removeEventListener?.("addtrack", updateTrackState);
            tracks.removeEventListener?.("removetrack", updateTrackState);
            tracks.removeEventListener?.("change", updateTrackState);
        };
    }, [activeSource, updateTrackState]);


    useEffect(() => {
        try {
            window.localStorage.setItem("storelimitless-player-volume", String(volume));
            window.localStorage.setItem("storelimitless-player-muted", String(muted));
            window.localStorage.setItem("storelimitless-player-speed", String(playbackRate));
        } catch { /* Storage can be disabled by the browser. */ }
    }, [muted, playbackRate, volume]);

    useEffect(() => {
        const handleFullscreenChange = () => setFullscreen(Boolean(document.fullscreenElement));
        document.addEventListener("fullscreenchange", handleFullscreenChange);
        document.addEventListener("webkitfullscreenchange", handleFullscreenChange);
        return () => {
            document.removeEventListener("fullscreenchange", handleFullscreenChange);
            document.removeEventListener("webkitfullscreenchange", handleFullscreenChange);
        };
    }, []);

    useEffect(() => {
        const handleOutsidePointer = (event) => {
            if (settingsRef.current && !settingsRef.current.contains(event.target)) setShowSettings(false);
        };
        document.addEventListener("pointerdown", handleOutsidePointer);
        return () => document.removeEventListener("pointerdown", handleOutsidePointer);
    }, []);

    useEffect(() => {
        if (playing && !showShortcuts && !showSettings) {
            window.clearTimeout(controlsTimerRef.current);
            controlsTimerRef.current = window.setTimeout(() => {
                if (videoRef.current && !videoRef.current.paused && !showShortcuts && !showSettings) setControlsVisible(false);
            }, 3000);
        } else {
            window.clearTimeout(controlsTimerRef.current);
            if (!playing) setControlsVisible(true);
        }
        return () => window.clearTimeout(controlsTimerRef.current);
    }, [playing, showSettings, showShortcuts]);

    useEffect(() => {
        return () => {
            window.clearTimeout(controlsTimerRef.current);
            window.clearTimeout(feedbackTimerRef.current);
            window.clearTimeout(seekTimerRef.current);
            window.clearTimeout(clickTimerRef.current);
            window.clearTimeout(ignoreClickTimerRef.current);
        };
    }, []);

    useEffect(() => { setActiveSource(streamUrl); }, [streamUrl]);

    const activeQuality = qualityOptions.find((option) => option.src === activeSource)?.label || "Auto";
    const bufferedWidth = Math.max(0, Math.min(100, bufferedPercent));
    const playedWidth = duration > 0 ? Math.max(0, Math.min(100, (currentTime / duration) * 100)) : 0;
    const icon = (name, size = 24) => {
        const attrs = {width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true", focusable: "false"};
        let shape;
        switch (name) {
        case "play": shape = <path d="M8 5v14l11-7z" fill="currentColor" stroke="none" />; break;
        case "pause": shape = <><path d="M7 5h4v14H7z" fill="currentColor" stroke="none" /><path d="M15 5h4v14h-4z" fill="currentColor" stroke="none" /></>; break;
        case "volume": shape = <><path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" stroke="none" /><path d="M15.5 8.5a5 5 0 0 1 0 7M18 5.5a9 9 0 0 1 0 13" /></>; break;
        case "muted": shape = <><path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" stroke="none" /><path d="m16 9 5 6m0-6-5 6" /></>; break;
        case "settings": shape = <><circle cx="12" cy="12" r="3" /><path d="m19 13 2 1-2 4-2-1-2 1v2h-5v-2l-2-1-2 1-2-4 2-1v-2l-2-1 2-4 2 1 2-1V4h5v2l2 1 2-1 2 4-2 1z" /></>; break;
        case "fullscreen": shape = <path d="M8 4H4v4M16 4h4v4M4 16v4h4M20 16v4h-4" />; break;
        case "exitFullscreen": shape = <path d="M4 8h4V4M16 4v4h4M20 16h-4v4M8 20v-4H4" />; break;
        case "close": shape = <path d="m6 6 12 12M18 6 6 18" />; break;
        case "check": shape = <path d="m5 12 4 4L19 6" />; break;
        case "back": shape = <><path d="M10 5 3 12l7 7" /><path d="M3 12h12a6 6 0 0 1 6 6" /></>; break;
        case "forward": shape = <><path d="m14 5 7 7-7 7" /><path d="M21 12H9a6 6 0 0 0-6 6" /></>; break;
        case "speed": shape = <><path d="M12 3v3M4.9 4.9 7 7M3 12h3M5 19l2-2M19 19l-2-2M21 12h-3" /><path d="M12 12 17 8" /><circle cx="12" cy="12" r="9" /></>; break;
        default: shape = <circle cx="12" cy="12" r="8" />;
        }
        return <svg {...attrs}>{shape}</svg>;
    };

    const changeQuality = (option) => {
        const video = videoRef.current;
        if (!video || option.src === activeSource) { setShowSettings(false); return; }
        restorePlaybackRef.current = {time: video.currentTime, playing: !video.paused};
        setActiveSource(option.src);
        setShowSettings(false);
    };

    return (
        <div
            ref={wrapRef}
            className={["ytp-player", playing ? "ytp-playing" : "", controlsVisible ? "" : "ytp-controls-hidden", volumeExpanded ? "ytp-volume-expanded" : ""].join(" ")}
            tabIndex={0}
            role="region"
            aria-label="Video player"
            onMouseMove={revealControls}
            onMouseEnter={revealControls}
            onMouseLeave={() => {
                if (videoRef.current && !videoRef.current.paused) {
                    window.clearTimeout(controlsTimerRef.current);
                    controlsTimerRef.current = window.setTimeout(() => {
                        if (!showShortcuts && !showSettings) setControlsVisible(false);
                    }, 3000);
                }
                setHoverProgress((state) => ({...state, visible: false}));
            }}
            onTouchStart={revealControls}
            onKeyDown={revealControls}
        >
            <style>{PLAYER_STYLES + ".ytp-video::cue{font-size:" + captionFontSize + "px;}"}</style>
            <video
                ref={videoRef}
                className="ytp-video"
                src={activeSource}
                preload="metadata"
                playsInline
                controls={false}
                disablePictureInPicture={false}
                aria-label={file?.name || "Video preview"}
                onClick={handleVideoClick}
                onDoubleClick={handleVideoDoubleClick}
                onPointerUp={handleSurfacePointerUp}
                onContextMenu={(event) => event.preventDefault()}
                onLoadedMetadata={handleLoadedMetadata}
                onDurationChange={(event) => {
                    if (Number.isFinite(event.currentTarget.duration)) setDuration(event.currentTarget.duration);
                    updateBuffered();
                }}
                onTimeUpdate={(event) => { setCurrentTime(event.currentTarget.currentTime || 0); updateBuffered(); }}
                onProgress={updateBuffered}
                onPlay={() => { setPlaying(true); setBuffering(false); revealControls(); }}
                onPause={() => {
                    setPlaying(false);
                    setBuffering(false);
                    setControlsVisible(true);
                    window.clearTimeout(controlsTimerRef.current);
                }}
                onWaiting={() => setBuffering(true)}
                onCanPlay={() => setBuffering(false)}
                onPlaying={() => setBuffering(false)}
                onEnded={() => { setPlaying(false); setControlsVisible(true); }}
                onVolumeChange={(event) => { setVolume(event.currentTarget.volume); setMuted(event.currentTarget.muted); }}
                onRateChange={(event) => setPlaybackRate(event.currentTarget.playbackRate)}
            />

            {buffering && <div className="ytp-spinner" role="status" aria-label="Buffering video" />}
            {centerFeedback && <div className="ytp-feedback" aria-live="polite">{icon(centerFeedback.kind === "play" ? "play" : centerFeedback.kind === "pause" ? "pause" : centerFeedback.kind === "muted" ? "muted" : centerFeedback.kind === "speed" ? "speed" : "volume", 32)}{centerFeedback.label && <span>{centerFeedback.label}</span>}</div>}
            {seekFeedback && <div className={"ytp-seek " + seekFeedback.side} aria-live="polite">{icon(seekFeedback.side === "left" ? "back" : "forward", 28)}<span>{seekFeedback.label}</span></div>}

            <button type="button" className="ytp-center" aria-label={playing ? "Pause video" : "Play video"} title={playing ? "Pause (K)" : "Play (K)"} onClick={(event) => { event.stopPropagation(); togglePlayback(); }}>
                {icon(playing ? "pause" : "play", 38)}
            </button>

            <div className="ytp-controls" aria-label="Video controls">
                <div
                    ref={progressRef}
                    className="ytp-progress"
                    role="slider"
                    tabIndex={0}
                    aria-label="Video progress"
                    aria-valuemin={0}
                    aria-valuemax={Math.round(duration || 0)}
                    aria-valuenow={Math.round(currentTime)}
                    aria-valuetext={formatTime(currentTime) + " of " + formatTime(duration)}
                    onPointerDown={handleProgressDown}
                    onPointerMove={setProgressFromPointer}
                    onPointerUp={handleProgressUp}
                    onPointerCancel={handleProgressUp}
                    onPointerLeave={() => { if (!draggingRef.current) setHoverProgress((state) => ({...state, visible: false})); }}
                    onFocus={revealControls}
                >
                    <div className="ytp-track"><div className="ytp-buffered" style={{width: bufferedWidth + "%"}} /><div className="ytp-played" style={{width: playedWidth + "%"}} /></div>
                    <div className="ytp-knob" style={{left: playedWidth + "%"}} />
                    {hoverProgress.visible && <div className="ytp-tooltip" style={{left: hoverProgress.x}}>{formatTime(hoverProgress.time)}</div>}
                </div>

                <div className="ytp-controls-row">
                    <button type="button" className="ytp-button" aria-label={playing ? "Pause video" : "Play video"} title={playing ? "Pause (K)" : "Play (K)"} onClick={togglePlayback}>{icon(playing ? "pause" : "play")}</button>
                    <div className="ytp-volume">
                        <button type="button" className="ytp-button" aria-label={muted || volume === 0 ? "Unmute" : "Mute"} title={muted || volume === 0 ? "Unmute (M)" : "Mute (M)"} onClick={() => { toggleMute(); setVolumeExpanded((value) => !value); }}>{icon(muted || volume === 0 ? "muted" : "volume")}</button>
                        <input className="ytp-volume-slider" type="range" min={0} max={1} step={0.01} value={muted ? 0 : volume} aria-label="Volume" title={"Volume " + Math.round(volume * 100) + "%"} onChange={(event) => changeVolume(Number(event.target.value))} />
                    </div>
                    <span className="ytp-time" aria-live="off">{formatTime(currentTime)} / {formatTime(duration)}</span>
                    <div className="ytp-spacer" />

                    {textTracks.length > 0 && <button type="button" className="ytp-button" aria-label={captionsOn ? "Turn captions off" : "Turn captions on"} aria-pressed={captionsOn} title={captionsOn ? "Turn captions off (C)" : "Turn captions on (C)"} onClick={toggleCaptions}><span style={{fontSize:12,fontWeight:800,letterSpacing:"-.5px",border:captionsOn?"1px solid #fff":"1px solid transparent",borderRadius:3,padding:"2px 3px"}}>CC</span></button>}

                    <div className="ytp-settings-area" ref={settingsRef}>
                        <button type="button" className="ytp-button" aria-label="Settings" aria-expanded={showSettings} title="Settings" onClick={() => { setShowSettings((value) => !value); setControlsVisible(true); }}>{icon("settings")}</button>
                        {showSettings && <div className="ytp-settings-menu" role="dialog" aria-label="Player settings">
                            <div className="ytp-menu-section">
                                <div className="ytp-menu-heading">Playback speed</div>
                                <div className="ytp-speed-grid">{PLAYER_SPEED_OPTIONS.map((rate) => <button key={rate} type="button" className="ytp-speed" aria-label={"Playback speed " + rate + "x"} aria-pressed={playbackRate === rate} onClick={() => { setPlaybackRate(rate); if (videoRef.current) videoRef.current.playbackRate = rate; setShowSettings(false); showCenterFeedback("speed", rate + "×"); }}>{rate === 1 ? "Normal" : rate + "×"}{playbackRate === rate && <span>{icon("check", 13)}</span>}</button>)}</div>
                            </div>
                            {qualityOptions.length > 1 && <div className="ytp-menu-section"><div className="ytp-menu-heading">Quality <span style={{fontWeight:400,color:"#aaa"}}>· {activeQuality}</span></div><div className="ytp-quality-list">{qualityOptions.map((option) => <button key={option.src} type="button" className="ytp-quality" aria-label={"Video quality " + option.label} aria-pressed={activeSource === option.src} onClick={() => changeQuality(option)}><span>{option.label}</span>{activeSource === option.src && icon("check", 14)}</button>)}</div></div>}
                        </div>}
                    </div>

                    <button type="button" className="ytp-button" aria-label={fullscreen ? "Exit fullscreen" : "Full screen"} title={fullscreen ? "Exit fullscreen (F)" : "Full screen (F)"} onClick={() => void toggleFullscreen()}>{icon(fullscreen ? "exitFullscreen" : "fullscreen")}</button>
                </div>
            </div>

            {showShortcuts && <div className="ytp-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowShortcuts(false); }}>
                <div className="ytp-modal" role="dialog" aria-modal="true" aria-labelledby="ytp-shortcuts-title">
                    <div className="ytp-modal-header"><div id="ytp-shortcuts-title" className="ytp-modal-title">Keyboard shortcuts</div><button type="button" className="ytp-button" aria-label="Close keyboard shortcuts" onClick={() => setShowShortcuts(false)}>{icon("close")}</button></div>
                    <div className="ytp-shortcuts">{[
                        [["Space","K"],"Play / pause"],[["J"],"Rewind 10 seconds"],[["L"],"Forward 10 seconds"],[["←"],"Rewind 5 seconds"],[["→"],"Forward 5 seconds"],[["↑"],"Volume up 5%"],[["↓"],"Volume down 5%"],[["M"],"Mute / unmute"],[["F"],"Toggle fullscreen"],[["Esc"],"Close menus / exit fullscreen"],[["C"],"Toggle captions"],[["0–9"],"Jump to 0–90%"],[["Home"],"Jump to beginning"],[["End"],"Jump to end"],[[","],"Previous frame (paused)"],[["."],"Next frame (paused)"],[["Shift",","],"Decrease playback speed"],[["Shift","."],"Increase playback speed"],[["+ / −"],"Caption text size"],[["Ctrl","← / →"],"Previous / next chapter"],[["Shift","/"],"Show shortcuts"]
                    ].map((entry) => <div className="ytp-shortcut" key={entry[1]}><span>{entry[1]}</span><span className="ytp-keys">{entry[0].map((part) => <kbd key={part}>{part}</kbd>)}</span></div>)}</div>
                </div>
            </div>}
        </div>
    );
}

/**
 * Resolve a signed stream URL and render the selected preview.
 * @returns {import("react").ReactElement}
 */
export default function ViewFile({active = true} = {}) {
    const location = useLocation();
    const viewRouteMatch = location.pathname.match(/^\/view\/([^/]+)\/?$/);
    const lastViewContextRef = useRef(null);
    if (viewRouteMatch) {
        lastViewContextRef.current = {
            routeId: decodeURIComponent(viewRouteMatch[1]),
            search: location.search,
        };
    }
    const routeId = viewRouteMatch
        ? decodeURIComponent(viewRouteMatch[1])
        : lastViewContextRef.current?.routeId ?? null;
    const searchParams = new URLSearchParams(
        viewRouteMatch ? location.search : lastViewContextRef.current?.search || "",
    );
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
    const downloadTrayJobs = (fileId || publicToken) && (downloadError || downloadState.status !== "idle") ? [{
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
                    endAuthSession();
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
            // Persistent pages can be mounted in the background after login.
            // An inactive viewer without a route target is not a failed download.
            if (active) setDownloadError("No file was selected.");
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
                const response = await fetch(`${API_URL}/auth/file/${encodeURIComponent(fileId)}/download-link`, {
                    method: "POST",
                    headers: {Authorization: `Bearer ${sessionToken}`},
                });
                const data = await response.json().catch(() => ({}));
                if (response.status === 401) {
                    endAuthSession();
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
    }, [active, fileId, navigate, publicFile, publicFileError, publicToken]);

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

    useEffect(() => {
        const handleSessionEnded = () => {
            downloadDismissedRef.current = true;
            downloadControllerRef.current?.abort();
            downloadControllerRef.current = null;
            const session = downloadSessionRef.current;
            if (session.tempName && typeof navigator.storage?.getDirectory === "function") {
                const tempName = session.tempName;
                navigator.storage.getDirectory().then((root) => root.removeEntry(tempName)).catch(() => {});
            }
            session.tempName = "";
            session.total = 0;
            session.etag = "";
            session.transferred = 0;
            removeResumableDownloadChunks(session.id).catch(() => {});
            setDownloadState({status: "idle", transferred: 0, total: 0, message: ""});
            setDownloadError("");
            if (!publicToken) {
                setDownloadUrl("");
                setDownloadPending(false);
                setPrivateFileMetadata(null);
                setPrivateFilePending(false);
                setPrivateFileError("Sign in to download this file.");
                setSource(null);
            }
        };

        window.addEventListener(AUTH_SESSION_END_EVENT, handleSessionEnded);
        return () => window.removeEventListener(AUTH_SESSION_END_EVENT, handleSessionEnded);
    }, [publicToken]);

    /**
     * Stage the file in StoreLimitless temporary storage before triggering the browser save.
     * This deliberately avoids a direct link to the download endpoint.
     * @returns {Promise<void>}
     */
    const startStagedDownload = useCallback(async () => {
        if (!downloadUrl || downloadControllerRef.current) return;
        const sessionToken = publicToken ? null : getToken();
        if (!publicToken && !sessionToken) {
            setDownloadUrl("");
            setDownloadPending(false);
            setDownloadError("Sign in to download this file.");
            return;
        }
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
        const ensureSession = () => {
            if (controller.signal.aborted || (!publicToken && (!sessionToken || getToken() !== sessionToken))) {
                throw new DOMException("The session ended", "AbortError");
            }
        };

        updateState({status: "downloading", transferred: session.transferred || 0, total: session.total || 0, message: "Preparing StoreLimitless download"});
        try {
            ensureSession();
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
                    ensureSession();
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
                ensureSession();
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
                ensureSession();
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
            ensureSession();
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
            // An in-flight IndexedDB/OPFS write can settle after the logout listener ran.
            // Clean again here so late writes cannot leave private bytes for the next session.
            if (!publicToken && getToken() !== sessionToken) {
                const stagedName = tempName || session.tempName;
                if (stagedName && typeof navigator.storage?.getDirectory === "function") {
                    navigator.storage.getDirectory().then((root) => root.removeEntry(stagedName)).catch(() => {});
                }
                session.tempName = "";
                session.total = 0;
                session.etag = "";
                session.transferred = 0;
                removeResumableDownloadChunks(session.id).catch(() => {});
            }
        }
    }, [downloadUrl, name, publicFile?.size, publicToken]);

    /** @type {[VideoSource | null, import("react").Dispatch<import("react").SetStateAction<VideoSource | null>>]} */
    const [source, setSource] = useState(null);
    const [status, setStatus] = useState("Preparing video…");
    const [error, setError] = useState("");
    const [copyStatus, setCopyStatus] = useState("");
    const copyTimer = useRef(null);

    // Backspace returns to the previous page/folder, unless the user is editing text.
    useEffect(() => {
        /**
         * Navigate back from the viewer unless a text-editing control has focus.
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        const handleKeyDown = (event) => {
            if (!active || event.key !== "Backspace" || event.altKey || event.ctrlKey || event.metaKey) return;

            const target = event.target;
            const tagName = target?.tagName?.toLowerCase();
            const isTextEntry = tagName === "input" || tagName === "textarea" || tagName === "select" || target?.isContentEditable;

            if (isTextEntry) return;

            event.preventDefault();
            navigate(-1);
        };

        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [active, navigate]);

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
                const response = await fetch(`${API_URL}/auth/file/${fileId}/private-stream-link`, {
                    method: "POST", headers: {Authorization: `Bearer ${token}`},
                });
                if (response.status === 401) {
                    endAuthSession();
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

    return (<div className="absolute inset-0 flex h-screen flex-col bg-[#0b0f0c] text-[#f3f7f4]"
                 style={{
                     fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
                     visibility: active ? "visible" : "hidden",
                     pointerEvents: active ? "auto" : "none",
                 }}>
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[#243229] bg-[#0b0f0c] px-5">
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
                            <VideoPlayer file={{name}} streamUrl={source.streamUrl} heightClass="h-full" active={active}/>
                        </div>
                    </div>)}
        </main>
        <UploadJobsTray
            traySide="right"
            trayPosition="bottom"
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
