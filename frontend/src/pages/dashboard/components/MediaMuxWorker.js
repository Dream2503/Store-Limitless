import {
    AppendOnlyStreamTarget,
    EncodedAudioPacketSource,
    EncodedPacket,
    EncodedVideoPacketSource,
    Mp4OutputFormat,
    Output,
} from "mediabunny";

let output = null;
let videoSource = null;
let audioSource = null;
let audioCodec = null;
let audioDecoderConfig = null;
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

function reportError(error) {
    if (failed) return;
    failed = true;
    const message = error instanceof Error ? error.message : String(error);
    self.postMessage({type: "error", message});
    for (const waiter of outputCapacityWaiters) waiter.reject(new Error(message));
    outputCapacityWaiters.length = 0;
    pendingWrites.clear();
    pendingWriteBytes = 0;

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
            audioCodec = message.audio.codec;
            audioDecoderConfig = message.audio.decoderConfig;
            audioPrimed = false;
            audioSource = new EncodedAudioPacketSource(audioCodec);
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

        await output.start();
        self.postMessage({type: "started"});
        void output.getMimeType().then((mimeType) => {
            self.postMessage({type: "ready", mimeType});
        }).catch(reportError);
    } catch (error) {
        reportError(error);
    }
}

function addPacketBatch(message) {
    if (failed || !output) return;
    const track = message.track;
    if (track !== "video" && track !== "audio") return;
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
        try {
            const canceled = output?.cancel?.();
            if (canceled?.catch) void canceled.catch(() => {});
        } catch {
            // Output may already be closing.
        }
        self.close();
    }
});
