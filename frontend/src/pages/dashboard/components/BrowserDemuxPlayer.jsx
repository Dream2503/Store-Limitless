import {useCallback, useEffect, useRef, useState} from "react";
import MediaMuxWorker from "./MediaMuxWorker.js?worker";
import MediaAudioWorker from "./MediaAudioWorker.js?worker";

const DEMUX_WASM_URL = "https://cdn.jsdelivr.net/npm/web-demuxer@4.0.0/dist/wasm-files/web-demuxer.wasm";
const MAX_FORWARD_BUFFER_SECONDS = 8; // Bounded MediaSource lookahead
const RETAIN_VIDEO_BUFFER_SECONDS = 20;
const TEXT_SUBTITLE_CODECS = new Set(["subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"]);

function formatVttTimestamp(seconds) {
    const milliseconds = Math.max(0, Math.round(Number(seconds) * 1000));
    const hours = Math.floor(milliseconds / 3600000);
    const minutes = Math.floor((milliseconds % 3600000) / 60000);
    const wholeSeconds = Math.floor((milliseconds % 60000) / 1000);
    const remainder = milliseconds % 1000;
    return String(hours).padStart(2, "0") + ":" +
        String(minutes).padStart(2, "0") + ":" +
        String(wholeSeconds).padStart(2, "0") + "." +
        String(remainder).padStart(3, "0");
}

function buildWebVtt(cues) {
    const body = cues
        .filter((cue) => Number.isFinite(cue.start) && Number.isFinite(cue.end) &&
            cue.end > cue.start && cue.text)
        .map((cue) =>
            formatVttTimestamp(cue.start) + " --> " + formatVttTimestamp(cue.end) + "\n" +
            String(cue.text).replace(/\r/g, "").replace(/\u0000/g, ""),
        )
        .join("\n\n");
    return "WEBVTT\n\n" + body + (body ? "\n" : "");
}

function mimeSupportDetails(mimeType) {
    const match = String(mimeType || "").match(/codecs\s*=\s*["']([^"']+)["']/i);
    if (!match || typeof MediaSource === "undefined") {
        return {combined: false, video: [], audio: []};
    }

    const codecs = match[1].split(",").map((codec) => codec.trim()).filter(Boolean);
    const videoCodecs = codecs.filter((codec) =>
        /^(?:avc1|avc3|hev1|hvc1|vp08|vp09|av01|apch|apcn|apcs|apco)/i.test(codec),
    );
    const audioCodecs = codecs.filter((codec) => !videoCodecs.includes(codec));

    const probe = (type, codec) => {
        const mime = type + '/mp4; codecs="' + codec + '"';
        return {
            codec,
            mime,
            supported: MediaSource.isTypeSupported(mime),
        };
    };

    return {
        combined: MediaSource.isTypeSupported(mimeType),
        video: videoCodecs.map((codec) => probe("video", codec)),
        audio: audioCodecs.map((codec) => probe("audio", codec)),
    };
}

function formatMimeSupportDetails(details) {
    const entries = [...details.video, ...details.audio];
    if (!entries.length) return "Individual codec support could not be determined.";
    return entries.map((entry) =>
        (entry.mime.startsWith("video/") ? "Video " : "Audio ") +
        entry.codec + ": " + (entry.supported ? "supported" : "NOT supported"),
    ).join("; ");
}

function videoCodecName(codec) {
    const value = String(codec || "").toLowerCase();
    if (["h264", "avc", "avc1"].includes(value)) return "avc";
    if (["hevc", "h265", "hvc1", "hev1"].includes(value)) return "hevc";
    if (["av1", "av01"].includes(value)) return "av1";
    if (["vp9", "vp09"].includes(value)) return "vp9";
    if (["vp8", "vp08"].includes(value)) return "vp8";
    if (value.includes("prores")) return "prores";
    return null;
}

const AUDIO_SOURCE_CODECS = {
    aac: "aac",
    mp3: "mp3",
    opus: "opus",
    vorbis: "vorbis",
    flac: "flac",
    ac3: "ac3",
    eac3: "eac3",
    dts: "dts",
    truehd: "truehd",
    pcm_s16le: "pcm-s16",
    pcm_s16be: "pcm-s16be",
    pcm_s24le: "pcm-s24",
    pcm_s24be: "pcm-s24be",
    pcm_s32le: "pcm-s32",
    pcm_s32be: "pcm-s32be",
    pcm_f32le: "pcm-f32",
    pcm_f32be: "pcm-f32be",
    pcm_f64le: "pcm-f64",
    pcm_f64be: "pcm-f64be",
};

function audioCodecName(codec) {
    return AUDIO_SOURCE_CODECS[String(codec || "").toLowerCase()] || null;
}

function needsAudioTranscoding(codec) {
    const value = String(codec || "").toLowerCase();
    const mimeCodec = value === "ac3" ? "ac-3" : value === "eac3" ? "ec-3" : null;
    if (!mimeCodec) return false;

    // Preserve the original encoded packets whenever this browser's MSE
    // implementation accepts the source codec. AAC conversion is only a
    // compatibility fallback for browsers that cannot play it directly.
    if (typeof MediaSource === "undefined" || typeof MediaSource.isTypeSupported !== "function") {
        return true;
    }
    return !MediaSource.isTypeSupported('audio/mp4; codecs="' + mimeCodec + '"');
}

function aacDecoderCodecString(stream, config) {
    const candidates = [config?.codec, stream?.codec_string]
        .map((value) => String(value || "").trim())
        .filter(Boolean);

    for (const candidate of candidates) {
        if (/^mp4a\.(?:40\.\d+|67)$/i.test(candidate)) return candidate;
    }

    // web-demuxer can emit the incomplete "mp4a.40" value when the Matroska
    // stream does not expose enough AudioSpecificConfig bytes during probing.
    // Mediabunny requires a fully-qualified AAC string; AAC-LC is the safe
    // baseline when the stream metadata does not specify an object type.
    const description = config?.description || stream?.extradata;
    if (description && description.length >= 2) {
        const first = description[0];
        let objectType = (first >> 3) & 0x1f;
        if (objectType === 31) {
            objectType = 32 + (((first & 0x07) << 3) | (description[1] >> 5));
        }
        if ([2, 5, 29, 42].includes(objectType)) {
            return "mp4a.40." + objectType;
        }
    }

    return "mp4a.40.2";
}

function dtsDecoderCodecString(stream) {
    const profile = [
        stream?.profile,
        stream?.codec_long_name,
        stream?.codec_string,
    ].join(" ").toLowerCase();

    if (/express/.test(profile)) return "dtse";
    if (/dts[- ]?hd|master audio|high resolution|xll/.test(profile)) return "dtsh";
    return "dtsc";
}

function audioDecoderCodecString(stream, sourceCodec, config) {
    if (sourceCodec === "aac") return aacDecoderCodecString(stream, config);
    if (sourceCodec === "ac3") return "ac-3";
    if (sourceCodec === "eac3") return "ec-3";
    if (sourceCodec === "dts") return dtsDecoderCodecString(stream);
    if (sourceCodec === "truehd") return "mlpa";
    return sourceCodec;
}

function audioTrackSupported(stream) {
    const sourceCodec = audioCodecName(stream?.codec_name);
    if (!sourceCodec) return false;
    if (needsAudioTranscoding(stream.codec_name)) return true;
    if (typeof MediaSource === "undefined" || typeof MediaSource.isTypeSupported !== "function") return false;

    const codec = audioDecoderCodecString(stream, sourceCodec, {
        codec: stream.codec_string,
        description: stream.extradata,
    });
    return Boolean(codec && MediaSource.isTypeSupported('audio/mp4; codecs="' + codec + '"'));
}

function languageCode(language) {
    const value = String(language || "").toLowerCase().split(/[-_]/)[0];
    if (/^[a-z]{3}$/.test(value)) return value;
    const map = {
        en:"eng",ja:"jpn",fr:"fra",de:"deu",es:"spa",it:"ita",ko:"kor",zh:"zho",
        ru:"rus",pt:"por",hi:"hin",ar:"ara",nl:"nld",sv:"swe",no:"nor",da:"dan",
        fi:"fin",pl:"pol",tr:"tur",th:"tha",vi:"vie",id:"ind",und:"und",
    };
    return map[value] || "und";
}

function trackLabel(stream) {
    const tags = stream?.tags || {};
    const title = tags.title || tags.handler_name || tags.language || tags.lang || "Unknown";
    return String(title) + " · " + String(stream?.codec_name || "unknown").toUpperCase();
}

