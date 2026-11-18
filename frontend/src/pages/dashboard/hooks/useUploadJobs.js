import {useEffect, useState} from "react";
import {API_URL} from "../constants.js";
import {getToken} from "../utils.js";

const JOBS_STORAGE_KEY = "storelimitless_upload_jobs";

/** Polls upload jobs, persists progress, and exposes tray actions. */
export default function useUploadJobs(refreshDirectory) {
    const [uploadJobs, setUploadJobs] = useState(() => {
        try {
            const storedJobs = JSON.parse(localStorage.getItem(JOBS_STORAGE_KEY) || "[]");
            return Array.isArray(storedJobs) ? storedJobs : [];
        } catch {
            return [];
        }
    });

    // Poll active jobs. On completion, remove the job from the tray and refresh
    // only the destination directory so the full in-memory tree stays intact.
    useEffect(() => {
        let cancelled = false;
        let polling = false;
        const jobsKey = "storelimitless_upload_jobs";
        /**
         * Refresh upload progress and sync only folders that received completed uploads.
         * @returns {Promise<void>}
         */
        const refreshUploadJobs = async () => {
            if (polling || cancelled) return;
            polling = true;
            try {
                let jobs;
                try {
                    jobs = JSON.parse(localStorage.getItem(jobsKey) || "[]");
                    if (!Array.isArray(jobs)) jobs = [];
                } catch {
                    jobs = [];
                }

                const token = getToken();
                const updatedJobs = await Promise.all(jobs.map(async (job) => {
                    if (!job?.jobId || job.status === "error" || job.status === "cancelled") return job;
                    try {
                        const response = await fetch(`${API_URL}/public/upload/${job.jobId}/status`, {
                            headers: token ? {Authorization: `Bearer ${token}`} : {}, cache: "no-store",
                        });
                        if (!response.ok) return job;
                        const data = await response.json();
                        const progressName = typeof data.name === "string" ? data.name.trim() : "";
                        const message = String(data.message || "");
                        const isComplete = data.status === "completed" || /upload\s+complete/i.test(message) || /complete/i.test(message) && Number(data.total) > 0 && Number(data.transfer) >= Number(data.total);
                        const isError = data.status === "error" || /upload\s+failed/i.test(message);
                        const transfer = Number.isFinite(Number(data.transfer)) ? Number(data.transfer) : (job.transfer || 0);
                        const total = Number.isFinite(Number(data.total)) ? Number(data.total) : (job.total || 0);
                        return {
                            ...job, // Adopt the backend's current progress name. For link imports,
                            // this changes the displayed URL to the actual YouTube title
                            // as soon as yt-dlp resolves it.
                            name: progressName || job.name,
                            transfer,
                            total,
                            progress: isComplete ? 100 : total > 0 ? Math.min(99, transfer / total * 100) : (job.progress || 0),
                            status: isComplete ? "completed" : isError ? "error" : "uploading",
                            message: isError ? (data.error || message || "Upload failed") : (message || job.message || "Uploading"),
                        };
                    } catch {
                        return job;
                    }
                }));

                if (cancelled) return;
                const completedIds = new Set(updatedJobs.filter(job => job?.status === "completed").map(job => job.jobId));
                const remainingJobs = updatedJobs.filter(job => job?.status !== "completed" && job?.status !== "cancelled");

                // Write the filtered list first, then update React state. This avoids
                // completed items reappearing from stale localStorage on the next poll.
                localStorage.setItem(jobsKey, JSON.stringify(remainingJobs));
                setUploadJobs(remainingJobs);
                if (completedIds.size > 0) {
                    // The completed jobs have already been removed from localStorage.
                    // Remove their global cancellation handles and sync each destination once.
                    const completedJobs = updatedJobs.filter(job => completedIds.has(job?.jobId));
                    for (const jobId of completedIds) {
                        window.__storeLimitlessUploadTransfers?.delete(jobId);
                    }
                    const directories = new Set(completedJobs.map(job => job.directory || "/home"));
                    await Promise.all([...directories].map(async directory => {
                        try {
                            await refreshDirectory(directory);
                        } catch (error) {
                            console.error("Failed to sync a completed upload into the local tree:", error);
                        }
                    }));
                }
            } finally {
                polling = false;
            }
        };

        void refreshUploadJobs();
        const intervalId = window.setInterval(refreshUploadJobs, 1200);
        return () => {
            cancelled = true;
            window.clearInterval(intervalId);
        };
    }, [refreshDirectory]);

    /**
     * Remove an upload job from persistent state and the dashboard tray.
     * @param {string} jobId
     * @returns {void}
     */
    const dismissUploadJob = (jobId) => {
        setUploadJobs(current => {
            const next = current.filter(job => job.jobId !== jobId);
            localStorage.setItem("storelimitless_upload_jobs", JSON.stringify(next));
            return next;
        });
    };

    /**
     * Abort a job’s active chunk requests and remove it from the tray.
     * @param {string} jobId
     * @returns {void}
     */
    const cancelUploadJob = (jobId) => {
        const transfer = window.__storeLimitlessUploadTransfers?.get(jobId);
        if (transfer) {
            transfer.cancelToken.cancelled = true;
            for (const xhr of transfer.xhrs || []) xhr.abort();
            window.__storeLimitlessUploadTransfers.delete(jobId);
        }
        // Remove it immediately from the tray. The browser transfer is aborted;
        // no server-side cancellation endpoint is required for chunked file uploads.
        dismissUploadJob(jobId);
    };


    return {uploadJobs, dismissUploadJob, cancelUploadJob};
}
