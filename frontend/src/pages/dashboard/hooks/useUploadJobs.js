import {useEffect, useRef, useState} from "react";
import {API_URL} from "../constants.js";
import {endAuthSession, getToken} from "../utils.js";

const ACTIVE_POLL_INTERVAL_MS = 500;
const IDLE_POLL_INTERVAL_MS = 10000;

/** Polls account-scoped batch tasks and retains terminal results until the user dismisses them. */
export default function useUploadJobs(refreshDirectory) {
    const [uploadJobs, setUploadJobs] = useState([]);
    const refreshedTerminalIds = useRef(new Set());
    const hasActiveUploads = useRef(false);
    const refreshDirectoryRef = useRef(refreshDirectory);
    refreshDirectoryRef.current = refreshDirectory;

    useEffect(() => {
        let cancelled = false;
        let polling = false;
        let pollAgain = false;
        let pollTimer = null;

        const handleOptimisticBatch = (event) => {
            const batch = event.detail;
            if (!batch?.jobId) return;
            const optimistic = {
                ...batch,
                jobId: String(batch.jobId),
                status: "queued",
                message: "Preparing upload",
                paused: false,
                progress: 0,
                transfer: 0,
                total: 0,
                downloadTransfer: 0,
                downloadTotal: 0,
                downloadActive: batch.kind === "link",
                uploadTransfer: 0,
                uploadTotal: 0,
                downloadSpeed: 0,
                uploadSpeed: 0,
                createdAt: Date.now(),
            };
            setUploadJobs((current) => current.some((job) => job.jobId === optimistic.jobId)
                ? current
                : [optimistic, ...current]);
            hasActiveUploads.current = true;
            scheduleNextPoll(true);
        };

        const scheduleNextPoll = (immediate = false) => {
            if (cancelled) return;
            if (pollTimer !== null) {
                window.clearTimeout(pollTimer);
                pollTimer = null;
            }

            // Idle tabs do not need to query Redis while hidden. Active tabs keep
            // polling in the background so a cancellation made on another device
            // can still stop an in-progress transfer.
            if (document.visibilityState !== "visible" && !hasActiveUploads.current) return;

            const delay = immediate
                ? 0
                : hasActiveUploads.current
                    ? ACTIVE_POLL_INTERVAL_MS
                    : IDLE_POLL_INTERVAL_MS;
            pollTimer = window.setTimeout(refreshUploadBatches, delay);
        };

        const authenticatedFetch = async (url, options = {}) => {
            const token = getToken();
            if (!token) return null;

            // A stalled tunnel/proxy response must not leave the single-flight
            // polling guard locked forever. This is only the status request, not
            // the underlying upload, so aborting it never cancels the transfer.
            const controller = new AbortController();
            const timeoutId = window.setTimeout(() => controller.abort(), 10000);
            try {
                return await fetch(url, {
                    ...options,
                    headers: {...(options.headers || {}), Authorization: `Bearer ${token}`},
                    signal: controller.signal,
                    cache: "no-store",
                });
            } finally {
                window.clearTimeout(timeoutId);
            }
        };

        const requestImmediateRefresh = () => {
            if (cancelled) return;
            if (polling) {
                pollAgain = true;
                return;
            }
            scheduleNextPoll(true);
        };

        const handleVisibilityChange = () => {
            if (document.visibilityState === "visible") {
                requestImmediateRefresh();
            } else if (!hasActiveUploads.current && pollTimer !== null) {
                window.clearTimeout(pollTimer);
                pollTimer = null;
            }
        };

        const refreshUploadBatches = async () => {
            if (cancelled) return;
            if (polling) {
                pollAgain = true;
                return;
            }
            polling = true;
            try {
                const response = await authenticatedFetch(`${API_URL}/auth/upload-batches`);
                if (!response) {
                    hasActiveUploads.current = false;
                    if (!cancelled) setUploadJobs([]);
                    return;
                }
                if (response.status === 401) {
                    // Drop an expired/revoked token so the dashboard stops polling
                    // with credentials the API has already rejected.
                    const rejectedToken = getToken();
                    if (rejectedToken) endAuthSession(rejectedToken);
                    hasActiveUploads.current = false;
                    if (!cancelled) setUploadJobs([]);
                    return;
                }
                if (response.status === 403) {
                    hasActiveUploads.current = false;
                    if (!cancelled) setUploadJobs([]);
                    return;
                }
                if (!response.ok) return;
                const payload = await response.json();
                if (!Array.isArray(payload.batches)) return;

                // One authenticated request returns the entire queue. Publish the same
                // snapshot to the browser upload worker so it never polls each child job.
                window.__storeLimitlessUploadBatchesSnapshot = payload.batches;
                window.dispatchEvent(new CustomEvent("storelimitless:upload-batches-snapshot", {
                    detail: payload.batches,
                }));

                const batches = payload.batches.map((batch) => {
                    const status = ["queued", "uploading", "paused", "completed", "error", "cancelled"].includes(batch.status) ? batch.status : "queued";
                    const totalItems = Math.max(0, Number(batch.total_items) || 0);
                    const normalized = {
                        jobId: String(batch.batch_id),
                        paused: Boolean(batch.paused) || status === "paused",
                        name: String(batch.name || "Upload task"),
                        kind: String(batch.kind || "file"),
                        directory: String(batch.directory || "/home"),
                        directories: Array.isArray(batch.directories) && batch.directories.length
                            ? [...new Set(batch.directories.map((value) => String(value || "").trim()).filter(Boolean))]
                            : [String(batch.directory || "/home")],
                        dataCenter: String(batch.data_center || ""),
                        items: Array.isArray(batch.items) ? batch.items : [],
                        torrentFiles: Array.isArray(batch.torrent_files) ? batch.torrent_files : [],
                        totalItems,
                        completedItems: Math.max(0, Number(batch.completed_items) || 0),
                        currentIndex: Math.max(0, Number(batch.current_index) || 0),
                        currentName: String(batch.current_name || ""),
                        currentJobId: String(batch.current_job_id || ""),
                        status,
                        progress: status === "completed" ? 100 : Math.max(0, Math.min(99, Number(batch.progress) || 0)),
                        transfer: Math.max(0, Number(batch.transfer) || 0),
                        total: Math.max(0, Number(batch.total) || 0),
                        uploadTransfer: Math.max(0, Number(batch.upload_transfer ?? batch.transfer) || 0),
                        uploadTotal: Math.max(0, Number(batch.upload_total ?? batch.total) || 0),
                        downloadTransfer: Math.max(0, Number(batch.download_transfer) || 0),
                        downloadTotal: Math.max(0, Number(batch.download_total) || 0),
                        downloadActive: Boolean(batch.download_active),
                        downloadSpeed: Math.max(0, Number(batch.download_speed) || 0),
                        uploadSpeed: Math.max(0, Number(batch.upload_speed) || 0),
                        downloadEta: batch.download_eta == null ? null : Math.max(0, Number(batch.download_eta) || 0),
                        uploadEta: batch.upload_eta == null ? null : Math.max(0, Number(batch.upload_eta) || 0),
                        ingestionType: String(batch.ingestion_type || ""),
                        fileCounts: batch.file_counts && typeof batch.file_counts === "object" ? batch.file_counts : {},
                        lastUpdatedAt: Math.max(0, Number(batch.last_updated_at) || 0),
                        message: String(batch.message || status),
                        createdAt: Number(batch.created_at) || 0,
                    };

                    // The backend list endpoint already merges Redis child progress
                    // and torrent item state into this batch. No per-child HTTP reads.
                    return normalized;
                });
                // Keep terminal tasks visible until the user explicitly removes
                // one with its row action or clears terminal history from the tray.
                const visibleBatches = batches;

                // Active batches stay near-real-time; while idle, the longer interval
                // still discovers work started by another device without constant empty polls.
                hasActiveUploads.current = visibleBatches.some((batch) => ["queued", "uploading", "paused"].includes(batch.status));

                // Mirror pause/resume/cancel requests made from another signed-in device.
                const batchById = new Map(batches.map((batch) => [batch.jobId, batch]));
                for (const [batchId, transfer] of window.__storeLimitlessUploadTransfers?.entries?.() || []) {
                    const serverBatch = batchById.get(batchId);
                    if (serverBatch === undefined || serverBatch.status === "cancelled") {
                        transfer.cancelToken.cancelled = true;
                        transfer.cancelToken.paused = false;
                        const waiters = transfer.cancelToken.pauseWaiters?.splice?.(0) || [];
                        waiters.forEach((resolve) => resolve());
                        for (const xhr of transfer.xhrs || []) xhr.abort();
                    } else if (serverBatch.status === "paused" && !transfer.cancelToken.paused) {
                        transfer.cancelToken.paused = true;
                        // Abort only in-flight chunks. Their offsets are retried after resume;
                        // completed chunks stay committed on the server.
                        for (const xhr of transfer.xhrs || []) xhr.abort();
                    } else if (serverBatch.status !== "paused" && transfer.cancelToken.paused) {
                        transfer.cancelToken.paused = false;
                        const waiters = transfer.cancelToken.pauseWaiters?.splice?.(0) || [];
                        waiters.forEach((resolve) => resolve());
                    }
                }

                if (!cancelled) setUploadJobs(visibleBatches);

                // Refresh destination directories after terminal states. Successful browser-file
                // batches were auto-dismissed above; failed/cancelled tasks and link-import
                // history remain visible until the user dismisses them.
                for (const batch of batches) {
                    if (!["completed", "error", "cancelled"].includes(batch.status) || refreshedTerminalIds.current.has(batch.jobId)) continue;
                    refreshedTerminalIds.current.add(batch.jobId);
                    for (const directory of (batch.directories?.length ? batch.directories : [batch.directory || "/home"])) {
                        try {
                            await refreshDirectoryRef.current(directory);
                        } catch (error) {
                            console.error(`Failed to refresh ${directory} after an upload task finished:`, error);
                        }
                    }
                }
            } catch (error) {
                console.error("Could not refresh upload tasks:", error);
            } finally {
                polling = false;
                if (pollAgain) {
                    pollAgain = false;
                    scheduleNextPoll(true);
                } else {
                    scheduleNextPoll();
                }
            }
        };

        const handleUploadFileCompleted = (event) => {
            const directory = String(event.detail?.directory || "").trim();
            if (!directory) return;
            void Promise.resolve(refreshDirectoryRef.current(directory)).catch((error) => {
                console.error(`Could not refresh ${directory} after an uploaded file completed:`, error);
            });
        };

        const warnBeforeUnload = (event) => {
            const activeTransfers = window.__storeLimitlessUploadTransfers;
            const hasRunningBrowserQueue = activeTransfers instanceof Map
                && Array.from(activeTransfers.values()).some((transfer) =>
                    transfer?.cancelToken && !transfer.cancelToken.cancelled
                );
            if (!hasRunningBrowserQueue) return;
            // The selected File objects are owned by this browser tab. Reloading
            // destroys them, so warn before the user accidentally strands queued files.
            event.preventDefault();
            event.returnValue = "";
        };

        window.addEventListener("storelimitless:upload-batch-created", handleOptimisticBatch);
        window.addEventListener("storelimitless:upload-file-completed", handleUploadFileCompleted);
        window.addEventListener("focus", requestImmediateRefresh);
        window.addEventListener("online", requestImmediateRefresh);
        window.addEventListener("beforeunload", warnBeforeUnload);
        document.addEventListener("visibilitychange", handleVisibilityChange);

        void refreshUploadBatches();
        return () => {
            cancelled = true;
            if (pollTimer !== null) window.clearTimeout(pollTimer);
            window.removeEventListener("storelimitless:upload-batch-created", handleOptimisticBatch);
            window.removeEventListener("storelimitless:upload-file-completed", handleUploadFileCompleted);
            window.removeEventListener("focus", requestImmediateRefresh);
            window.removeEventListener("online", requestImmediateRefresh);
            window.removeEventListener("beforeunload", warnBeforeUnload);
            document.removeEventListener("visibilitychange", handleVisibilityChange);
        };
    }, []);

    /** Remove one terminal task from the shared tray. */
    const dismissUploadJob = (jobId) => {
        setUploadJobs((current) => current.filter((job) => job.jobId !== jobId));
        const token = getToken();
        if (!token) return;
        void fetch(`${API_URL}/auth/upload-batches/${encodeURIComponent(jobId)}`, {
            method: "DELETE", headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
        }).then(async (response) => {
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || `Could not remove task (${response.status})`);
            }
        }).catch((error) => console.error("Could not remove upload task:", error));
    };

    /** Pause or resume a browser-driven upload without discarding completed chunks. */
    const setUploadPaused = (jobId, paused) => {
        const token = getToken();
        if (!token) return;
        const currentJob = uploadJobs.find((job) => job.jobId === jobId);
        if (currentJob?.kind !== "file") {
            console.warn("Pause/resume is currently available for file uploads only.");
            return;
        }
        if (paused && currentJob.status === "queued") {
            console.warn("Wait until the file begins uploading before pausing.");
            return;
        }
        const transfer = window.__storeLimitlessUploadTransfers?.get(jobId);
        const localTransfer = Boolean(transfer);
        const previousPaused = Boolean(transfer?.cancelToken?.paused);
        if (!localTransfer && paused) {
            console.warn("This upload was started in another tab/device. Pause it from the tab running the upload.");
        }
        if (transfer?.cancelToken) {
            transfer.cancelToken.paused = paused;
            const waiters = transfer.cancelToken.pauseWaiters?.splice?.(0) || [];
            waiters.forEach((resolve) => resolve());
            if (paused) {
                // Aborted in-flight chunks are retried by their worker after resume.
                for (const xhr of transfer.xhrs || []) xhr.abort();
            }
        }
        setUploadJobs((current) => current.map((job) => job.jobId === jobId ? {
            ...job,
            status: paused ? "paused" : "uploading",
            message: paused ? "Paused by you" : "Resuming upload",
            uploadSpeed: paused ? 0 : job.uploadSpeed,
            downloadSpeed: paused ? 0 : job.downloadSpeed,
        } : job));
        void fetch(`${API_URL}/auth/upload-batches/${encodeURIComponent(jobId)}/pause`, {
            method: "POST",
            headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
            body: JSON.stringify({paused}),
            cache: "no-store",
        }).then(async (response) => {
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `Could not ${paused ? "pause" : "resume"} upload (${response.status})`);
            return data;
        }).catch((error) => {
            console.error("Could not change upload pause state:", error);
            if (transfer?.cancelToken) {
                // Roll back the optimistic local flag when the API rejects the
                // pause/resume request, and wake any chunk lanes that need to recover.
                transfer.cancelToken.paused = previousPaused;
                if (!previousPaused) {
                    const waiters = transfer.cancelToken.pauseWaiters?.splice?.(0) || [];
                    waiters.forEach((resolve) => resolve());
                }
            }
            setUploadJobs((current) => current.map((job) => job.jobId === jobId ? {
                ...job,
                status: previousPaused ? "paused" : "uploading",
                message: error.message || "Could not change pause state",
            } : job));
        });
    };

    /** Stop the current transfer and prevent later items in the batch from starting. */
    const cancelUploadJob = (jobId) => {
        const transfer = window.__storeLimitlessUploadTransfers?.get(jobId);
        if (transfer) {
            transfer.cancelToken.cancelled = true;
            transfer.cancelToken.paused = false;
            const waiters = transfer.cancelToken.pauseWaiters?.splice?.(0) || [];
            waiters.forEach((resolve) => resolve());
            for (const xhr of transfer.xhrs || []) xhr.abort();
        }
        setUploadJobs((current) => current.map((job) => job.jobId === jobId ? {
            ...job, status: "cancelled", message: "Cancelling upload task",
        } : job));
        const token = getToken();
        if (!token) return;
        void fetch(`${API_URL}/auth/upload-batches/${encodeURIComponent(jobId)}/cancel`, {
            method: "POST", headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
        }).then(async (response) => {
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || `Could not cancel task (${response.status})`);
            }
        }).catch((error) => console.error("Could not cancel upload task:", error));
    };

    /** Remove terminal upload history without cancelling or mutating active transfers. */
    const clearAllUploadJobs = () => {
        const token = getToken();
        if (!token) return;

        void fetch(`${API_URL}/auth/upload-batches`, {
            method: "DELETE", headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
        }).then(async (response) => {
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.detail || `Could not clear upload tray (${response.status})`);
            }

            // The server returns the exact terminal batches it removed. Active
            // queued/uploading/paused batches remain in both Redis and local state.
            const removedIds = new Set(
                Array.isArray(data.removed_batch_ids)
                    ? data.removed_batch_ids.map((id) => String(id))
                    : uploadJobs
                        .filter((job) => ["completed", "error", "failed", "cancelled"].includes(String(job.status)))
                        .map((job) => String(job.jobId))
            );

            setUploadJobs((current) => current.filter((job) => !removedIds.has(String(job.jobId))));
            const transfers = window.__storeLimitlessUploadTransfers;
            for (const jobId of removedIds) transfers?.delete?.(jobId);
        }).catch((error) => console.error("Could not clear upload history:", error));
    };

    return {uploadJobs, dismissUploadJob, cancelUploadJob, pauseUploadJob: (jobId) => setUploadPaused(jobId, true), resumeUploadJob: (jobId) => setUploadPaused(jobId, false), clearAllUploadJobs};
}
