import {useCallback, useEffect, useRef, useState} from "react";
import MediaMuxWorker from "./MediaMuxWorker.js?worker";

const DEMUX_WASM_URL = "https://cdn.jsdelivr.net/npm/web-demuxer@4.0.0/dist/wasm-files/web-demuxer.wasm";
const MAX_FORWARD_BUFFER_SECONDS = 35;
const MAX_IN_FLIGHT_BATCHES = Math.max(
    3,
    Math.min(10, Math.floor((typeof navigator !== "undefined" ? navigator.hardwareConcurrency : 8) * 0.75)),
);
const TEXT_SUBTITLE_CODECS = new Set(["subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"]);

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
    pcm_u8: "pcm-u8",
    pcm_s8: "pcm-s8",
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
    pcm_mulaw: "ulaw",
    pcm_alaw: "alaw",
};

function audioCodecName(codec) {
    return AUDIO_SOURCE_CODECS[String(codec || "").toLowerCase()] || null;
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

function vttTime(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    const s = Math.floor(value % 60);
    const ms = Math.floor((value - Math.floor(value)) * 1000);
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" +
        String(s).padStart(2, "0") + "." + String(ms).padStart(3, "0");
}

function subtitleText(stream, packet) {
    const codec = String(stream?.codec_name || "").toLowerCase();
    let bytes = packet.data;

    if (codec === "mov_text" && bytes?.length >= 2) {
        const length = (bytes[0] << 8) | bytes[1];
        if (length > 0 && length <= bytes.length - 2) bytes = bytes.subarray(2, 2 + length);
    }

    let text = new TextDecoder("utf-8").decode(bytes || new Uint8Array()).replace(/\u0000/g, "").trim();
    if (!text) return "";

    if (codec === "ass" || codec === "ssa") {
        const dialogue = text.match(/Dialogue:\s*(.*)/i);
        const fields = (dialogue ? dialogue[1] : text).split(",");
        if (fields.length >= 10) text = fields.slice(9).join(",");
        text = text.replace(/\{[^}]*\}/g, "").replace(/\\[Nn]/g, "\n").replace(/\\h/g, " ");
    } else if (["srt", "subrip", "webvtt"].includes(codec)) {
        const lines = text.split(/\r?\n/);
        const timing = lines.findIndex((line) => line.includes("-->"));
        text = timing >= 0 ? lines.slice(timing + 1).join("\n").trim()
            : lines.filter((line) => !/^\d+$/.test(line.trim())).join("\n").trim();
    }

    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
}

function makeWebVtt(stream, packets) {
    const cues = [];
    for (const packet of packets) {
        const text = subtitleText(stream, packet);
        if (!text) continue;
        const start = Math.max(0, Number(packet.timestamp) || 0);
        const duration = Number(packet.duration);
        const end = start + (Number.isFinite(duration) && duration > 0 ? duration : 3);
        cues.push(vttTime(start) + " --> " + vttTime(Math.max(start + 0.25, end)) + "\n" + text);
    }
    return "WEBVTT\n\n" + cues.join("\n\n") + "\n";
}