function decoderConfig(demuxer, stream, type) {
    const config = demuxer.genDecoderConfig(type, stream);
    if (type === "video") {
        return {
            codec: config.codec,
            codedWidth: config.codedWidth,
            codedHeight: config.codedHeight,
            description: config.description,
        };
    }

    const sourceCodec = audioCodecName(stream?.codec_name);
    if (!sourceCodec) {
        throw new Error("Unsupported audio codec: " + (stream?.codec_name || "unknown"));
    }

    const sampleRate = Number(config.sampleRate || stream.sample_rate);
    const numberOfChannels = Number(config.numberOfChannels || stream.channels);
    if (!Number.isFinite(sampleRate) || sampleRate <= 0 ||
        !Number.isFinite(numberOfChannels) || numberOfChannels <= 0) {
        throw new Error("Audio track is missing a valid sample rate or channel count.");
    }

    return {
        // The packet source uses a short codec ID; decoderConfig needs the
        // codec string defined by the Mediabunny registry.
        codec: audioDecoderCodecString(stream, sourceCodec, config),
        sampleRate,
        numberOfChannels,
        description: config.description,
    };
}

function subtitleText(stream, packet) {
    const codec = String(stream?.codec_name || "").toLowerCase();
    let text = "";

    if (typeof packet?.data === "string") {
        text = packet.data;
    } else {
        let bytes = packet?.data;
        if (bytes instanceof ArrayBuffer) bytes = new Uint8Array(bytes);
        if (!(bytes instanceof Uint8Array)) bytes = new Uint8Array();

        if (codec === "mov_text" && bytes.length >= 2) {
            const length = (bytes[0] << 8) | bytes[1];
            if (length > 0 && length <= bytes.length - 2) {
                bytes = bytes.subarray(2, 2 + length);
            }
        }

        text = new TextDecoder("utf-8", {fatal: false}).decode(bytes);
    }

    text = text.replace(/^\uFEFF/, "").replace(/\u0000/g, "").trim();
    if (!text) return "";

    if (codec === "ass" || codec === "ssa") {
        const dialogue = text.match(/(?:^|\r?\n)Dialogue:\s*([^\r\n]*)/i);
        if (dialogue) {
            const fields = dialogue[1].split(",");
            if (fields.length >= 10) text = fields.slice(9).join(",");
        }
        text = text.replace(/\{[^}]*\}/g, "").replace(/\\[Nn]/g, "\n").replace(/\\h/g, " ");
    } else if (["srt", "subrip", "webvtt"].includes(codec)) {
        const lines = text.replace(/^WEBVTT[^\r\n]*\r?\n/i, "").split(/\r?\n/);
        const timing = lines.findIndex((line) => line.includes("-->"));
        text = timing >= 0
            ? lines.slice(timing + 1).join("\n").trim()
            : lines.filter((line) =>
                !/^\d+$/.test(line.trim()) && !/^\d{2}:\d{2}:\d{2}[,.]\d{3}/.test(line.trim()),
            ).join("\n").trim();
    }

    return text.trim();
}

function isMediaTimeBuffered(media, time) {
    if (!media?.buffered || !Number.isFinite(time)) return false;
    for (let index = 0; index < media.buffered.length; index += 1) {
        if (time >= media.buffered.start(index) - 0.15 &&
            time <= media.buffered.end(index) + 0.15) return true;
    }
    return false;
}

function waitForMediaTimeReady(media, time, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
        let settled = false;
        let timeout = null;
        const events = ["progress", "loadeddata", "canplay", "timeupdate", "seeked", "durationchange"];

        const cleanup = () => {
            for (const name of events) media.removeEventListener(name, check);
            if (timeout !== null) window.clearTimeout(timeout);
        };
        const finish = (error) => {
            if (settled) return;
            settled = true;
            cleanup();
            if (error) reject(error);
            else resolve();
        };
        const check = () => {
            if (media.error) {
                finish(new Error("Media failed while buffering the requested playback position."));
                return;
            }
            if (media.readyState >= 2 && isMediaTimeBuffered(media, time)) finish();
        };

        for (const name of events) media.addEventListener(name, check);
        timeout = window.setTimeout(() => {
            finish(new Error("Timed out waiting for media data at " + time.toFixed(2) + " seconds."));
        }, timeoutMs);
        check();
    });
}

