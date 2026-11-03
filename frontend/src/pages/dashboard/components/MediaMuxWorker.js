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
let trackChains = {video: Promise.resolve(), audio: Promise.resolve()};
let packetSequence = {video: 0, audio: 0};
let pendingWrites = new Map();
let nextWriteId = 1;
let closedTracks = new Set();
let expectedTracks = 0;
let finalizing = false;
let failed = false;

function reportError(error) {
    if (failed) return;
    failed = true;
    const message = error instanceof Error ? error.message : String(error);
    self.postMessage({type: "error", message});
    for (const pending of pendingWrites.values()) pending.reject(new Error(message));
    pendingWrites.clear();

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
        if (message.audio) audioSource = new EncodedAudioPacketSource(message.audio.codec);

        const target = new AppendOnlyStreamTarget(new WritableStream({
            write(data) {
                const copy = data.slice();
                const id = nextWriteId++;
                return new Promise((resolve, reject) => {
                    pendingWrites.set(id, {resolve, reject});
                    self.postMessage({type: "data", id, buffer: copy.buffer}, [copy.buffer]);
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
            output.addAudioTrack(audioSource, {
                decoderConfig: message.audio.decoderConfig,
                languageCode: message.audio.languageCode || "und",
                name: message.audio.name || "",
            });
        }

        await output.start();
        self.postMessage({type: "ready", mimeType: await output.getMimeType()});
    } catch (error) {
        reportError(error);
    }
}

function addPacket(message) {
    if (failed || !output) return;
    const track = message.track;
    if (track !== "video" && track !== "audio") return;
    const source = track === "video" ? videoSource : audioSource;
    if (!source) return;

    trackChains[track] = trackChains[track].then(async () => {
        const bytes = new Uint8Array(message.buffer);
        const timestamp = Number(message.timestamp);
        const duration = Number(message.duration);
        if (!Number.isFinite(timestamp)) {
            self.postMessage({type: "packet-ack", id: message.id});
            return;
        }

        const packet = new EncodedPacket(
            bytes,
            track === "video" ? (message.keyframe ? "key" : "delta") : "key",
            timestamp,
            Number.isFinite(duration) && duration > 0 ? duration : (track === "video" ? 1 / 30 : 0.02),
            packetSequence[track]++,
        );
        await source.add(packet);
        self.postMessage({type: "packet-ack", id: message.id});
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
    } else if (message.type === "packet") {
        addPacket(message);
    } else if (message.type === "end-track") {
        closedTracks.add(message.track);
        void finalizeWhenReady();
    } else if (message.type === "write-ack") {
        const pending = pendingWrites.get(message.id);
        if (pending) {
            pendingWrites.delete(message.id);
            pending.resolve();
        }
    } else if (message.type === "cancel") {
        failed = true;
        for (const pending of pendingWrites.values()) pending.reject(new DOMException("Mux cancelled", "AbortError"));
        pendingWrites.clear();
        try {
            const canceled = output?.cancel?.();
            if (canceled?.catch) void canceled.catch(() => {});
        } catch {
            // Output may already be closing.
        }
        self.close();
    }
});