export default function BrowserDemuxPlayer({source, title, active = true, onUseStandardPlayer, onUnavailable}) {
    const videoRef = useRef(null);
    const sessionRef = useRef(null);
    const restartRef = useRef(null);
    const audioTracksRef = useRef([]);
    const selectedAudioRef = useRef("");
    const subtitleDemuxerRef = useRef(null);
    const subtitleTaskRef = useRef(0);
    const subtitleUrlRef = useRef("");
    const seekTimerRef = useRef(null);
    const fallbackRef = useRef(false);

    const [audioTracks, setAudioTracks] = useState([]);
    const [subtitleTracks, setSubtitleTracks] = useState([]);
    const [selectedAudio, setSelectedAudio] = useState("");
    const [selectedSubtitle, setSelectedSubtitle] = useState("");
    const [subtitleUrl, setSubtitleUrl] = useState("");
    const [subtitleLabel, setSubtitleLabel] = useState("");
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
        const oldUrl = subtitleUrlRef.current;
        subtitleUrlRef.current = "";
        setSubtitleUrl("");
        setSubtitleLabel("");
        if (oldUrl) URL.revokeObjectURL(oldUrl);
    }, []);

    const loadSubtitle = useCallback(async (stream) => {
        clearSubtitle();
        if (!stream) return;
        if (!TEXT_SUBTITLE_CODECS.has(String(stream.codec_name || "").toLowerCase())) {
            setStatus("Image-based subtitles are not supported by the browser caption renderer.");
            return;
        }

        const taskId = subtitleTaskRef.current;
        setStatus("Extracting subtitle cues in a worker…");
        try {
            const {WebDemuxer, AVMediaType, AVLogLevel} = await import("web-demuxer");
            const demuxer = new WebDemuxer({wasmFilePath: DEMUX_WASM_URL});
            subtitleDemuxerRef.current = demuxer;
            await demuxer.load(source);
            await demuxer.setLogLevel(AVLogLevel.AV_LOG_FATAL);
            if (taskId !== subtitleTaskRef.current) {
                demuxer.destroy();
                return;
            }

            const reader = demuxer.readAVPacket(0, 0, AVMediaType.AVMEDIA_TYPE_SUBTITLE, stream.index).getReader();
            const packets = [];
            try {
                while (taskId === subtitleTaskRef.current) {
                    const next = await reader.read();
                    if (next.done) break;
                    packets.push(next.value);
                }
            } finally {
                try { reader.releaseLock?.(); } catch { /* already closed */ }
            }

            demuxer.destroy();
            if (subtitleDemuxerRef.current === demuxer) subtitleDemuxerRef.current = null;
            if (taskId !== subtitleTaskRef.current) return;

            const vtt = makeWebVtt(stream, packets);
            if (!vtt.includes("-->")) throw new Error("No readable cues were found in this subtitle stream.");
            const url = URL.createObjectURL(new Blob([vtt], {type: "text/vtt"}));
            subtitleUrlRef.current = url;
            setSubtitleUrl(url);
            setSubtitleLabel(trackLabel(stream));
            setStatus("Parallel worker playback active");
        } catch (error) {
            if (taskId === subtitleTaskRef.current) {
                console.warn("Subtitle extraction failed.", error);
                setStatus(error?.message || "Could not read this subtitle track.");
            }
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
            readers: [],
            worker: null,
            mediaSource: null,
            sourceBuffer: null,
            objectUrl: "",
            queue: [],
            packetAcks: new Map(),
            nextPacketId: 1,
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
            for (const reader of session.readers) {
                try { void reader.cancel(); } catch { /* stream already closed */ }
            }
            for (const demuxer of session.demuxers) {
                try { demuxer.destroy?.(); } catch { /* worker already closed */ }
            }
            for (const waiter of session.packetAcks.values()) {
                waiter.reject(new DOMException("Playback replaced", "AbortError"));
            }
            session.packetAcks.clear();
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
            const {WebDemuxer, AVMediaType, AVLogLevel} = await import("web-demuxer");
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
            let audioDemuxer = null;
            if (audioIndex !== null && audioIndex !== "") {
                audioStream = (streams || []).find((item) =>
                    item.codec_type_string === "audio" && item.index === Number(audioIndex),
                );
                if (!audioStream) throw new Error("The selected audio stream is no longer available.");
                if (!audioCodecName(audioStream.codec_name)) {
                    throw new Error("Unsupported audio codec: " + audioStream.codec_name);
                }

                audioDemuxer = new WebDemuxer({wasmFilePath: DEMUX_WASM_URL});
                session.demuxers.push(audioDemuxer);
                await audioDemuxer.load(source);
                await audioDemuxer.setLogLevel(AVLogLevel.AV_LOG_FATAL);
            }
            if (session.cancelled) return;

            const videoConfig = decoderConfig(videoDemuxer, videoStream, "video");
            const audioConfig = audioStream ? decoderConfig(audioDemuxer, audioStream, "audio") : null;
            const mediaSource = new MediaSource();
            session.mediaSource = mediaSource;
            session.objectUrl = URL.createObjectURL(mediaSource);

            let resolveOpen;
            let rejectOpen;
            const sourceOpen = new Promise((resolve, reject) => {
                resolveOpen = resolve;
                rejectOpen = reject;
            });
            let resolveReady;
            let rejectReady;
            const workerReady = new Promise((resolve, reject) => {
                resolveReady = resolve;
                rejectReady = reject;
            });
            session.rejectStartup = (error) => {
                rejectOpen(error);
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
                if (!MediaSource.isTypeSupported(session.mimeType)) {
                    rejectReady(new Error("Browser MSE does not support: " + session.mimeType));
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
                if (message.type === "ready") {
                    session.mimeType = message.mimeType;
                    createSourceBuffer();
                } else if (message.type === "batch-ack") {
                    const waiter = session.packetAcks.get(message.id);
                    if (waiter) {
                        session.packetAcks.delete(message.id);
                        waiter.resolve();
                    }
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
            worker.postMessage({
                type: "init",
                video: {
                    codec: videoCodec,
                    decoderConfig: videoConfig,
                    rotation: videoStream.rotation || 0,
                    flip: Boolean(videoStream.flip),
                },
                audio: audioStream ? {
                    codec: audioCodecName(audioStream.codec_name),
                    decoderConfig: audioConfig,
                    languageCode: languageCode(audioStream.tags?.language),
                    name: audioStream.tags?.title || audioStream.tags?.language || "",
                } : null,
            });

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

            await Promise.all([sourceOpen, workerReady]);
            if (session.cancelled) return;

            const pump = async (demuxer, track, streamIndex, mediaType) => {
                const reader = demuxer.readAVPacket(session.startAt, 0, mediaType, streamIndex).getReader();
                session.readers.push(reader);
                const batch = [];
                const inFlightBatches = new Set();
                let batchBytes = 0;
                let batchError = null;

                const flushBatch = async () => {
                    if (!batch.length) return;
                    if (batchError) throw batchError;

                    const id = session.nextPacketId++;
                    const items = batch.splice(0);
                    batchBytes = 0;
                    const transfers = [];
                    const packets = items.map((packet) => {
                        const bytes = packet.data;
                        const buffer = (
                            bytes.buffer instanceof ArrayBuffer &&
                            bytes.byteOffset === 0 &&
                            bytes.byteLength === bytes.buffer.byteLength
                        ) ? bytes.buffer : bytes.slice().buffer;
                        transfers.push(buffer);
                        return {
                            buffer,
                            timestamp: packet.timestamp,
                            duration: packet.duration,
                            keyframe: packet.keyframe === 1,
                        };
                    });

                    const ack = new Promise((resolve, reject) => session.packetAcks.set(id, {resolve, reject}));
                    try {
                        worker.postMessage({type: "packet-batch", id, track, packets}, transfers);
                    } catch (error) {
                        session.packetAcks.delete(id);
                        throw error;
                    }

                    // Keep a bounded number of packet batches moving through the
                    // muxer instead of waiting after every batch for an MSE append.
                    let completion;
                    completion = ack.then(
                        () => null,
                        (error) => {
                            batchError ||= error;
                            return error;
                        },
                    );
                    inFlightBatches.add(completion);
                    void completion.then(() => inFlightBatches.delete(completion));

                    if (inFlightBatches.size >= MAX_IN_FLIGHT_BATCHES) {
                        const error = await Promise.race(inFlightBatches);
                        if (error) throw error;
                    }
                };

                try {
                    while (!session.cancelled && sessionRef.current === session) {
                        if (batchError) throw batchError;
                        const result = await reader.read();
                        if (result.done) break;
                        const packet = result.value;
                        if (!packet?.data?.byteLength || !Number.isFinite(packet.timestamp)) continue;

                        batch.push(packet);
                        batchBytes += packet.data.byteLength;
                        if (batch.length >= 24 || batchBytes >= 2 * 1024 * 1024) {
                            await flushBatch();
                        }
                    }

                    await flushBatch();
                    if (inFlightBatches.size) await Promise.all(inFlightBatches);
                    if (batchError) throw batchError;

                    if (!session.cancelled && sessionRef.current === session) {
                        worker.postMessage({type: "end-track", track});
                    }
                } finally {
                    try { reader.releaseLock?.(); } catch { /* already closed */ }
                }
            };

            const videoPump = pump(videoDemuxer, "video", videoStream.index, AVMediaType.AVMEDIA_TYPE_VIDEO);
            const audioPump = audioStream
                ? pump(audioDemuxer, "audio", audioStream.index, AVMediaType.AVMEDIA_TYPE_AUDIO)
                : Promise.resolve();

            void Promise.all([videoPump, audioPump]).catch((error) => {
                if (!session.cancelled) session.fail(error);
            });

            const wakeAppend = () => appendNext();
            video.addEventListener("timeupdate", wakeAppend);
            video.addEventListener("playing", wakeAppend);
            session.listeners.push(["timeupdate", wakeAppend], ["playing", wakeAppend]);

            setStatus(audioStream
                ? `Video/audio demux workers active · up to ${MAX_IN_FLIGHT_BATCHES} in-flight batches per track`
                : `Video demux worker active · up to ${MAX_IN_FLIGHT_BATCHES} in-flight batches`);
        } catch (error) {
            if (!session.cancelled) session.fail(error);
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
        setStatus("Preparing separate FFmpeg/WASM workers…");

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

                await startPipeline(defaultAudio?.index ?? null, 0, false, metadataDemuxer, streams);
                metadataDemuxer = null;
            } catch (error) {
                if (!cancelled) fallback(error instanceof Error ? error : new Error(String(error)));
            }
        })();

        restartRef.current = (audioIndex, time, shouldPlay) => {
            void startPipeline(audioIndex, time, shouldPlay);
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
        const video = videoRef.current;
        const time = video?.currentTime || 0;
        const wasPlaying = Boolean(video && !video.paused);
        selectedAudioRef.current = track?.id || "";
        setSelectedAudio(track?.id || "");
        restartRef.current?.(track?.index ?? null, time, wasPlaying);
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
        if (!active) video.pause();

        const onSeeking = () => {
            const session = sessionRef.current;
            if (!session || session.cancelled || session.programmaticSeek) return;
            const time = video.currentTime;
            for (let index = 0; index < video.buffered.length; index += 1) {
                if (time >= video.buffered.start(index) - 0.15 && time <= video.buffered.end(index) + 0.15) return;
            }
            if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
            const wasPlaying = !video.paused;
            seekTimerRef.current = window.setTimeout(() => {
                restartRef.current?.(selectedAudioRef.current, time, wasPlaying);
            }, 120);
        };

        video.addEventListener("seeking", onSeeking);
        return () => video.removeEventListener("seeking", onSeeking);
    }, [active]);

    useEffect(() => () => {
        if (seekTimerRef.current) window.clearTimeout(seekTimerRef.current);
        if (subtitleUrlRef.current) URL.revokeObjectURL(subtitleUrlRef.current);
    }, []);

    const workerCount = 2 + (selectedAudio ? 1 : 0) + (selectedSubtitle ? 1 : 0);

    return (
        <div className="relative flex h-full min-h-0 w-full flex-col bg-black">
            <div className="absolute left-3 top-3 z-20 flex flex-wrap items-center gap-2">
                <span className="rounded-md border border-[#3ecf8e]/40 bg-[#062a1d]/90 px-2.5 py-1.5 text-[11px] font-medium text-[#8af0bd] shadow-lg backdrop-blur">
                    {workerCount} workers · {MAX_IN_FLIGHT_BATCHES} batches/track
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
                {subtitleUrl && (
                    <track
                        key={subtitleUrl}
                        kind="subtitles"
                        src={subtitleUrl}
                        srcLang="und"
                        label={subtitleLabel || "Embedded subtitles"}
                        default
                        onLoad={(event) => {
                            try { event.currentTarget.track.mode = "showing"; } catch { /* browser has not attached it */ }
                        }}
                    />
                )}
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
