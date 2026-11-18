import {useEffect, useRef, useState} from "react";
import {API_URL} from "../constants.js";
import {getToken} from "../utils.js";

/** Polls account-scoped batch tasks and retains terminal results until the user dismisses them. */
export default function useUploadJobs(refreshDirectory) {
    const [uploadJobs, setUploadJobs] = useState([]);
    const refreshedTerminalIds = useRef(new Set());

    useEffect(() => {
        let cancelled = false;
        let polling = false;

        const authenticatedFetch = (url, options = {}) => {
            const token = getToken();
            if (!token) return Promise.resolve(null);
            return fetch(url, {
                ...options, headers: {...(options.headers || {}), Authorization: `Bearer ${token}`}, cache: "no-store",
            });
        };

        const refreshUploadBatches = async () => {
            if (polling || cancelled) return;
            polling = true;
            try {
                const response = await authenticatedFetch(`${API_URL}/auth/upload-batches`);
                if (!response) {
                    if (!cancelled) setUploadJobs([]);
                    return;
                }
                if (response.status === 401 || response.status === 403) {
                    if (!cancelled) setUploadJobs([]);
                    return;
                }
                if (!response.ok) return;
                const payload = await response.json();
                if (!Array.isArray(payload.batches)) return;

                const batches = payload.batches.map((batch) => {
                    const status = ["queued", "uploading", "completed", "error", "cancelled"].includes(batch.status) ? batch.status : "queued";
                    const totalItems = Math.max(0, Number(batch.total_items) || 0);
                    return {
                        jobId: String(batch.batch_id),
                        name: String(batch.name || "Upload task"),
                        kind: String(batch.kind || "file"),
                        directory: String(batch.directory || "/home"),
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
                        message: String(batch.message || status),
                        createdAt: Number(batch.created_at) || 0,
                    };
                });
                // Cancellation and Clear all can be initiated from another signed-in device.
                // Mirror those server-side changes into this tab's active chunk requests.
                const batchStatusById = new Map(batches.map((batch) => [batch.jobId, batch.status]));
                for (const [batchId, transfer] of window.__storeLimitlessUploadTransfers?.entries?.() || []) {
                    const serverStatus = batchStatusById.get(batchId);
                    if (serverStatus === undefined || serverStatus === "cancelled") {
                        transfer.cancelToken.cancelled = true;
                        for (const xhr of transfer.xhrs || []) xhr.abort();
                    }
                }

                if (!cancelled) setUploadJobs(batches);

                // Refresh only after the whole batch reaches a terminal state. Keep its tray
                // record visible; completion history is user-dismissed, not auto-pruned.
                for (const batch of batches) {
                    if (!["completed", "error", "cancelled"].includes(batch.status) || refreshedTerminalIds.current.has(batch.jobId)) continue;
                    refreshedTerminalIds.current.add(batch.jobId);
                    try {
                        await refreshDirectory(batch.directory || "/home");
                    } catch (error) {
                        console.error("Failed to refresh the directory after an upload task finished:", error);
                    }
                }
            } catch (error) {
                console.error("Could not refresh upload tasks:", error);
            } finally {
                polling = false;
            }
        };

        void refreshUploadBatches();
        const intervalId = window.setInterval(refreshUploadBatches, 1000);
        return () => {
            cancelled = true;
            window.clearInterval(intervalId);
        };
    }, [refreshDirectory]);

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

    /** Stop the current transfer and prevent later items in the batch from starting. */
    const cancelUploadJob = (jobId) => {
        const transfer = window.__storeLimitlessUploadTransfers?.get(jobId);
        if (transfer) {
            transfer.cancelToken.cancelled = true;
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

    /** Clear all task history, cancelling active batch uploads first. */
    const clearAllUploadJobs = () => {
        for (const transfer of window.__storeLimitlessUploadTransfers?.values?.() || []) {
            transfer.cancelToken.cancelled = true;
            for (const xhr of transfer.xhrs || []) xhr.abort();
        }
        setUploadJobs([]);
        const token = getToken();
        if (!token) return;
        void fetch(`${API_URL}/auth/upload-batches`, {
            method: "DELETE", headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
        }).then(async (response) => {
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || `Could not clear upload tray (${response.status})`);
            }
        }).catch((error) => console.error("Could not clear upload tray:", error));
    };

    return {uploadJobs, dismissUploadJob, cancelUploadJob, clearAllUploadJobs};
}
