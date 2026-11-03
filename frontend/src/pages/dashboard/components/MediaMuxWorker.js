import {
    ALL_FORMATS,
    AppendOnlyStreamTarget,
    AudioSampleSink,
    AudioSampleSource,
    EncodedAudioPacketSource,
    EncodedPacketSink,
    EncodedVideoPacketSource,
    Input,
    Mp4OutputFormat,
    Quality,
    UrlSource,
} from "mediabunny";

let input = null;
let output = null;
let videoSource = null;
let audioSource = null;
let pendingWrites = new Map();
let pendingWriteBytes = 0;
const MAX_PENDING_OUTPUT_BYTES = 8 * 1024 * 1024;
const outputCapacityWaiters = [];
let nextWriteId = 1;
let closedTracks = new Set();
let expectedTracks = 0;
let finalizing = false;
let failed = false;
let audioIterator = null;

function reportError(error) {
    if (failed) return;
    failed = true;
    const message = error instanceof Error ? error.message : String(error);
    self.postMessage({type: "error", message});

    for (const waiter of outputCapacityWaiters) waiter.reject(new Error(message));
    outputCapacityWaiters.length = 0;
    pendingWrites.clear();
    pendingWriteBytes = 0;

    try { input?.dispose(); } catch { /* input already disposed */ }
    input = null;
    try { void audioIterator?.return?.(); } catch { /* iterator already closed */ }
    audioIterator = null;

    try {
        const canceled = output?.cancel?.();
        if (canceled?.catch) void canceled.catch(() => {});
    } catch {
        // Output may already be finalizing.
    }
}

async function finalizeWhenReady() {
    if (failed || finalizing || closedTracks.size < expectedTracks) return;
    finalizing = true;

    try {
        await output.finalize();
        self.postMessage({type: "done"});
    } catch (error) {
        reportError(error);
    } finally {
        try { input?.dispose(); } catch { /* input already disposed */ }
        input = null;
    }
}

async function pumpVideoPackets(sink, startPacket) {
    try {
        for await (const packet of sink.packets(startPacket)) {
            if (failed) break;
            await videoSource.add(packet);
        }
    } catch (error) {
        reportError(error);
        return;
    }

    if (failed) return;
    videoSource.close();
    closedTracks.add("video");
    void finalizeWhenReady();
}

async function pumpEncodedAudioPackets(sink, startPacket) {
    try {
        for await (const packet of sink.packets(startPacket)) {
            if (failed) break;
            await audioSource.add(packet);
        }
    } catch (error) {
        reportError(error);
        return;
    }

    if (failed) return;
    audioSource.close();
    closedTracks.add("audio");
    void finalizeWhenReady();
}

async function pumpDecodedAudioSamples() {
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
    } catch (error) {
        reportError(error);
        return;
    }

    if (failed) return;
    audioSource.close();
    closedTracks.add("audio");
    void finalizeWhenReady();
}

