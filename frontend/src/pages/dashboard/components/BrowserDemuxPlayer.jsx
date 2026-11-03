import {useCallback, useEffect, useRef, useState} from "react";
import MediaMuxWorker from "./MediaMuxWorker.js?worker";
import MediaAudioWorker from "./MediaAudioWorker.js?worker";

const DEMUX_WASM_URL = "https://cdn.jsdelivr.net/npm/web-demuxer@4.0.0/dist/wasm-files/web-demuxer.wasm";
const MAX_FORWARD_BUFFER_SECONDS = 12;
const TEXT_SUBTITLE_CODECS = new Set(["subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"]);

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
    return ["ac3", "eac3"].includes(String(codec || "").toLowerCase());
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
    let bytes = packet.data;

    if (codec === "mov_text" && bytes?.length >= 2) {
        const length = (bytes[0] << 8) | bytes[1];
        if (length > 0 && length <= bytes.length - 2) bytes = bytes.subarray(2, 2 + length);
    }

    let text = new TextDecoder("utf-8").decode(bytes || new Uint8Array())
        .replace(/\u0000/g, "")
        .trim();
    if (!text) return "";

    if (codec === "ass" || codec === "ssa") {
        const dialogue = text.match(/Dialogue:\s*(.*)/i);
        const fields = (dialogue ? dialogue[1] : text).split(",");
        if (fields.length >= 10) text = fields.slice(9).join(",");
        text = text.replace(/\{[^}]*\}/g, "").replace(/\\[Nn]/g, "\n").replace(/\\h/g, " ");
    } else if (["srt", "subrip", "webvtt"].includes(codec)) {
        const lines = text.split(/\r?\n/);
        const timing = lines.findIndex((line) => line.includes("-->"));
        text = timing >= 0
            ? lines.slice(timing + 1).join("\n").trim()
            : lines.filter((line) => !/^\d+$/.test(line.trim())).join("\n").trim();
    }

    return text;
}

