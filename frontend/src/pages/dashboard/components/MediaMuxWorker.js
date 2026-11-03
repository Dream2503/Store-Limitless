import {
    ALL_FORMATS,
    AppendOnlyStreamTarget,
    AudioSampleSink,
    AudioSampleSource,
    EncodedAudioPacketSource,
    EncodedPacket,
    EncodedVideoPacketSource,
    Input,
    Mp4OutputFormat,
    Output,
    Quality,
    UrlSource,
} from "mediabunny";

let output = null;
let videoSource = null;
let audioSource = null;
let audioCodec = null;
let audioDecoderConfig = null;
let audioMode = "passthrough";
let audioInput = null;
let audioIterator = null;
let firstAudioSample = null;
let trackChains = {video: Promise.resolve(), audio: Promise.resolve()};
let packetSequence = {video: 0, audio: 0};
let pendingWrites = new Map();
let pendingWriteBytes = 0;
const MAX_PENDING_OUTPUT_BYTES = 64 * 1024 * 1024;
const outputCapacityWaiters = [];
let nextWriteId = 1;
let closedTracks = new Set();
let expectedTracks = 0;
let audioPrimed = false;
let finalizing = false;
let failed = false;

async function prepareAc3Audio(message) {
    const [{registerAc3Decoder}, {registerAacEncoder}] = await Promise.all([
        import("@mediabunny/ac3"),
        import("@mediabunny/aac-encoder"),
    ]);
    registerAc3Decoder();
    registerAacEncoder();

    audioInput = new Input({
        formats: ALL_FORMATS,
        source: new UrlSource(message.source),
    });

    const tracks = await audioInput.getAudioTracks();
    const trackNumber = Number(message.trackNumber);
    const track = tracks.find((item) => item.number === trackNumber);
    if (!track) {
        throw new Error("Could not find selected audio track " + trackNumber + " in the Matroska source.");
    }

    const codec = await track.getCodec();
    if (!["ac3", "eac3"].includes(String(codec || "").toLowerCase())) {
        throw new Error("Selected source track is not AC-3/E-AC-3: " + (codec || "unknown"));
    }

    const sink = new AudioSampleSink(track);
    audioIterator = sink.samples(Number(message.startAt) || 0)[Symbol.asyncIterator]();
    const first = await audioIterator.next();
    if (first.done || !first.value) {
        throw new Error("The selected AC-3 track did not produce any decodable audio samples.");
    }
    firstAudioSample = first.value;

    audioSource = new AudioSampleSource({
        codec: "aac",
        quality: new Quality({bitrate: 160000}),
        transform: {
            numberOfChannels: 2,
            sampleRate: 48000,
        },
    });
}

async function pumpAc3Audio() {
    try {
        while (!failed && audioIterator) {
            const result = await audioIterator.next();
            if (result.done) break;

            const sample = result.value;
            try {
                await audioSource.add(sample);
            } finally {
                sample.close();
            }
        }

        if (failed) return;
        audioSource.close();
        closedTracks.add("audio");
        void finalizeWhenReady();
    } catch (error) {
        reportError(error);
    } finally {
        try { audioInput?.dispose(); } catch { /* input already closed */ }
        audioInput = null;
        audioIterator = null;
    }
}

function reportError(error) {
    if (failed) return;
    failed = true;
    const message = error instanceof Error ? error.message : String(error);
    self.postMessage({type: "error", message});
    for (const waiter of outputCapacityWaiters) waiter.reject(new Error(message));
    outputCapacityWaiters.length = 0;
    pendingWrites.clear();
    pendingWriteBytes = 0;

    try { audioInput?.dispose(); } catch { /* input already closed */ }
    audioInput = null;
    try { firstAudioSample?.close(); } catch { /* sample already closed */ }
    firstAudioSample = null;

    try {
        const canceled = output?.cancel?.();
        if (canceled?.catch) void canceled.catch(() => {});
    } catch {
        // Output may already be finalizing.
    }
}