async function initialize(message) {
    try {
        expectedTracks = message.audio ? 2 : 1;

        // One shared range source feeds both sinks. Mediabunny prefetches
        // packet data based on consumer speed and keeps the cache bounded.
        input = new Input({
            formats: ALL_FORMATS,
            source: new UrlSource(message.source, {
                maxCacheSize: 8 * 1024 * 1024,
                parallelism: 4,
            }),
        });

        const videoTracks = await input.getVideoTracks();
        const videoTrack = videoTracks.find((track) => track.number === Number(message.video.trackNumber));
        if (!videoTrack) {
            throw new Error("Could not find video track " + message.video.trackNumber + " in the source.");
        }

        const videoSink = new EncodedPacketSink(videoTrack);
        const startAt = Math.max(0, Number(message.startAt) || 0);
        let firstVideoPacket = startAt > 0
            ? await videoSink.getKeyPacket(startAt)
            : await videoSink.getFirstKeyPacket();
        if (!firstVideoPacket) firstVideoPacket = await videoSink.getFirstPacket();
        if (!firstVideoPacket) throw new Error("The video track contains no readable packets.");

        videoSource = new EncodedVideoPacketSource(message.video.codec);

        let audioSink = null;
        let firstAudioPacket = null;
        let audioTrackForDecode = null;
        let audioMode = "passthrough";

        if (message.audio) {
            const audioTracks = await input.getAudioTracks();
            const audioTrack = audioTracks.find((track) => track.number === Number(message.audio.trackNumber));
            if (!audioTrack) {
                throw new Error("Could not find audio track " + message.audio.trackNumber + " in the source.");
            }

            audioMode = message.audio.mode || "passthrough";
            const sourceCodec = String(await audioTrack.getCodec() || "").toLowerCase();
            const expectedCodec = String(message.audio.inputCodec || "").toLowerCase();
            if (expectedCodec && sourceCodec && expectedCodec !== sourceCodec) {
                throw new Error(
                    "Selected audio track changed between metadata scan and streaming input (" +
                    expectedCodec + " -> " + sourceCodec + ").",
                );
            }

            if (audioMode === "ac3-to-aac") {
                // Register the AAC output track immediately. Loading the WASM
                // decoder is deferred until after video packet pumping begins.
                audioTrackForDecode = audioTrack;
                audioSource = new AudioSampleSource({
                    codec: "aac",
                    fullCodecString: "mp4a.40.2",
                    quality: new Quality({bitrate: 160000}),
                    transform: {
                        numberOfChannels: 2,
                        sampleRate: 48000,
                    },
                });
            } else {
                audioSink = new EncodedPacketSink(audioTrack);
                firstAudioPacket = startAt > 0
                    ? await audioSink.getPacket(startAt)
                    : await audioSink.getFirstPacket();
                if (!firstAudioPacket) firstAudioPacket = await audioSink.getFirstPacket();
                if (!firstAudioPacket) throw new Error("The selected audio track contains no readable packets.");
                audioSource = new EncodedAudioPacketSource(message.audio.codec);
            }
        }

        const target = new AppendOnlyStreamTarget(new WritableStream({
            write(data) {
                const copy = data.slice();
                const id = nextWriteId++;
                pendingWriteBytes += copy.byteLength;
                pendingWrites.set(id, {byteLength: copy.byteLength});
                self.postMessage({type: "data", id, buffer: copy.buffer}, [copy.buffer]);

                // Backpressure the packet iterators when the MSE append queue
                // is full instead of accumulating the rest of the file in RAM.
                if (pendingWriteBytes < MAX_PENDING_OUTPUT_BYTES) return;
                return new Promise((resolve, reject) => {
                    outputCapacityWaiters.push({resolve, reject});
                });
            },
        }));

        output = new Output({
            format: new Mp4OutputFormat({fastStart: "fragmented", minimumFragmentDuration: 0.25}),
            target,
        });
        output.addVideoTrack(videoSource, {
            decoderConfig: message.video.decoderConfig,
            rotation: message.video.rotation || 0,
            flip: Boolean(message.video.flip),
        });

        if (audioSource && message.audio) {
            if (audioMode === "ac3-to-aac") {
                output.addAudioTrack(audioSource, {
                    languageCode: message.audio.languageCode || "und",
                    name: message.audio.name || "",
                });
            } else {
                output.addAudioTrack(audioSource, {
                    decoderConfig: message.audio.decoderConfig,
                    primingPacket: firstAudioPacket,
                    languageCode: message.audio.languageCode || "und",
                    name: message.audio.name || "",
                });
            }
        }

        await output.start();
        self.postMessage({type: "started"});

        // Start video immediately. AC-3 decoder/WASM setup happens in
        // parallel and cannot delay the first video fragments.
        void pumpVideoPackets(videoSink, firstVideoPacket);
        if (message.audio) {
            if (audioMode === "ac3-to-aac") {
                void (async () => {
                    const [{registerAc3Decoder}, {registerAacEncoder}] = await Promise.all([
                        import("@mediabunny/ac3"),
                        import("@mediabunny/aac-encoder"),
                    ]);
                    if (failed) return;
                    registerAc3Decoder();
                    registerAacEncoder();
                    if (failed) return;

                    const sink = new AudioSampleSink(audioTrackForDecode);
                    audioIterator = sink.samples(startAt)[Symbol.asyncIterator]();
                    await pumpDecodedAudioSamples();
                })().catch(reportError);
            } else {
                void pumpEncodedAudioPackets(audioSink, firstAudioPacket);
            }
        }

        void output.getMimeType().then((mimeType) => {
            self.postMessage({type: "ready", mimeType});
        }).catch(reportError);
    } catch (error) {
        reportError(error);
    }
}

self.addEventListener("message", (event) => {
    const message = event.data;
    if (message.type === "init") {
        void initialize(message);
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
        try { input?.dispose(); } catch { /* input already disposed */ }
        input = null;
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