export default function BrowserDemuxPlayer({source, title, active = true, onUseStandardPlayer, onUnavailable}) {
    const videoRef = useRef(null);
    const sessionRef = useRef(null);
    const restartRef = useRef(null);
    const audioTracksRef = useRef([]);
    const selectedAudioRef = useRef("");
    const requestedAudioRef = useRef("");
    const audioSessionRef = useRef(null);
    const audioSessionsRef = useRef(new Map());
    const pendingAudioSessionsRef = useRef(new Map());
    const subtitleDemuxerRef = useRef(null);
    const subtitleCueCacheRef = useRef(new Map());
    const subtitleTaskRef = useRef(0);
    const seekTimerRef = useRef(null);
    const fallbackRef = useRef(false);

    const [audioTracks, setAudioTracks] = useState([]);
    const [subtitleTracks, setSubtitleTracks] = useState([]);
    const [selectedAudio, setSelectedAudio] = useState("");
    const [selectedSubtitle, setSelectedSubtitle] = useState("");
    const [subtitleUrl, setSubtitleUrl] = useState("");
    const [subtitleLabel, setSubtitleLabel] = useState("");
    const [subtitleLanguage, setSubtitleLanguage] = useState("und");
    const [status, setStatus] = useState("Preparing parallel workers…");

    const fallback = useCallback((error) => {
        if (fallbackRef.current) return;
        fallbackRef.current = true;
        if (error) console.error("Parallel browser playback unavailable; using standard playback.", error);
        onUnavailable?.(error);
    }, [onUnavailable]);

    const clearSubtitle = useCallback(() => {
        subtitleTaskRef.current += 1;
        const demuxer = subtitleDemuxerRef.current;
        subtitleDemuxerRef.current = null;
        try { demuxer?.destroy?.(); } catch { /* already closed */ }
        setSubtitleUrl("");
        setSubtitleLabel("");
        setSubtitleLanguage("und");
    }, []);

    useEffect(() => {
        if (!subtitleUrl) return undefined;
        return () => URL.revokeObjectURL(subtitleUrl);
    }, [subtitleUrl]);

    useEffect(() => {
        const video = videoRef.current;
        if (!video || !subtitleUrl) return undefined;

        let trackElement = null;
        const detach = () => {
            if (trackElement) {
                try { trackElement.track.mode = "disabled"; } catch { /* track not initialized */ }
                trackElement.remove();
                trackElement = null;
            }
        };
        const attach = () => {
            detach();
            const currentTrackElement = document.createElement("track");
            trackElement = currentTrackElement;
            currentTrackElement.kind = "subtitles";
            currentTrackElement.label = subtitleLabel || "Subtitles";
            currentTrackElement.srclang = subtitleLanguage || "und";
            currentTrackElement.src = subtitleUrl;
            currentTrackElement.default = true;
            currentTrackElement.addEventListener("load", () => {
                if (trackElement !== currentTrackElement) return;
                currentTrackElement.track.mode = "showing";
                setStatus(
                    "Native WebVTT renderer loaded · " +
                    (currentTrackElement.track.cues?.length || 0) + " cues",
                );
            }, {once: true});
            currentTrackElement.addEventListener("error", () => {
                if (trackElement !== currentTrackElement) return;
                setStatus("The browser rejected the generated WebVTT file.");
                console.error("Native WebVTT track failed to load.", {
                    readyState: currentTrackElement.readyState,
                    src: subtitleUrl,
                });
            }, {once: true});
            video.appendChild(currentTrackElement);
            try { currentTrackElement.track.mode = "showing"; } catch { /* activated by load event */ }
        };

        attach();
        const onLoadStart = () => attach();
        video.addEventListener("loadstart", onLoadStart);
        return () => {
            video.removeEventListener("loadstart", onLoadStart);
            detach();
        };
    }, [subtitleUrl, subtitleLabel, subtitleLanguage]);

    const loadSubtitle = useCallback(async (stream) => {
        clearSubtitle();
        if (!stream) return;

        const taskId = subtitleTaskRef.current;
        let demuxer = null;
        let reader = null;
        setStatus("Demuxing selected subtitle track…");

        try {
            let cues = subtitleCueCacheRef.current.get(String(stream.index));
            if (!cues) {
                const {WebDemuxer, AVMediaType, AVLogLevel} = await import("web-demuxer");
                demuxer = new WebDemuxer({wasmFilePath: DEMUX_WASM_URL});
                subtitleDemuxerRef.current = demuxer;
                await demuxer.load(source);
                await demuxer.setLogLevel(AVLogLevel.AV_LOG_FATAL);
                if (taskId !== subtitleTaskRef.current) return;

                reader = demuxer.readAVPacket(
                    0,
                    0,
                    AVMediaType.AVMEDIA_TYPE_SUBTITLE,
                    stream.index,
                ).getReader();

                const rawCues = [];
                while (true) {
                    const next = await reader.read();
                    if (next.done) break;
                    if (taskId !== subtitleTaskRef.current) return;

                    const packet = next.value;
                    const text = subtitleText(stream, packet);
                    const start = Number(packet?.timestamp);
                    const duration = Number(packet?.duration);
                    if (!text || !Number.isFinite(start)) continue;

                    rawCues.push({
                        start: Math.max(0, start),
                        duration: Number.isFinite(duration) && duration > 0 ? duration : 0,
                        text,
                    });
                }

                rawCues.sort((a, b) => a.start - b.start);
                cues = rawCues.map((cue, index) => {
                    const nextStart = rawCues[index + 1]?.start;
                    const fallbackEnd = Number.isFinite(nextStart) && nextStart > cue.start
                        ? nextStart
                        : cue.start + 3;
                    const end = cue.duration > 0
                        ? cue.start + cue.duration
                        : fallbackEnd;
                    return {
                        start: cue.start,
                        end: Math.max(cue.start + 0.05, end),
                        text: cue.text,
                    };
                });
                subtitleCueCacheRef.current.set(String(stream.index), cues);
            }

            if (taskId !== subtitleTaskRef.current) return;
            if (!cues.length) {
                setStatus("This subtitle stream contains no readable text cues. Bitmap subtitles need a different renderer.");
                return;
            }

            const vtt = buildWebVtt(cues);
            if (!vtt.startsWith("WEBVTT\n") || !vtt.includes(" --> ")) {
                throw new Error("The selected subtitle stream did not produce valid WebVTT cues.");
            }

            const url = URL.createObjectURL(new Blob([vtt], {
                type: "text/vtt;charset=utf-8",
            }));
            setSubtitleLabel(trackLabel(stream));
            setSubtitleLanguage(languageCode(stream.tags?.language));
            setSubtitleUrl(url);
            setStatus("Generated WebVTT · " + cues.length + " cues · attaching native subtitle track");
        } catch (error) {
            if (taskId === subtitleTaskRef.current) {
                console.error("Could not demux the selected subtitle track.", error);
                setStatus(error?.message || "Could not load this subtitle track.");
            }
        } finally {
            try { await reader?.cancel?.(); } catch { /* stream already completed */ }
            try { reader?.releaseLock?.(); } catch { /* reader already released */ }
            demuxer?.destroy?.();
            if (subtitleDemuxerRef.current === demuxer) subtitleDemuxerRef.current = null;
        }
    }, [clearSubtitle, source]);

    const startPipeline = useCallback(async (
        audioIndex,
        startAt = 0,
        shouldPlay = false,
        preparedVideoDemuxer = null,
        knownStreams = null,
        deferPlayback = false,
    ) => {
        const video = videoRef.current;
        if (!video) throw new Error("Video element is unavailable.");

        sessionRef.current?.dispose();
        sessionRef.current = null;

        let resolveTargetBuffered;
        let rejectTargetBuffered;
        let targetBufferedSettled = false;
        const targetBufferedPromise = new Promise((resolve, reject) => {
            resolveTargetBuffered = resolve;
            rejectTargetBuffered = reject;
        });
        targetBufferedPromise.catch(() => {});

        const markTargetBuffered = () => {
            if (targetBufferedSettled) return;
            targetBufferedSettled = true;
            resolveTargetBuffered();
        };
        const rejectTargetBuffer = (error) => {
            if (targetBufferedSettled) return;
            targetBufferedSettled = true;
            rejectTargetBuffered(error);
        };

        const session = {
            cancelled: false,
            demuxers: [],
            worker: null,
            mediaSource: null,
            sourceBuffer: null,
            objectUrl: "",
            queue: [],
            writingId: null,
            pendingEnd: false,
            didStart: false,
            programmaticSeek: false,
            programmaticSeekTarget: null,
            targetBuffered: false,
            quotaRecoveryAttempts: 0,
            forwardBufferSeconds: MAX_FORWARD_BUFFER_SECONDS,
            trimming: false,
            startAt: Math.max(0, Number(startAt) || 0),
            shouldPlay: Boolean(shouldPlay),
            listeners: [],
            rejectStartup: null,
            dispose: null,
            fail: null,
        };

        const onProgrammaticSeeked = () => {
            if (!session.programmaticSeek) return;
            const target = session.programmaticSeekTarget;
            if (target == null || Math.abs(video.currentTime - target) < 0.75) {
                session.programmaticSeek = false;
                session.programmaticSeekTarget = null;
            }
        };
        video.addEventListener("seeked", onProgrammaticSeeked);
        session.listeners.push(["seeked", onProgrammaticSeeked]);

        session.dispose = () => {
            if (session.cancelled) return;
            session.cancelled = true;
            for (const [name, handler] of session.listeners) video.removeEventListener(name, handler);
            for (const demuxer of session.demuxers) {
                try { demuxer.destroy?.(); } catch { /* worker already closed */ }
            }
            if (session.worker) {
                try { session.worker.postMessage({type: "cancel"}); } catch { /* worker already closed */ }
                session.worker.terminate();
            }
            video.pause();
            video.removeAttribute("src");
            video.load();
            if (session.objectUrl) URL.revokeObjectURL(session.objectUrl);
        };

        session.fail = (error) => {
            if (session.cancelled) return;
            session.rejectStartup?.(error);
            rejectTargetBuffer(error);
            console.error("Parallel media pipeline failed.", error);
            session.dispose();
            if (sessionRef.current === session) sessionRef.current = null;
            fallback(error instanceof Error ? error : new Error(String(error)));
        };

        sessionRef.current = session;

        try {
            const {WebDemuxer, AVLogLevel} = await import("web-demuxer");
            if (session.cancelled) return;

            let videoDemuxer = preparedVideoDemuxer;
            let streams = knownStreams;
            if (!videoDemuxer) {
                videoDemuxer = new WebDemuxer({wasmFilePath: DEMUX_WASM_URL});
                session.demuxers.push(videoDemuxer);
                await videoDemuxer.load(source);
                await videoDemuxer.setLogLevel(AVLogLevel.AV_LOG_FATAL);
                streams = await videoDemuxer.getAVStreams();
            } else {
                session.demuxers.push(videoDemuxer);
            }
            if (session.cancelled) return;

            const videoStream = (streams || []).find((item) => item.codec_type_string === "video");
            if (!videoStream) throw new Error("No video stream was found.");
            const videoCodec = videoCodecName(videoStream.codec_name);
            if (!videoCodec) throw new Error("Unsupported video codec: " + videoStream.codec_name);

            let audioStream = null;
            let transcodeAudio = false;
            let videoTrackNumber = null;
            let audioTrackNumber = null;
            if (audioIndex !== null && audioIndex !== "") {
                audioStream = (streams || []).find((item) =>
                    item.codec_type_string === "audio" && item.index === Number(audioIndex),
                );
                if (!audioStream) throw new Error("The selected audio stream is no longer available.");
                if (!audioCodecName(audioStream.codec_name)) {
                    throw new Error("Unsupported audio codec: " + audioStream.codec_name);
                }

                transcodeAudio = needsAudioTranscoding(audioStream.codec_name);
                const orderedAudioStreams = (streams || [])
                    .filter((item) => item.codec_type_string === "audio")
                    .sort((a, b) => a.index - b.index);
                audioTrackNumber = orderedAudioStreams.findIndex((item) => item.index === audioStream.index) + 1;
                if (audioTrackNumber < 1) {
                    throw new Error("Could not map the selected audio stream to its Matroska track number.");
                }
            }
            const orderedVideoStreams = (streams || [])
                .filter((item) => item.codec_type_string === "video")
                .sort((a, b) => a.index - b.index);
            videoTrackNumber = orderedVideoStreams.findIndex((item) => item.index === videoStream.index) + 1;
            if (videoTrackNumber < 1) {
                throw new Error("Could not map the video stream to its Matroska track number.");
            }
            if (session.cancelled) return;

            const videoConfig = decoderConfig(videoDemuxer, videoStream, "video");
            const audioConfig = audioStream && !transcodeAudio
                ? decoderConfig(videoDemuxer, audioStream, "audio")
                : null;

            const mediaSource = new MediaSource();
            session.mediaSource = mediaSource;
            session.objectUrl = URL.createObjectURL(mediaSource);

            let resolveOpen;
            let rejectOpen;
            const sourceOpen = new Promise((resolve, reject) => {
                resolveOpen = resolve;
                rejectOpen = reject;
            });
            let resolveStarted;
            let rejectStarted;
            const workerStarted = new Promise((resolve, reject) => {
                resolveStarted = resolve;
                rejectStarted = reject;
            });
            let resolveReady;
            let rejectReady;
            const workerReady = new Promise((resolve, reject) => {
                resolveReady = resolve;
                rejectReady = reject;
            });
            session.rejectStartup = (error) => {
                rejectOpen(error);
                rejectStarted(error);
                rejectReady(error);
                rejectTargetBuffer(error);
            };

            const worker = new MediaMuxWorker();
            session.worker = worker;

            const finishIfReady = () => {
                const sb = session.sourceBuffer;
                if (!session.pendingEnd || !sb || sb.updating || session.queue.length || session.writingId !== null) return;
                if (!session.targetBuffered) {
                    session.fail(new Error("The video stream ended before the requested seek position was buffered."));
                    return;
                }
                if (mediaSource.readyState === "open") {
                    try { mediaSource.endOfStream(); } catch { /* already ended */ }
                }
            };

            const appendNext = () => {
                const sb = session.sourceBuffer;
                if (session.cancelled || !sb || sb.updating || session.writingId !== null) return;

                if (session.didStart && sb.buffered.length) {
                    const end = sb.buffered.end(sb.buffered.length - 1);
                    if (end - video.currentTime >= session.forwardBufferSeconds) return;
                }

                const next = session.queue.shift();
                if (!next) {
                    finishIfReady();
                    return;
                }

                try {
                    session.writingId = next.id;
                    sb.appendBuffer(next.buffer);
                } catch (error) {
                    session.writingId = null;
                    if (error?.name === "QuotaExceededError") {
                        session.queue.unshift(next);
                        session.quotaRecoveryAttempts += 1;
                        session.forwardBufferSeconds = Math.max(0.5, session.forwardBufferSeconds / 2);
                        setStatus("Reducing the video buffer · playback continues");

                        if (trimOldBuffer(true)) return;
                        if (session.quotaRecoveryAttempts >= 4 &&
                            session.forwardBufferSeconds <= 0.5 &&
                            !sb.buffered.length) {
                            session.fail(new Error("A video fragment is larger than the available browser buffer."));
                        }
                        return;
                    }
                    session.fail(error);
                }
            };

            session.wakeAppend = appendNext;

            const trimOldBuffer = (aggressive = false) => {
                const sb = session.sourceBuffer;
                if (!session.didStart || !sb || sb.updating || session.trimming || !sb.buffered.length) return false;

                const keepBehind = aggressive ? 0.5 : RETAIN_VIDEO_BUFFER_SECONDS;
                const cutoff = Math.max(0, video.currentTime - keepBehind);
                if (cutoff <= 0) return false;

                for (let index = 0; index < sb.buffered.length; index += 1) {
                    const rangeStart = sb.buffered.start(index);
                    const rangeEnd = Math.min(sb.buffered.end(index), cutoff);
                    if (rangeEnd <= rangeStart + 0.25) continue;

                    try {
                        session.trimming = true;
                        sb.remove(rangeStart, rangeEnd);
                        return true;
                    } catch {
                        session.trimming = false;
                        return false;
                    }
                }
                return false;
            };

            let createSourceBuffer = () => {};
            createSourceBuffer = () => {
                if (session.cancelled || !session.opened || !session.mimeType || session.sourceBuffer) return;
                const support = mimeSupportDetails(session.mimeType);
                if (!support.combined) {
                    const individualSupport = formatMimeSupportDetails(support);
                    const unsupported = [...support.video, ...support.audio]
                        .filter((entry) => !entry.supported)
                        .map((entry) => entry.codec);
                    const reason = unsupported.length
                        ? "Unsupported codec(s): " + unsupported.join(", ") + "."
                        : "The codecs may work separately, but this combined audio/video SourceBuffer is not supported.";
                    rejectReady(new Error(
                        "Browser MSE cannot play " + session.mimeType + ". " +
                        reason + " Individual checks: " + individualSupport +
                        " The source must be transcoded to browser-supported codecs for the enhanced player.",
                    ));
                    return;
                }

                try {
                    const sb = mediaSource.addSourceBuffer(session.mimeType);
                    sb.mode = "segments";
                    session.sourceBuffer = sb;
                    const duration = Number(videoStream.duration);
                    if (Number.isFinite(duration) && duration > 0) {
                        try { mediaSource.duration = duration; } catch { /* duration can be inferred from fragments */ }
                    }

                    sb.addEventListener("updateend", () => {
                        if (session.cancelled) return;
                        if (session.writingId !== null) {
                            worker.postMessage({type: "write-ack", id: session.writingId});
                            session.writingId = null;
                            session.quotaRecoveryAttempts = 0;
                            session.forwardBufferSeconds = Math.min(
                                MAX_FORWARD_BUFFER_SECONDS,
                                session.forwardBufferSeconds + 0.5,
                            );
                        }
                        if (session.trimming) session.trimming = false;
                        if (!session.didStart && sb.buffered.length) {
                            session.didStart = true;
                            if (session.startAt > 0 &&
                                Math.abs(video.currentTime - session.startAt) > 0.25) {
                                session.programmaticSeek = true;
                                session.programmaticSeekTarget = session.startAt;
                                try { video.currentTime = session.startAt; } catch { /* target range is still arriving */ }
                            }
                        }

                        if (isMediaTimeBuffered(video, session.startAt)) {
                            session.targetBuffered = true;
                            markTargetBuffered();
                        }

                        trimOldBuffer();
                        appendNext();
                        finishIfReady();
                    });
                    sb.addEventListener("error", () => session.fail(new Error("Browser rejected an MP4 media fragment.")));
                    resolveReady();
                    appendNext();
                } catch (error) {
                    rejectReady(error);
                }
            };

            mediaSource.addEventListener("sourceopen", () => {
                if (session.cancelled) return;
                session.opened = true;
                resolveOpen();
                createSourceBuffer();
            }, {once: true});
            mediaSource.addEventListener("error", () => session.fail(new Error("MediaSource failed to open.")), {once: true});

            worker.addEventListener("message", (event) => {
                const message = event.data;
                if (session.cancelled) return;
                if (message.type === "started") {
                    resolveStarted();
                } else if (message.type === "ready") {
                    session.mimeType = message.mimeType;
                    createSourceBuffer();
                    resolveReady();
                } else if (message.type === "data") {
                    session.queue.push({id: message.id, buffer: message.buffer});
                    appendNext();
                } else if (message.type === "done") {
                    session.pendingEnd = true;
                    if (!session.targetBuffered && !session.queue.length &&
                        !session.sourceBuffer?.updating && session.writingId === null) {
                        session.fail(new Error("The video stream ended before the requested seek position was buffered."));
                        return;
                    }
                    finishIfReady();
                } else if (message.type === "error") {
                    session.fail(new Error(message.message || "Remux worker failed."));
                }
            });
            worker.addEventListener("error", (event) => session.fail(new Error(event.message || "Remux worker crashed.")));
            const initMessage = {
                type: "init",
                source,
                startAt: session.startAt,
                video: {
                    codec: videoCodec,
                    trackNumber: videoTrackNumber,
                    decoderConfig: videoConfig,
                    rotation: videoStream.rotation || 0,
                    flip: Boolean(videoStream.flip),
                },
                audio: null,
            };
            worker.postMessage(initMessage);

            const onVideoError = () => {
                if (session.cancelled) return;
                const code = video.error?.code;
                session.fail(new Error(
                    "The browser could not decode the remuxed media" +
                    (code ? " (media error " + code + ")" : "") +
                    "; switching to the standard player.",
                ));
            };
            video.addEventListener("error", onVideoError);
            session.listeners.push(["error", onVideoError]);

            video.pause();
            video.removeAttribute("src");
            video.src = session.objectUrl;
            video.load();

            // The output MIME type depends on the first packet for codecs such
            // as AC-3/E-AC-3. Start feeding packets after the worker is running,
            // then wait for its MIME type once the priming packet is available.
            const wakeAppend = () => {
                appendNext();
            };
            video.addEventListener("play", wakeAppend);
            video.addEventListener("timeupdate", wakeAppend);
            video.addEventListener("playing", wakeAppend);
            session.listeners.push(
                ["play", wakeAppend],
                ["timeupdate", wakeAppend],
                ["playing", wakeAppend],
            );

            await Promise.all([sourceOpen, workerStarted, workerReady, targetBufferedPromise]);
            if (session.cancelled) return;

            if (session.shouldPlay && !deferPlayback) {
                await video.play().catch((error) => {
                    console.warn("Playback could not resume automatically after seeking.", error);
                });
            }

            setStatus(audioStream
                ? transcodeAudio
                    ? "Range streaming active · AC-3 decoding and AAC encoding in parallel"
                    : "Range streaming active · video and audio packets demuxed progressively"
                : "Range streaming active · video packets demuxed progressively");
        } catch (error) {
            if (!session.cancelled) session.fail(error);
        }
    }, [fallback, source]);

    const startAudioPipeline = useCallback(async (
        track,
        startAt = 0,
        shouldPlay = false,
        activate = true,
        preserveSelectionOnFailure = false,
    ) => {
        const video = videoRef.current;
        if (!video || !track) return null;

        const existing = audioSessionsRef.current.get(track.id);
        if (existing && !existing.cancelled) {
            if (activate) {
                selectedAudioRef.current = track.id;
                audioSessionRef.current = existing;
                setSelectedAudio(track.id);
                for (const [id, item] of audioSessionsRef.current) {
                    item.audio.muted = video.muted || id !== track.id;
                    item.audio.volume = video.volume;
                    if (!video.paused && item.audio.paused && item.audio.readyState >= 2) {
                        item.audio.play().catch(() => {});
                    }
                }
            }
            return existing;
        }

        pendingAudioSessionsRef.current.get(track.id)?.dispose?.();

        const session = {
            cancelled: false,
            ready: false,
            activateWhenReady: Boolean(activate),
            requestedTime: Math.max(0, Number(startAt) || 0),
            track,
            audio: new Audio(),
            worker: new MediaAudioWorker(),
            mediaSource: new MediaSource(),
            sourceBuffer: null,
            objectUrl: "",
            queue: [],
            writingId: null,
            pendingEnd: false,
            didStart: false,
            opened: false,
            mimeType: "",
            startAt: Math.max(0, Number(startAt) || 0),
            trimming: false,
            listeners: [],
            rejectStartup: null,
            dispose: null,
            fail: null,
        };

        const audio = session.audio;
        const worker = session.worker;
        const mediaSource = session.mediaSource;
        audio.preload = "auto";
        audio.volume = video.volume;
        audio.muted = true;
        audio.playbackRate = video.playbackRate;

        session.objectUrl = URL.createObjectURL(mediaSource);
        session.dispose = () => {
            if (session.cancelled) return;
            if (!session.ready) {
                session.rejectStartup?.(new DOMException("Audio pipeline replaced", "AbortError"));
            }
            session.cancelled = true;
            if (pendingAudioSessionsRef.current.get(track.id) === session) {
                pendingAudioSessionsRef.current.delete(track.id);
            }
            if (audioSessionsRef.current.get(track.id) === session) {
                audioSessionsRef.current.delete(track.id);
            }
            if (audioSessionRef.current === session) audioSessionRef.current = null;
            for (const [name, handler] of session.listeners) {
                audio.removeEventListener(name, handler);
            }
            try { worker.postMessage({type: "cancel"}); } catch { /* worker already closed */ }
            worker.terminate();
            audio.pause();
            audio.removeAttribute("src");
            audio.load();
            if (session.objectUrl) URL.revokeObjectURL(session.objectUrl);
        };

        let resolveOpen;
        let rejectOpen;
        const sourceOpen = new Promise((resolve, reject) => {
            resolveOpen = resolve;
            rejectOpen = reject;
        });
        let resolveStarted;
        let rejectStarted;
        const workerStarted = new Promise((resolve, reject) => {
            resolveStarted = resolve;
            rejectStarted = reject;
        });
        let resolveReady;
        let rejectReady;
        const workerReady = new Promise((resolve, reject) => {
            resolveReady = resolve;
            rejectReady = reject;
        });
        let resolveBuffered;
        let rejectBuffered;
        const firstBuffered = new Promise((resolve, reject) => {
            resolveBuffered = resolve;
            rejectBuffered = reject;
        });

        session.rejectStartup = (error) => {
            rejectOpen(error);
            rejectStarted(error);
            rejectReady(error);
            rejectBuffered(error);
        };

        const finishIfReady = () => {
            const sourceBuffer = session.sourceBuffer;
            if (!session.pendingEnd || !sourceBuffer || sourceBuffer.updating ||
                session.queue.length || session.writingId !== null) return;
            if (mediaSource.readyState === "open") {
                try { mediaSource.endOfStream(); } catch { /* already ended */ }
            }
        };

        const appendNext = () => {
            const sourceBuffer = session.sourceBuffer;
            if (session.cancelled || !sourceBuffer || sourceBuffer.updating || session.writingId !== null) return;

            if (session.didStart && sourceBuffer.buffered.length) {
                const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1);
                const playbackTime = Math.max(audio.currentTime || 0, session.startAt);
                if (end - playbackTime >= MAX_FORWARD_BUFFER_SECONDS) return;
            }

            const next = session.queue.shift();
            if (!next) {
                finishIfReady();
                return;
            }

            try {
                session.writingId = next.id;
                sourceBuffer.appendBuffer(next.buffer);
            } catch (error) {
                session.writingId = null;
                session.fail(error);
            }
        };

        const trimOldBuffer = () => {
            const sourceBuffer = session.sourceBuffer;
            const playbackTime = audio.currentTime || 0;
            if (!session.didStart || !sourceBuffer || sourceBuffer.updating ||
                session.trimming || !sourceBuffer.buffered.length || playbackTime < 45) return;

            const start = sourceBuffer.buffered.start(0);
            const end = Math.min(sourceBuffer.buffered.end(0), playbackTime - 30);
            if (end <= start + 1) return;

            try {
                session.trimming = true;
                sourceBuffer.remove(start, end);
            } catch {
                session.trimming = false;
            }
        };

        const createSourceBuffer = () => {
            if (session.cancelled || !session.opened || !session.mimeType || session.sourceBuffer) return;

            if (!MediaSource.isTypeSupported(session.mimeType)) {
                session.fail(new Error(
                    "The browser cannot play the selected audio codec (" + session.mimeType + ").",
                ));
                return;
            }

            try {
                const sourceBuffer = mediaSource.addSourceBuffer(session.mimeType);
                sourceBuffer.mode = "segments";
                session.sourceBuffer = sourceBuffer;

                const duration = Number(track.stream.duration);
                if (Number.isFinite(duration) && duration > 0) {
                    try { mediaSource.duration = duration; } catch { /* duration can be inferred from fragments */ }
                }

                sourceBuffer.addEventListener("updateend", () => {
                    if (session.cancelled) return;

                    if (session.writingId !== null) {
                        worker.postMessage({type: "write-ack", id: session.writingId});
                        session.writingId = null;
                    }
                    if (session.trimming) session.trimming = false;

                    if (!session.didStart && sourceBuffer.buffered.length) {
                        session.didStart = true;
                    }

                    if (isMediaTimeBuffered(audio, session.requestedTime)) {
                        try { audio.currentTime = session.requestedTime; } catch { /* metadata may still be loading */ }
                        resolveBuffered();
                    }

                    trimOldBuffer();
                    appendNext();
                    finishIfReady();
                });

                sourceBuffer.addEventListener("error", () => {
                    session.fail(new Error("The browser rejected the selected audio media fragment."));
                });

                resolveReady();
                appendNext();
            } catch (error) {
                session.fail(error);
            }
        };

        mediaSource.addEventListener("sourceopen", () => {
            if (session.cancelled) return;
            session.opened = true;
            resolveOpen();
            createSourceBuffer();
        }, {once: true});
        mediaSource.addEventListener("error", () => {
            session.fail(new Error("The audio MediaSource failed to open."));
        }, {once: true});

        worker.addEventListener("message", (event) => {
            const message = event.data;
            if (session.cancelled) return;

            if (message.type === "started") {
                resolveStarted();
            } else if (message.type === "ready") {
                session.mimeType = message.mimeType;
                createSourceBuffer();
            } else if (message.type === "data") {
                session.queue.push({id: message.id, buffer: message.buffer});
                appendNext();
            } else if (message.type === "done") {
                session.pendingEnd = true;
                if (!isMediaTimeBuffered(audio, session.requestedTime)) {
                    session.fail(new Error("The audio stream ended before the requested seek position was buffered."));
                    return;
                }
                finishIfReady();
            } else if (message.type === "error") {
                session.fail(new Error(message.message || "Audio mux worker failed."));
            }
        });
        worker.addEventListener("error", (event) => {
            session.fail(new Error(event.message || "Audio mux worker crashed."));
        });

        const wakeAppend = () => appendNext();
        audio.addEventListener("timeupdate", wakeAppend);
        audio.addEventListener("playing", wakeAppend);
        session.listeners.push(["timeupdate", wakeAppend], ["playing", wakeAppend]);

        session.fail = (error) => {
            if (session.cancelled) return;
            session.rejectStartup?.(error);
            console.error("Audio track pipeline failed.", error);
            const wasRequested = requestedAudioRef.current === track.id;
            const activeId = audioSessionRef.current?.track?.id || "";
            session.dispose();

            if (wasRequested) {
                const restoreId = preserveSelectionOnFailure ? track.id : activeId;
                requestedAudioRef.current = restoreId;
                selectedAudioRef.current = restoreId;
                setSelectedAudio(restoreId);
                // Audio-track failures must never tear down the independent video
                // MediaSource or force the whole player into its standard fallback.
                setStatus(error?.message || "Could not prepare the selected audio track.");
            } else {
                console.warn("Could not preload audio track " + track.label, error);
            }
        };

        pendingAudioSessionsRef.current.set(track.id, session);
        audio.src = session.objectUrl;
        audio.load();

        const audioCodec = audioCodecName(track.stream.codec_name);
        const transcodeAudio = needsAudioTranscoding(track.stream.codec_name);
        const orderedAudioStreams = audioTracksRef.current
            .map((item) => item.stream)
            .sort((a, b) => a.index - b.index);
        const audioTrackNumber = orderedAudioStreams.findIndex((item) => item.index === track.index) + 1;

        if (!audioCodec || audioTrackNumber < 1) {
            session.fail(new Error("The selected audio stream cannot be mapped to a supported audio track."));
            return null;
        }

        const sampleRate = Number(track.stream.sample_rate);
        const numberOfChannels = Number(track.stream.channels);
        const audioConfig = transcodeAudio ? null : {
            codec: audioDecoderCodecString(track.stream, audioCodec, {
                codec: track.stream.codec_string,
                description: track.stream.extradata,
            }),
            sampleRate,
            numberOfChannels,
            description: track.stream.extradata,
        };

        if (!transcodeAudio && (!Number.isFinite(sampleRate) || sampleRate <= 0 ||
            !Number.isFinite(numberOfChannels) || numberOfChannels <= 0)) {
            session.fail(new Error("The selected audio track is missing a valid sample rate or channel count."));
            return null;
        }

        try {
            worker.postMessage({
                type: "init",
                source,
                startAt: session.startAt,
                audio: {
                    trackNumber: audioTrackNumber,
                    codec: transcodeAudio ? "aac" : audioCodec,
                    inputCodec: audioCodec,
                    mode: transcodeAudio ? "ac3-to-aac" : "passthrough",
                    decoderConfig: audioConfig,
                    languageCode: languageCode(track.stream.tags?.language),
                    name: track.stream.tags?.title || track.stream.tags?.language || "",
                },
            });

            await Promise.all([sourceOpen, workerStarted, workerReady, firstBuffered]);
            if (session.cancelled || pendingAudioSessionsRef.current.get(track.id) !== session) return null;

            audio.pause();
            try {
                audio.currentTime = session.requestedTime;
                session.wakeAppend?.();
            } catch { /* the next SourceBuffer update retries */ }
            await waitForMediaTimeReady(audio, session.requestedTime);
            if (session.cancelled || pendingAudioSessionsRef.current.get(track.id) !== session) return null;
            audio.volume = video.volume;
            audio.playbackRate = video.playbackRate;

            const previousForTrack = audioSessionsRef.current.get(track.id);
            audioSessionsRef.current.set(track.id, session);
            pendingAudioSessionsRef.current.delete(track.id);
            session.ready = true;

            const shouldActivate = session.activateWhenReady && requestedAudioRef.current === track.id;
            if (shouldActivate) {
                if (!video.paused) {
                    audio.muted = true;
                    await audio.play();
                    if (session.cancelled || requestedAudioRef.current !== track.id) return null;
                } else {
                    audio.pause();
                }

                selectedAudioRef.current = track.id;
                requestedAudioRef.current = track.id;
                setSelectedAudio(track.id);
                audioSessionRef.current = session;
            } else {
                audio.muted = true;
                if (!video.paused && selectedAudioRef.current) {
                    audio.play().catch(() => {});
                } else {
                    audio.pause();
                }
            }

            for (const [id, item] of audioSessionsRef.current) {
                item.audio.muted = video.muted || id !== selectedAudioRef.current;
                item.audio.volume = video.volume;
                item.audio.playbackRate = video.playbackRate;
            }

            if (previousForTrack && previousForTrack !== session) previousForTrack.dispose();

            if (shouldActivate) setStatus("Audio switched · video stream uninterrupted");
            return session;
        } catch (error) {
            if (!session.cancelled) session.fail(error);
            return null;
        }
    }, [fallback, source]);


    useEffect(() => {
        let cancelled = false;
        let metadataDemuxer = null;
        fallbackRef.current = false;
        audioTracksRef.current = [];
        selectedAudioRef.current = "";
        requestedAudioRef.current = "";
        setAudioTracks([]);
        setSubtitleTracks([]);
        setSelectedAudio("");
        setSelectedSubtitle("");
        clearSubtitle();
        setStatus("Opening HTTP range stream…");

        (async () => {
            try {
                const {WebDemuxer, AVLogLevel} = await import("web-demuxer");
                metadataDemuxer = new WebDemuxer({wasmFilePath: DEMUX_WASM_URL});
                await metadataDemuxer.load(source);
                await metadataDemuxer.setLogLevel(AVLogLevel.AV_LOG_FATAL);
                if (cancelled) {
                    metadataDemuxer.destroy();
                    return;
                }

                const streams = await metadataDemuxer.getAVStreams();
                if (cancelled) {
                    metadataDemuxer.destroy();
                    return;
                }

                const videoStream = streams.find((item) => item.codec_type_string === "video");
                if (!videoStream || !videoCodecName(videoStream.codec_name)) {
                    throw new Error("No compatible video stream was found.");
                }

                const audio = streams
                    .filter((item) => item.codec_type_string === "audio")
                    .map((item) => ({
                        id: String(item.index),
                        index: item.index,
                        label: trackLabel(item),
                        stream: item,
                        supported: audioTrackSupported(item),
                    }));
                // Keep only text subtitle formats. PGS/DVD/VobSub are bitmap
                // streams and cannot be rendered by the WebVTT text-track path.
                const subtitles = streams
                    .filter((item) =>
                        item.codec_type_string === "subtitle" &&
                        TEXT_SUBTITLE_CODECS.has(String(item.codec_name || "").toLowerCase()),
                    )
                    .map((item) => ({
                        id: String(item.index),
                        index: item.index,
                        label: trackLabel(item),
                        stream: item,
                        supported: true,
                    }));

                audioTracksRef.current = audio;
                subtitleCueCacheRef.current.clear();
                setAudioTracks(audio);
                setSubtitleTracks(subtitles);
                // Video is independent of audio. If the browser cannot play any
                // embedded audio codec, keep the video running silently instead
                // of falling back and reloading the entire media element.
                const defaultAudio = audio.find((item) => item.supported) || null;
                const audioId = defaultAudio?.id || "";
                selectedAudioRef.current = audioId;
                requestedAudioRef.current = audioId;
                setSelectedAudio(audioId);

                const videoPreparation = startPipeline(null, 0, false, metadataDemuxer, streams);
                const audioPreparation = defaultAudio
                    ? startAudioPipeline(defaultAudio, 0, false, true)
                    : Promise.resolve(null);
                await Promise.all([videoPreparation, audioPreparation]);
                metadataDemuxer = null;
            } catch (error) {
                if (!cancelled) fallback(error instanceof Error ? error : new Error(String(error)));
            }
        })();

        let seekGeneration = 0;
        restartRef.current = (audioIndex, time, shouldPlay) => {
            const target = Math.max(0, Number(time) || 0);
            const generation = ++seekGeneration;
            const selectedId = selectedAudioRef.current;
            const selectedTrack = audioTracksRef.current.find((item) => item.id === selectedId);
            const video = videoRef.current;

            // Freeze both outputs before replacing either MediaSource.
            video?.pause();
            for (const item of audioSessionsRef.current.values()) {
                item.audio.pause();
                item.audio.muted = true;
            }
            for (const item of pendingAudioSessionsRef.current.values()) {
                item.audio.pause();
                item.audio.muted = true;
            }

            for (const item of pendingAudioSessionsRef.current.values()) item.dispose?.();
            pendingAudioSessionsRef.current.clear();
            for (const item of audioSessionsRef.current.values()) item.dispose?.();
            audioSessionsRef.current.clear();
            audioSessionRef.current = null;

            void (async () => {
                try {
                    setStatus("Seeking · demuxing video and selected audio in parallel…");
                    let audioPreparation = Promise.resolve(null);
                    if (selectedTrack?.supported) {
                        requestedAudioRef.current = selectedTrack.id;
                        audioPreparation = startAudioPipeline(selectedTrack, target, false, true, true);
                    }

                    const [videoReady, readyAudio] = await Promise.all([
                        startPipeline(null, target, shouldPlay, null, null, true),
                        audioPreparation,
                    ]);
                    if (generation !== seekGeneration) return;
                    if (selectedTrack?.supported && !readyAudio) {
                        setStatus("Audio could not be prepared at this position. Playback remains paused.");
                        return;
                    }

                    if (generation !== seekGeneration || videoReady === false) return;
                    const currentVideo = videoRef.current;
                    if (!currentVideo || !sessionRef.current || sessionRef.current.cancelled) return;

                    currentVideo.pause();
                    currentVideo.currentTime = target;
                    await waitForMediaTimeReady(currentVideo, target);

                    if (readyAudio) {
                        const currentAudio = readyAudio.audio;
                        currentAudio.pause();
                        currentAudio.currentTime = target;
                        currentAudio.volume = currentVideo.volume;
                        currentAudio.playbackRate = currentVideo.playbackRate;
                        currentAudio.muted = currentVideo.muted;

                        await waitForMediaTimeReady(currentAudio, target);
                        if (generation !== seekGeneration || readyAudio.cancelled) return;

                        selectedAudioRef.current = selectedTrack.id;
                        requestedAudioRef.current = selectedTrack.id;
                        audioSessionRef.current = readyAudio;
                        for (const [id, item] of audioSessionsRef.current) {
                            item.audio.muted = currentVideo.muted || id !== selectedTrack.id;
                            item.audio.volume = currentVideo.volume;
                            item.audio.playbackRate = currentVideo.playbackRate;
                        }

                        if (shouldPlay) {
                            setStatus("Synchronizing audio and video…");
                            const starts = await Promise.allSettled([
                                currentAudio.play(),
                                currentVideo.play(),
                            ]);
                            if (starts.some((result) => result.status === "rejected")) {
                                currentVideo.pause();
                                currentAudio.pause();
                                setStatus("A/V is buffered, but the browser blocked synchronized playback. Press play to retry.");
                                return;
                            }
                        }
                    } else if (shouldPlay) {
                        await currentVideo.play();
                    }

                    if (generation === seekGeneration) {
                        setStatus(shouldPlay
                            ? "Audio and video synchronized · playback resumed"
                            : "Audio and video ready · paused");
                    }
                } catch (error) {
                    if (generation === seekGeneration) {
                        videoRef.current?.pause();
                        for (const item of audioSessionsRef.current.values()) item.audio.pause();
                        setStatus(error?.message || "Could not prepare synchronized playback.");
                    }
                    console.error("Could not complete synchronized seek.", error);
                }
            })();
        };

        return () => {
            cancelled = true;
            restartRef.current = null;
            sessionRef.current?.dispose();
            sessionRef.current = null;
            for (const session of pendingAudioSessionsRef.current.values()) session.dispose?.();
            pendingAudioSessionsRef.current.clear();
            for (const session of audioSessionsRef.current.values()) session.dispose?.();
            audioSessionsRef.current.clear();
            audioSessionRef.current = null;
            selectedAudioRef.current = "";
            requestedAudioRef.current = "";
            subtitleDemuxerRef.current?.destroy?.();
            subtitleDemuxerRef.current = null;
            subtitleCueCacheRef.current.clear();
            metadataDemuxer?.destroy?.();
            clearSubtitle();
        };
    }, [clearSubtitle, fallback, source, startAudioPipeline, startPipeline]);

    const onAudioChange = (event) => {
        const track = audioTracksRef.current.find((item) => item.id === event.target.value);
        const video = videoRef.current;

        if (!track) {
            selectedAudioRef.current = "";
            requestedAudioRef.current = "";
            audioSessionRef.current = null;
            setSelectedAudio("");
            for (const session of audioSessionsRef.current.values()) {
                session.audio.muted = true;
                session.audio.pause();
            }
            setStatus("Audio off");
            return;
        }

        const pendingSession = pendingAudioSessionsRef.current.get(track.id);
        if (pendingSession && !pendingSession.cancelled) {
            // The background preloader may still be near the beginning of the file.
            // Restart only this pending audio stream at the current playback time so
            // it does not have to append every packet between 0 and the playhead.
            const time = Math.max(0, Number(video?.currentTime) || 0);
            const wasPlaying = Boolean(video && !video.paused);
            requestedAudioRef.current = track.id;
            setSelectedAudio(track.id);
            setStatus("Preparing selected audio at the current position · video keeps playing");
            void startAudioPipeline(track, time, wasPlaying, true);
            return;
        }

        const readySession = audioSessionsRef.current.get(track.id);
        if (readySession && !readySession.cancelled) {
            const targetTime = Math.max(0, Number(video?.currentTime) || 0);
            const audio = readySession.audio;

            // "Ready" means the first fragment arrived, not necessarily that this
            // track has buffered the current playhead. Restart it at the playhead
            // rather than making it append the entire earlier section of the file.
            if (!isMediaTimeBuffered(audio, targetTime)) {
                const wasPlaying = Boolean(video && !video.paused);
                requestedAudioRef.current = track.id;
                setSelectedAudio(track.id);
                setStatus("Preparing selected audio at the current position · video keeps playing");
                readySession.dispose?.();
                void startAudioPipeline(track, targetTime, wasPlaying, true);
                return;
            }

            if (Math.abs(audio.currentTime - targetTime) > 0.2) {
                try { audio.currentTime = targetTime; } catch { /* the next sync tick retries */ }
            }
            audio.playbackRate = video?.playbackRate || 1;
            audio.volume = video?.volume ?? 1;
            audio.muted = true;

            // Start the new audio element while muted. Only switch the mute states
            // after play() succeeds, so a failed element never interrupts the old one.
            const activate = () => {
                if (readySession.cancelled || requestedAudioRef.current !== track.id) return;
                selectedAudioRef.current = track.id;
                audioSessionRef.current = readySession;
                setSelectedAudio(track.id);
                for (const [id, session] of audioSessionsRef.current) {
                    session.audio.muted = Boolean(video?.muted) || id !== track.id;
                    if (video) {
                        session.audio.volume = video.volume;
                        session.audio.playbackRate = video.playbackRate;
                    }
                }
                setStatus("Audio switched · video stream uninterrupted");
            };

            requestedAudioRef.current = track.id;
            if (video && !video.paused) {
                audio.play().then(activate).catch((error) => {
                    requestedAudioRef.current = selectedAudioRef.current;
                    setSelectedAudio(selectedAudioRef.current);
                    console.warn("Could not activate the selected audio stream.", error);
                    setStatus("Could not activate that audio track; the previous audio remains active.");
                });
            } else {
                activate();
            }
            return;
        }

        const time = video?.currentTime || 0;
        const wasPlaying = Boolean(video && !video.paused);
        requestedAudioRef.current = track.id;
        setSelectedAudio(track.id);
        setStatus("Preparing this audio track · video keeps playing");
        void startAudioPipeline(track, time, wasPlaying, true);
    };

    const onSubtitleChange = (event) => {
        const id = event.target.value;
        setSelectedSubtitle(id);
        if (!id) {
            clearSubtitle();
            setStatus("Subtitles off");
            return;
        }
        const track = subtitleTracks.find((item) => item.id === id);
        if (track?.supported) void loadSubtitle(track.stream);
    };

    useEffect(() => {
        const video = videoRef.current;
        if (!video) return undefined;

        const syncAudio = () => {
            const shouldPlay = !video.paused && !video.ended;
            for (const [id, session] of audioSessionsRef.current) {
                if (session.cancelled) continue;
                const audio = session.audio;
                audio.volume = video.volume;
                audio.muted = video.muted || id !== selectedAudioRef.current;
                audio.playbackRate = video.playbackRate;

                if (!shouldPlay || id !== selectedAudioRef.current) {
                    audio.pause();
                    continue;
                }

                const drift = Math.abs(audio.currentTime - video.currentTime);
                if (drift > 0.35 && video.readyState >= 2) {
                    try {
                        audio.currentTime = video.currentTime;
                        session.wakeAppend?.();
                    } catch { /* audio stream is buffering */ }
                }
                if (audio.paused && audio.readyState >= 2) {
                    audio.play().catch((error) => {
                        console.warn("Could not synchronize an audio stream.", error);
                    });
                }
            }
        };

        const onVolumeChange = () => {
            for (const [id, session] of audioSessionsRef.current) {
                session.audio.volume = video.volume;
                session.audio.muted = video.muted || id !== selectedAudioRef.current;
            }
        };

        video.addEventListener("play", syncAudio);
        video.addEventListener("playing", syncAudio);
        video.addEventListener("pause", syncAudio);
        video.addEventListener("timeupdate", syncAudio);
        video.addEventListener("ratechange", syncAudio);
        video.addEventListener("volumechange", onVolumeChange);
        if (!active) video.pause();

        return () => {
            video.removeEventListener("play", syncAudio);
            video.removeEventListener("playing", syncAudio);
            video.removeEventListener("pause", syncAudio);
            video.removeEventListener("timeupdate", syncAudio);
            video.removeEventListener("ratechange", syncAudio);
            video.removeEventListener("volumechange", onVolumeChange);
        };
    }, [active]);

    useEffect(() => {
        const video = videoRef.current;
        if (!video) return undefined;
        if (!active) video.pause();

        let pendingSeek = null;

        const finishSeek = async (time, wasPlaying) => {
            const session = sessionRef.current;
            if (!session || session.cancelled || session.programmaticSeek) return;

            if (!isMediaTimeBuffered(video, time)) {
                restartRef.current?.(null, time, wasPlaying);
                return;
            }

            const selectedId = selectedAudioRef.current;
            const selectedTrack = audioTracksRef.current.find((item) => item.id === selectedId);
            if (!selectedTrack?.supported) {
                video.currentTime = time;
                await waitForMediaTimeReady(video, time);
                if (wasPlaying) await video.play();
                return;
            }

            let selectedSession = audioSessionsRef.current.get(selectedId);
            if (!selectedSession || selectedSession.cancelled ||
                !isMediaTimeBuffered(selectedSession.audio, time)) {
                selectedSession?.dispose?.();
                setStatus("Seeking · loading selected audio at the new position…");
                requestedAudioRef.current = selectedId;
                selectedSession = await startAudioPipeline(selectedTrack, time, false, true, true);
            }

            if (!selectedSession || selectedSession.cancelled) {
                video.pause();
                setStatus("Audio could not be prepared at this position. Playback remains paused.");
                return;
            }

            video.pause();
            selectedSession.audio.pause();
            video.currentTime = time;
            selectedSession.audio.currentTime = time;
            selectedSession.audio.volume = video.volume;
            selectedSession.audio.playbackRate = video.playbackRate;
            selectedSession.audio.muted = video.muted;

            await Promise.all([
                waitForMediaTimeReady(video, time),
                waitForMediaTimeReady(selectedSession.audio, time),
            ]);
            if (selectedSession.cancelled || sessionRef.current !== session) return;

            selectedAudioRef.current = selectedId;
            requestedAudioRef.current = selectedId;
            audioSessionRef.current = selectedSession;
            for (const [id, item] of audioSessionsRef.current) {
                item.audio.muted = video.muted || id !== selectedId;
                item.audio.volume = video.volume;
                item.audio.playbackRate = video.playbackRate;
            }

            if (wasPlaying) {
                setStatus("Synchronizing audio and video…");
                const starts = await Promise.allSettled([
                    selectedSession.audio.play(),
                    video.play(),
                ]);
                if (starts.some((result) => result.status === "rejected")) {
                    video.pause();
                    selectedSession.audio.pause();
                    setStatus("A/V is buffered, but the browser blocked synchronized playback. Press play to retry.");
                    return;
                }
                setStatus("Audio and video synchronized · playback resumed");
            } else {
                setStatus("Audio and video ready · paused");
            }
        };

        const onSeeking = () => {
            const session = sessionRef.current;
            if (!session || session.cancelled || session.programmaticSeek) return;
            if (!pendingSeek) {
                pendingSeek = {wasPlaying: !video.paused};
                video.pause();
                for (const item of audioSessionsRef.current.values()) item.audio.pause();
                for (const item of pendingAudioSessionsRef.current.values()) item.audio.pause();
            }

            if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
            seekTimerRef.current = window.setTimeout(() => {
                if (!pendingSeek || !video.seeking) return;
                const {wasPlaying} = pendingSeek;
                const time = Math.max(0, Number(video.currentTime) || 0);
                if (!isMediaTimeBuffered(video, time)) {
                    pendingSeek = null;
                    restartRef.current?.(null, time, wasPlaying);
                }
            }, 220);
        };

        const onSeeked = () => {
            if (!pendingSeek) return;
            if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
            seekTimerRef.current = null;
            const {wasPlaying} = pendingSeek;
            pendingSeek = null;
            const time = Math.max(0, Number(video.currentTime) || 0);
            void finishSeek(time, wasPlaying).catch((error) => {
                video.pause();
                for (const item of audioSessionsRef.current.values()) item.audio.pause();
                console.error("Could not synchronize playback after seeking.", error);
                setStatus(error?.message || "Could not synchronize audio and video after seeking.");
            });
        };

        video.addEventListener("seeking", onSeeking);
        video.addEventListener("seeked", onSeeked);
        return () => {
            video.removeEventListener("seeking", onSeeking);
            video.removeEventListener("seeked", onSeeked);
            if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
        };
    }, [active, startAudioPipeline]);

    useEffect(() => () => {
        if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
        for (const session of pendingAudioSessionsRef.current.values()) session.dispose?.();
        for (const session of audioSessionsRef.current.values()) session.dispose?.();
        pendingAudioSessionsRef.current.clear();
        audioSessionsRef.current.clear();
        audioSessionRef.current = null;
    }, []);

    return (
        <div className="relative flex h-full min-h-0 w-full flex-col bg-black">
            <div className="absolute left-3 top-3 z-20 flex flex-wrap items-center gap-2">
                <span className="rounded-md border border-[#3ecf8e]/40 bg-[#062a1d]/90 px-2.5 py-1.5 text-[11px] font-medium text-[#8af0bd] shadow-lg backdrop-blur">
                    HTTP Range · progressive demux
                </span>
                {audioTracks.length > 0 && (
                    <label className="flex items-center gap-1.5 rounded-md border border-white/15 bg-black/75 px-2 py-1 text-[11px] text-white shadow-lg backdrop-blur">
                        <span className="text-white/65">Audio</span>
                        <select value={selectedAudio} onChange={onAudioChange} className="max-w-[180px] bg-transparent text-white outline-none" aria-label="Select audio track">
                            <option value="" className="bg-[#151515]">No audio</option>
                            {audioTracks.map((track) => (
                                <option key={track.id} value={track.id} disabled={!track.supported} className="bg-[#151515]">
                                    {track.label}{track.supported ? "" : " (unsupported)"}
                                </option>
                            ))}
                        </select>
                    </label>
                )}
                {subtitleTracks.length > 0 && (
                    <label className="flex items-center gap-1.5 rounded-md border border-white/15 bg-black/75 px-2 py-1 text-[11px] text-white shadow-lg backdrop-blur">
                        <span className="text-white/65">Subtitles</span>
                        <select value={selectedSubtitle} onChange={onSubtitleChange} className="max-w-[180px] bg-transparent text-white outline-none" aria-label="Select subtitle track">
                            <option value="" className="bg-[#151515]">Off</option>
                            {subtitleTracks.map((track) => (
                                <option key={track.id} value={track.id} disabled={!track.supported} className="bg-[#151515]">
                                    {track.label}{track.supported ? "" : " (unsupported)"}
                                </option>
                            ))}
                        </select>
                    </label>
                )}
            </div>

            <video ref={videoRef} controls playsInline preload="auto" className="h-full min-h-[240px] w-full bg-black object-contain" aria-label={title || "Video"} />

            <div className="pointer-events-none absolute bottom-14 left-3 rounded bg-black/65 px-2 py-1 text-[11px] text-white/75">{status}</div>

            <button
                type="button"
                onClick={onUseStandardPlayer}
                title="Return to the standard StoreLimitless player"
                className="absolute right-3 top-3 z-20 rounded-md border border-white/20 bg-black/75 px-2.5 py-1.5 text-[11px] font-medium text-white/90 shadow-lg backdrop-blur transition-colors hover:bg-black/95 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
            >
                Standard player
            </button>
        </div>
    );
}