async function initialize(message) {
    try {
        expectedTracks = message.audio ? 2 : 1;
        videoSource = new EncodedVideoPacketSource(message.video.codec);
        if (message.audio) {
            audioMode = message.audio.mode || "passthrough";
            audioCodec = message.audio.codec;
            audioDecoderConfig = message.audio.decoderConfig;
            audioPrimed = false;

            if (audioMode === "ac3-to-aac") {
                await prepareAc3Audio(message.audio);
            } else {
                audioSource = new EncodedAudioPacketSource(audioCodec);
            }
        }

        const target = new AppendOnlyStreamTarget(new WritableStream({
            write(data) {
                const copy = data.slice();
                const id = nextWriteId++;
                pendingWriteBytes += copy.byteLength;
                pendingWrites.set(id, {byteLength: copy.byteLength});
                self.postMessage({type: "data", id, buffer: copy.buffer}, [copy.buffer]);

                // Keep remuxing while MSE appends previous fragments. Backpressure
                // only when the bounded output queue reaches its memory budget.
                if (pendingWriteBytes < MAX_PENDING_OUTPUT_BYTES) return;

                return new Promise((resolve, reject) => {
                    outputCapacityWaiters.push({resolve, reject});
                });
            },
        }));

        output = new Output({
            format: new Mp4OutputFormat({fastStart: "fragmented", minimumFragmentDuration: 0.5}),
            target,
        });
        output.addVideoTrack(videoSource, {
            decoderConfig: message.video.decoderConfig,
            rotation: message.video.rotation || 0,
            flip: Boolean(message.video.flip),
        });
        if (audioSource && message.audio) {
            if (audioMode === "ac3-to-aac") {
                // Decoded samples are re-encoded as browser-compatible AAC.
                // No AC-3 priming packet or AC-3 sample entry reaches MSE.
                output.addAudioTrack(audioSource, {
                    languageCode: message.audio.languageCode || "und",
                    name: message.audio.name || "",
                });
            } else {
                const priming = message.audio.primingPacket;
                if (!priming?.buffer || !Number.isFinite(priming.timestamp)) {
                    throw new Error("The audio track did not receive a valid priming packet.");
                }
                const primingPacket = new EncodedPacket(
                    new Uint8Array(priming.buffer),
                    "key",
                    priming.timestamp,
                    Number.isFinite(priming.duration) && priming.duration > 0 ? priming.duration : 0.02,
                    0,
                );
                output.addAudioTrack(audioSource, {
                    decoderConfig: audioDecoderConfig,
                    primingPacket,
                    languageCode: message.audio.languageCode || "und",
                    name: message.audio.name || "",
                });
            }
        }

        await output.start();
        if (audioMode === "ac3-to-aac") {
            if (!firstAudioSample) throw new Error("AC-3 decoder did not yield its initial sample.");
            await audioSource.add(firstAudioSample);
            firstAudioSample.close();
            firstAudioSample = null;
        }
        self.postMessage({type: "started"});
        void output.getMimeType().then((mimeType) => {
            self.postMessage({type: "ready", mimeType});
        }).catch(reportError);

        if (audioMode === "ac3-to-aac") {
            void pumpAc3Audio();
        }
    } catch (error) {
        reportError(error);
    }
}

function addPacketBatch(message) {
    if (failed || !output) return;
    const track = message.track;
    if (track !== "video" && track !== "audio") return;
    if (track === "audio" && audioMode === "ac3-to-aac") {
        self.postMessage({type: "batch-ack", id: message.id});
        return;
    }
    const source = track === "video" ? videoSource : audioSource;
    if (!source) {
        self.postMessage({type: "batch-ack", id: message.id});
        return;
    }

    trackChains[track] = trackChains[track].then(async () => {
        for (const item of message.packets) {
            const timestamp = Number(item.timestamp);
            if (!Number.isFinite(timestamp)) continue;
            const duration = Number(item.duration);
            const packetData = new Uint8Array(item.buffer);
            let packetType = track === "video" ? (item.keyframe ? "key" : "delta") : "key";
            if (track === "audio" && audioCodec === "truehd") {
                // TrueHD marks major-sync access units as key packets. The
                // four-byte sync word follows the four-byte access-unit header.
                const hasMajorSync = packetData.length >= 8 &&
                    packetData[4] === 0xf8 &&
                    packetData[5] === 0x72 &&
                    packetData[6] === 0x6f &&
                    packetData[7] === 0xba;
                packetType = hasMajorSync ? "key" : "delta";
            }
            const packet = new EncodedPacket(
                packetData,
                packetType,
                timestamp,
                Number.isFinite(duration) && duration > 0 ? duration : (track === "video" ? 1 / 30 : 0.02),
                packetSequence[track]++,
            );
            if (track === "audio" && !audioPrimed) {
                // Passing metadata with the first packet lets Mediabunny use
                // that packet as the priming packet required by AC-3/E-AC-3
                // and DTS sample-entry generation.
                await source.add(packet, {decoderConfig: audioDecoderConfig});
                audioPrimed = true;
            } else {
                await source.add(packet);
            }
        }
        self.postMessage({type: "batch-ack", id: message.id});
    }).catch(reportError);
}

async function finalizeWhenReady() {
    if (failed || finalizing || closedTracks.size < expectedTracks) return;
    finalizing = true;

    try {
        await Promise.all([trackChains.video, trackChains.audio]);
        videoSource?.close();
        audioSource?.close();
        await output.finalize();
        self.postMessage({type: "done"});
    } catch (error) {
        reportError(error);
    }
}

self.addEventListener("message", (event) => {
    const message = event.data;
    if (message.type === "init") {
        void initialize(message);
    } else if (message.type === "packet-batch") {
        addPacketBatch(message);
    } else if (message.type === "end-track") {
        closedTracks.add(message.track);
        void finalizeWhenReady();
    } else if (message.type === "write-ack") {
        const pending = pendingWrites.get(message.id);
        if (pending) {
            pendingWrites.delete(message.id);
            pendingWriteBytes = Math.max(0, pendingWriteBytes - pending.byteLength);

            if (pendingWriteBytes < MAX_PENDING_OUTPUT_BYTES && outputCapacityWaiters.length) {
                const waiters = outputCapacityWaiters.splice(0);
                for (const waiter of waiters) waiter.resolve();
            }
        }
    } else if (message.type === "cancel") {
        failed = true;
        for (const waiter of outputCapacityWaiters) waiter.reject(new DOMException("Mux cancelled", "AbortError"));
        outputCapacityWaiters.length = 0;
        pendingWrites.clear();
        pendingWriteBytes = 0;
        try { audioInput?.dispose(); } catch { /* input already closed */ }
        audioInput = null;
        try { firstAudioSample?.close(); } catch { /* sample already closed */ }
        firstAudioSample = null;
        try { void audioIterator?.return?.(); } catch { /* iterator already closed */ }
        audioIterator = null;
        try {
            const canceled = output?.cancel?.();
            if (canceled?.catch) void canceled.catch(() => {});
        } catch {
            // Output may already be closing.
        }
        self.close();
    }
});
