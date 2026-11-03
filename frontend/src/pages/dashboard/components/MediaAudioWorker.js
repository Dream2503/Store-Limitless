import {
    ALL_FORMATS,
    AppendOnlyStreamTarget,
    AudioSampleSink,
    AudioSampleSource,
    EncodedAudioPacketSource,
    EncodedPacketSink,
    Input,
    Mp4OutputFormat,
    Output,
    Quality,
    UrlSource,
} from "mediabunny";

let input = null;
let output = null;
let audioIterator = null;
let pendingWrites = new Map();
let pendingWriteBytes = 0;
const MAX_PENDING_OUTPUT_BYTES = 4 * 1024 * 1024;
const outputCapacityWaiters = [];
let nextWriteId = 1;
let trackClosed = false;
let finalizing = false;
let failed = false;

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
    if (failed || finalizing || !trackClosed) return;
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

async function pumpEncodedAudioPackets(sink, firstPacket) {
    try {
        for await (const packet of sink.packets(firstPacket)) {
            if (failed) break;
            await audioSource.add(packet);
        }
    } catch (error) {
        reportError(error);
        return;
    }

    if (failed) return;
    audioSource.close();
    trackClosed = true;
    void finalizeWhenReady();
}

let audioSource = null;

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
    trackClosed = true;
    void finalizeWhenReady();
}

async function initialize(message) {
    try {
        input = new Input({
            formats: ALL_FORMATS,
            source: new UrlSource(message.source, {
                maxCacheSize: 8 * 1024 * 1024,
                parallelism: 2,
            }),
        });

        const audioTracks = await input.getAudioTracks();
        const audioTrack = audioTracks.find(
            (track) => Number(track.number) === Number(message.audio.trackNumber),
        );
        if (!audioTrack) {
            throw new Error("Could not find audio track " + message.audio.trackNumber + " in the source.");
        }

        const sourceCodec = String(await audioTrack.getCodec() || "").toLowerCase();
        const expectedCodec = String(message.audio.inputCodec || "").toLowerCase();
        if (expectedCodec && sourceCodec && expectedCodec !== sourceCodec) {
            throw new Error(
                "Selected audio track changed between metadata scan and audio streaming (" +
                expectedCodec + " -> " + sourceCodec + ").",
            );
        }

        const startAt = Math.max(0, Number(message.startAt) || 0);
        const transcode = message.audio.mode === "ac3-to-aac";
        let firstPacket = null;

        if (transcode) {
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
            const sink = new EncodedPacketSink(audioTrack);
            firstPacket = startAt > 0
                ? await sink.getPacket(startAt)
                : await sink.getFirstPacket();

            if (!firstPacket && startAt > 0) {
                const first = await sink.getFirstPacket();
                if (first && startAt <= first.timestamp + first.duration + 0.25) {
                    firstPacket = first;
                }
            }
            if (!firstPacket) {
                throw new Error("No audio packet exists at or before the requested seek position.");
            }
            audioSource = new EncodedAudioPacketSource(message.audio.codec);
        }

        const target = new AppendOnlyStreamTarget(new WritableStream({
            write(data) {
                const copy = data.slice();
                const id = nextWriteId++;
                pendingWriteBytes += copy.byteLength;
                pendingWrites.set(id, {byteLength: copy.byteLength});
                self.postMessage({type: "data", id, buffer: copy.buffer}, [copy.buffer]);

                if (pendingWriteBytes < MAX_PENDING_OUTPUT_BYTES) return;
                return new Promise((resolve, reject) => {
                    outputCapacityWaiters.push({resolve, reject});
                });
            },
        }));

        output = new Output({
            format: new Mp4OutputFormat({
                fastStart: "fragmented",
                minimumFragmentDuration: 0.04,
            }),
            target,
        });

        output.addAudioTrack(audioSource, {
            decoderConfig: transcode ? undefined : message.audio.decoderConfig,
            primingPacket: transcode ? undefined : firstPacket,
            languageCode: message.audio.languageCode || "und",
            name: message.audio.name || "",
        });

        await output.start();
        self.postMessage({type: "started"});

        if (transcode) {
            void (async () => {
                const [{registerAc3Decoder}, {registerAacEncoder}] = await Promise.all([
                    import("@mediabunny/ac3"),
                    import("@mediabunny/aac-encoder"),
                ]);
                if (failed) return;
                registerAc3Decoder();
                registerAacEncoder();
                if (failed) return;

                const sink = new AudioSampleSink(audioTrack);
                audioIterator = sink.samples(startAt)[Symbol.asyncIterator]();
                await pumpDecodedAudioSamples();
            })().catch(reportError);
        } else {
            const sink = new EncodedPacketSink(audioTrack);
            void pumpEncodedAudioPackets(sink, firstPacket);
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
        for (const waiter of outputCapacityWaiters) {
            waiter.reject(new DOMException("Audio mux cancelled", "AbortError"));
        }
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