export default function BrowserDemuxPlayer({source, title, active = true, onUseStandardPlayer, onUnavailable}) {
    const videoRef = useRef(null);
    const sessionRef = useRef(null);
    const restartRef = useRef(null);
    const audioTracksRef = useRef([]);
    const selectedAudioRef = useRef("");
    const audioSessionRef = useRef(null);
    const pendingAudioSessionRef = useRef(null);
    const subtitleDemuxerRef = useRef(null);
    const subtitleTaskRef = useRef(0);
    const subtitleTrackRef = useRef(null);
    const seekTimerRef = useRef(null);
    const fallbackRef = useRef(false);

    const [audioTracks, setAudioTracks] = useState([]);
    const [subtitleTracks, setSubtitleTracks] = useState([]);
    const [selectedAudio, setSelectedAudio] = useState("");
    const [selectedSubtitle, setSelectedSubtitle] = useState("");
    const [status, setStatus] = useState("Preparing parallel workers…");

    const fallback = useCallback((error) => {
        if (fallbackRef.current) return;
        fallbackRef.current = true;
        if (error) console.error("Parallel browser playback unavailable; using standard playback.", error);
        onUnavailable?.(error);
    }, [onUnavailable]);

    const clearSubtitle = useCallback(() => {
        subtitleTaskRef.current += 1;
        subtitleDemuxerRef.current?.destroy?.();
        subtitleDemuxerRef.current = null;

        const activeTrack = subtitleTrackRef.current;
        if (activeTrack) {
            activeTrack.mode = "disabled";
            if (activeTrack.cues) {
                for (let index = activeTrack.cues.length - 1; index >= 0; index -= 1) {
                    activeTrack.removeCue(activeTrack.cues[index]);
                }
            }
            subtitleTrackRef.current = null;
        }
    }, []);

    const loadSubtitle = useCallback(async (stream) => {
        clearSubtitle();
        if (!stream) return;

        const video = videoRef.current;
        if (!video || typeof video.addTextTrack !== "function" || typeof VTTCue === "undefined") {
            setStatus("This browser cannot render embedded text subtitles.");
            return;
        }

        const taskId = subtitleTaskRef.current;
        const textTrack = video.addTextTrack(
            "subtitles",
            trackLabel(stream),
            languageCode(stream.tags?.language),
        );
        textTrack.mode = "showing";
        subtitleTrackRef.current = textTrack;
        setStatus("Loading subtitle cues…");

        let demuxer = null;
        let cueCount = 0;
        try {
            const {WebDemuxer, AVMediaType, AVLogLevel} = await import("web-demuxer");
            demuxer = new WebDemuxer({wasmFilePath: DEMUX_WASM_URL});
            subtitleDemuxerRef.current = demuxer;
            await demuxer.load(source);
            await demuxer.setLogLevel(AVLogLevel.AV_LOG_FATAL);
            if (taskId !== subtitleTaskRef.current) return;

            const reader = demuxer.readAVPacket(
                0,
                0,
                AVMediaType.AVMEDIA_TYPE_SUBTITLE,
                stream.index,
            ).getReader();

            try {
                while (taskId === subtitleTaskRef.current) {
                    const next = await reader.read();
                    if (next.done) break;

                    const packet = next.value;
                    const text = subtitleText(stream, packet);
                    if (!text) continue;

                    // Web-Demuxer packet timestamps and durations are already in seconds.
                    const start = Math.max(0, Number(packet.timestamp) || 0);
                    const duration = Number(packet.duration);
                    const end = start + (Number.isFinite(duration) && duration > 0 ? duration : 3);
                    if (end <= start) continue;

                    try {
                        textTrack.addCue(new VTTCue(start, end, text));
                        cueCount += 1;
                    } catch (error) {
                        console.warn("Skipping an invalid subtitle cue.", error);
                    }
                }
            } finally {
                try { reader.releaseLock?.(); } catch { /* already closed */ }
            }

            if (taskId === subtitleTaskRef.current) {
                if (cueCount > 0) {
                    textTrack.mode = "showing";
                    setStatus("Subtitles active · " + cueCount + " cues");
                } else {
                    textTrack.mode = "disabled";
                    setStatus("No readable subtitle cues were found in this track.");
                }
            }
        } catch (error) {
            if (taskId === subtitleTaskRef.current) {
                textTrack.mode = "disabled";
                console.warn("Subtitle extraction failed.", error);
                setStatus(error?.message || "Could not read this subtitle track.");
            }
        } finally {
            demuxer?.destroy?.();
            if (subtitleDemuxerRef.current === demuxer) subtitleDemuxerRef.current = null;
        }
    }, [clearSubtitle, source]);

    const startPipeline = useCallback(async (audioIndex, startAt = 0, shouldPlay = false, preparedVideoDemuxer = null, knownStreams = null) => {
        const video = videoRef.current;
        if (!video) throw new Error("Video element is unavailable.");

        sessionRef.current?.dispose();
        sessionRef.current = null;

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
            trimming: false,
            startAt: Math.max(0, Number(startAt) || 0),
            shouldPlay: Boolean(shouldPlay),
            listeners: [],
            rejectStartup: null,
            dispose: null,
            fail: null,
        };

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
            };

            const worker = new MediaMuxWorker();
            session.worker = worker;

            const finishIfReady = () => {
                const sb = session.sourceBuffer;
                if (!session.pendingEnd || !sb || sb.updating || session.queue.length || session.writingId !== null) return;
                if (mediaSource.readyState === "open") {
                    try { mediaSource.endOfStream(); } catch { /* already ended */ }
                }
            };

            const appendNext = () => {
                const sb = session.sourceBuffer;
                if (session.cancelled || !sb || sb.updating || session.writingId !== null) return;
                if (session.didStart && sb.buffered.length) {
                    const end = sb.buffered.end(sb.buffered.length - 1);
                    if (end - video.currentTime >= MAX_FORWARD_BUFFER_SECONDS) return;
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
                    session.fail(error);
                }
            };

            const trimOldBuffer = () => {
                const sb = session.sourceBuffer;
                if (!session.didStart || !sb || sb.updating || session.trimming || !sb.buffered.length || video.currentTime < 45) return;
                const start = sb.buffered.start(0);
                const end = Math.min(sb.buffered.end(0), video.currentTime - 30);
                if (end <= start + 1) return;
                try {
                    session.trimming = true;
                    sb.remove(start, end);
                } catch {
                    session.trimming = false;
                }
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
                        }
                        if (session.trimming) session.trimming = false;

                        if (!session.didStart && sb.buffered.length) {
                            session.didStart = true;
                            if (session.startAt > 0) {
                                session.programmaticSeek = true;
                                try { video.currentTime = session.startAt; } catch { /* first range not ready */ }
                                window.setTimeout(() => { session.programmaticSeek = false; }, 700);
                            }
                            if (session.shouldPlay) video.play().catch(() => {});
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
            await Promise.all([sourceOpen, workerStarted]);
            if (session.cancelled) return;

            await workerReady;
            if (session.cancelled) return;

            const wakeAppend = () => appendNext();
            video.addEventListener("timeupdate", wakeAppend);
            video.addEventListener("playing", wakeAppend);
            session.listeners.push(["timeupdate", wakeAppend], ["playing", wakeAppend]);

            setStatus(audioStream
                ? transcodeAudio
                    ? "Range streaming active · AC-3 decoding and AAC encoding in parallel"
                    : "Range streaming active · video and audio packets demuxed progressively"
                : "Range streaming active · video packets demuxed progressively");
        } catch (error) {
            if (!session.cancelled) session.fail(error);
        }
    }, [fallback, source]);

    const startAudioPipeline = useCallback(async (track, startAt = 0, shouldPlay = false) => {
        const video = videoRef.current;
        if (!video || !track) return;

        pendingAudioSessionRef.current?.dispose?.();

        const session = {
            cancelled: false,
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
        audio.volume = 0;
        audio.muted = video.muted;
        audio.playbackRate = video.playbackRate;

        session.objectUrl = URL.createObjectURL(mediaSource);
        session.dispose = () => {
            if (session.cancelled) return;
            session.cancelled = true;
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
                const error = new Error(
                    "The browser cannot play the selected audio codec (" + session.mimeType + ").",
                );
                session.fail(error);
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
                        try { audio.currentTime = session.startAt; } catch { /* metadata may still be loading */ }
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
            session.dispose();
            if (pendingAudioSessionRef.current === session) pendingAudioSessionRef.current = null;
            if (!audioSessionRef.current) fallback(error);
            else setStatus(error?.message || "Could not switch to the selected audio track.");
        };

        pendingAudioSessionRef.current = session;
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
            return;
        }

        const audioConfig = transcodeAudio ? null : {
            codec: audioDecoderCodecString(track.stream, audioCodec, {
                codec: track.stream.codec_string,
                description: track.stream.extradata,
            }),
            sampleRate: Number(track.stream.sample_rate),
            numberOfChannels: Number(track.stream.channels),
            description: track.stream.extradata,
        };

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
            if (session.cancelled || pendingAudioSessionRef.current !== session) return;

            try { audio.currentTime = session.startAt; } catch { /* first range establishes the timeline */ }
            audio.muted = video.muted;
            audio.playbackRate = video.playbackRate;

            const previous = audioSessionRef.current;
            if (shouldPlay && !video.paused) {
                audio.volume = 0;
                await audio.play();
                if (session.cancelled || pendingAudioSessionRef.current !== session) return;
            } else {
                audio.pause();
                audio.volume = video.volume;
            }

            audioSessionRef.current = session;
            pendingAudioSessionRef.current = null;

            if (shouldPlay && !video.paused) {
                const targetVolume = video.muted ? 0 : video.volume;
                const fadeStart = performance.now();
                const fadeDuration = 120;
                const fade = (now) => {
                    if (session.cancelled) return;
                    const progress = Math.min(1, (now - fadeStart) / fadeDuration);
                    audio.volume = targetVolume * progress;
                    if (previous && !previous.cancelled) {
                        previous.audio.volume = targetVolume * (1 - progress);
                    }
                    if (progress < 1) {
                        requestAnimationFrame(fade);
                    } else {
                        previous?.dispose?.();
                    }
                };
                requestAnimationFrame(fade);
            } else {
                previous?.dispose?.();
            }

            setStatus("Audio track active · video stream uninterrupted");
        } catch (error) {
            if (!session.cancelled) {
                session.fail(error);
                const activeId = audioSessionRef.current?.track?.id || "";
                if (selectedAudioRef.current === track.id) {
                    selectedAudioRef.current = activeId;
                    setSelectedAudio(activeId);
                }
            }
        }
    }, [fallback, source]);


    useEffect(() => {
        let cancelled = false;
        let metadataDemuxer = null;
        fallbackRef.current = false;
        audioTracksRef.current = [];
        selectedAudioRef.current = "";
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
                        supported: Boolean(audioCodecName(item.codec_name)),
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
                setAudioTracks(audio);
                setSubtitleTracks(subtitles);

                const defaultAudio = audio.find((item) => item.supported) || null;
                if (audio.length > 0 && !defaultAudio) {
                    const detectedCodecs = [...new Set(audio.map((track) =>
                        String(track.stream.codec_name || "unknown"),
                    ))].join(", ");
                    throw new Error(
                        "No supported audio track was found. Detected audio codecs: " + detectedCodecs +
                        ". The browser player supports AAC, MP3, Opus, Vorbis, FLAC, AC-3, E-AC-3, DTS, TrueHD and PCM; switching to the standard player.",
                    );
                }
                const audioId = defaultAudio?.id || "";
                selectedAudioRef.current = audioId;
                setSelectedAudio(audioId);

                await startPipeline(null, 0, false, metadataDemuxer, streams);
                metadataDemuxer = null;
                if (defaultAudio) void startAudioPipeline(defaultAudio, 0, false);
            } catch (error) {
                if (!cancelled) fallback(error instanceof Error ? error : new Error(String(error)));
            }
        })();

        restartRef.current = (audioIndex, time, shouldPlay) => {
            void startPipeline(null, time, shouldPlay);
        };

        return () => {
            cancelled = true;
            restartRef.current = null;
            sessionRef.current?.dispose();
            sessionRef.current = null;
            metadataDemuxer?.destroy?.();
            clearSubtitle();
        };
    }, [clearSubtitle, fallback, source, startPipeline]);

    const onAudioChange = (event) => {
        const track = audioTracksRef.current.find((item) => item.id === event.target.value);
        if (!track) {
            pendingAudioSessionRef.current?.dispose?.();
            pendingAudioSessionRef.current = null;
            audioSessionRef.current?.dispose?.();
            audioSessionRef.current = null;
            selectedAudioRef.current = "";
            setSelectedAudio("");
            setStatus("Audio off");
            return;
        }

        const video = videoRef.current;
        const time = video?.currentTime || 0;
        const wasPlaying = Boolean(video && !video.paused);
        selectedAudioRef.current = track.id;
        setSelectedAudio(track.id);
        setStatus("Preparing audio track · video keeps playing");
        void startAudioPipeline(track, time, wasPlaying);
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
            const session = audioSessionRef.current;
            if (!session || session.cancelled) return;
            const audio = session.audio;

            audio.muted = video.muted;
            audio.volume = video.muted ? 0 : video.volume;
            audio.playbackRate = video.playbackRate;

            const drift = Math.abs(audio.currentTime - video.currentTime);
            if (drift > 0.35 && video.readyState >= 2) {
                try { audio.currentTime = video.currentTime; } catch { /* audio stream is buffering */ }
            }

            if (video.paused || video.ended) {
                audio.pause();
            } else if (audio.paused && audio.readyState >= 2) {
                audio.play().catch((error) => {
                    console.warn("Could not resume the selected audio track.", error);
                });
            }
        };

        const onVolumeChange = () => {
            const sessions = [audioSessionRef.current, pendingAudioSessionRef.current];
            for (const session of sessions) {
                if (!session || session.cancelled) continue;
                session.audio.muted = video.muted;
                session.audio.volume = video.muted ? 0 : video.volume;
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

        const onSeeking = () => {
            const session = sessionRef.current;
            if (!session || session.cancelled || session.programmaticSeek) return;
            const time = video.currentTime;
            const wasPlaying = !video.paused;

            const activeAudio = audioSessionRef.current;
            if (activeAudio && !activeAudio.cancelled) {
                let audioBuffered = false;
                for (let index = 0; index < activeAudio.audio.buffered.length; index += 1) {
                    if (time >= activeAudio.audio.buffered.start(index) - 0.15 &&
                        time <= activeAudio.audio.buffered.end(index) + 0.15) {
                        audioBuffered = true;
                        break;
                    }
                }

                if (audioBuffered) {
                    try { activeAudio.audio.currentTime = time; } catch { /* browser is updating the timeline */ }
                } else {
                    const selectedTrack = audioTracksRef.current.find((item) => item.id === selectedAudioRef.current);
                    if (selectedTrack) void startAudioPipeline(selectedTrack, time, wasPlaying);
                }
            }

            for (let index = 0; index < video.buffered.length; index += 1) {
                if (time >= video.buffered.start(index) - 0.15 && time <= video.buffered.end(index) + 0.15) return;
            }
            if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
            seekTimerRef.current = window.setTimeout(() => {
                restartRef.current?.(null, time, wasPlaying);
            }, 120);
        };

        video.addEventListener("seeking", onSeeking);
        return () => video.removeEventListener("seeking", onSeeking);
    }, [active, startAudioPipeline]);

    useEffect(() => () => {
        if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
        pendingAudioSessionRef.current?.dispose?.();
        audioSessionRef.current?.dispose?.();
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

            <video ref={videoRef} controls playsInline preload="auto" className="h-full min-h-[240px] w-full bg-black object-contain" aria-label={title || "Video"}>
            </video>

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
