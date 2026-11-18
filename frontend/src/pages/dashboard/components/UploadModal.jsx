import {useEffect, useRef, useState} from "react";
import {useNavigate, useSearchParams} from "react-router-dom";
import {getFilesFromDrop, getUploadRelativePath} from "../uploadFiles.js";

/** @typedef {{cancelled: boolean}} UploadCancelToken */
/** @typedef {Error & {fatal?: boolean, queueFatal?: boolean}} UploadRequestError */

/** @type {string | undefined} */
const API_URL = window.STORELIMITLESS_CONFIG?.API_URL ?? import.meta.env.VITE_API_URL

const DATA_CENTERS = ["Discord", "Telegram"];

// Each chunk is its own PUT; the server writes it to disk as it arrives.
const CHUNK_SIZE = 1024 * 1024;
// How many chunks are in flight at once (browsers allow ~6 connections per host).
const PARALLEL_CHUNKS = 8;
const MAX_CHUNK_ATTEMPTS = 3;

// Supabase-style classes
const labelClass = "block text-sm text-[#b4b4b4] mb-2";
const optionBase = ["flex items-center justify-center gap-2 h-[38px] rounded-md border px-4 text-sm transition-colors", "disabled:cursor-not-allowed disabled:opacity-50",].join(" ");
const optionOff = "border-[#363636] bg-white/[0.026] text-[#898989] hover:border-[#4d4d4d] hover:text-[#fafafa]";
const optionOn = "border-[#3ecf8e]/50 bg-[#3ecf8e]/10 text-[#fafafa]";

/**
 * Convert a stored directory path into the upload page’s dashboard route.
 * @param {string} directory
 * @returns {string}
 */
const directoryToRoute = (directory) => {
    const segments = (directory || "/home").split("/").filter(Boolean);
    const relativeSegments = segments[0] === "home" ? segments.slice(1) : segments;
    return `/home${relativeSegments.length ? `/${relativeSegments.map(encodeURIComponent).join("/")}` : ""}`;
};

/**
 * Format a file size using binary units.
 * @param {number} bytes
 * @returns {string}
 */
const formatBytes = (bytes) => {
    if (bytes < 1000) return `${bytes} B`;

    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes;
    let unit = -1;

    do {
        value /= 1000;
        unit++;
    } while (value >= 1000 && unit < units.length - 1);

    return `${value.toFixed(1)} ${units[unit]}`;
};

/**
 * Render the file and URL upload workflow.
 * @param {{modal?: boolean, targetDirectory?: string, onClose?: (() => void) | null, initialFiles?: File[]}} [props={}]
 * @returns {import("react").ReactElement}
 */
export default function UploadPage({modal = false, targetDirectory = "", onClose = null, initialFiles = []} = {}) {
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();

    // Current directory from dashboard
    const directory = targetDirectory || searchParams.get("directory") || "/home";

    // Upload states
    /** @type {[File[], import("react").Dispatch<import("react").SetStateAction<File[]>>]} */
    const [files, setFiles] = useState(() => Array.from(initialFiles || []));
    const [link, setLink] = useState("");
    const links = link.trim() ? [link.trim()] : [];
    /** @type {["file" | "link", import("react").Dispatch<import("react").SetStateAction<"file" | "link">>]} */
    const [uploadMode, setUploadMode] = useState("file");

    // Other states
    /** @type {["Discord" | "Telegram", import("react").Dispatch<import("react").SetStateAction<"Discord" | "Telegram">>]} */
    const [dataCenter, setDataCenter] = useState("Discord");
    const [message, setMessage] = useState("");
    const [status, setStatus] = useState("idle");
    const [dragOver, setDragOver] = useState(false);
    const [queueProgress, setQueueProgress] = useState({current: 0, total: 0});
    const [torrentSelectionQueue, setTorrentSelectionQueue] = useState([]);
    const [torrentSelectionIndex, setTorrentSelectionIndex] = useState(0);
    const [torrentSelections, setTorrentSelections] = useState({});
    const [torrentSelectionReady, setTorrentSelectionReady] = useState(false);
    const [torrentSelectionModal, setTorrentSelectionModal] = useState(false);
    const [torrentPreviewLoading, setTorrentPreviewLoading] = useState(false);
    const uploadButtonRef = useRef(null);
    const uploadRunRef = useRef(false);
    const keyboardActionsRef = useRef({isUploadDisabled: true, directory, navigate});

    /**
     * Wait for a specified number of milliseconds.
     * @param {number} milliseconds
     * @returns {Promise<void>}
     */
    const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

    /**
     * Wait until the backend finishes storing one upload before allowing the queue to advance.
     * @param {string} jobId
     * @param {string} token
     * @returns {Promise<void>}
     */
    const waitForJobCompletion = async (jobId, token, cancelToken = null) => {
        let delay = 500;

        while (true) {
            if (cancelToken?.cancelled) throw new Error("Upload task was cancelled.");
            let response;

            try {
                response = await fetch(`${API_URL}/auth/upload-jobs/${encodeURIComponent(jobId)}/status`, {
                    headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
                });
            } catch {
                await sleep(delay);
                delay = Math.min(delay * 2, 3000);
                continue;
            }

            if (!response.ok) {
                if (response.status >= 500) {
                    await sleep(delay);
                    delay = Math.min(delay * 2, 3000);
                    continue;
                }

                let detail = `Could not check upload status (${response.status})`;
                try {
                    const payload = await response.json();
                    detail = payload.detail || detail;
                } catch {
                    // Keep the HTTP status message if the response is not JSON.
                }
                /** @type {UploadRequestError} */
                const statusError = new Error(detail);
                statusError.queueFatal = true;
                throw statusError;
            }

            let job;
            try {
                job = await response.json();
            } catch {
                /** @type {UploadRequestError} */
                const statusError = new Error("The server returned an unreadable upload status.");
                statusError.queueFatal = true;
                throw statusError;
            }

            if (job.status === "completed") return;
            if (job.status === "cancelled") throw new Error(`${job.name || "Upload"} was cancelled.`);
            if (job.status === "error") throw new Error(job.message || `${job.name || "Upload"} failed.`);

            delay = 500;
            await sleep(500);
        }
    };

    // ==========================================
    // Send ONE chunk (several of these run in parallel)
    // ==========================================
    /**
     * Send one file chunk and track its XMLHttpRequest in this job’s set.
     * @param {string} jobId
     * @param {Blob} blob
     * @param {number} offset
     * @param {number} totalSize
     * @param {Set<XMLHttpRequest>} jobXhrs
     * @returns {Promise<void>}
     */
    const sendChunk = (jobId, blob, offset, totalSize, jobXhrs) => new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        jobXhrs.add(xhr);

        const done = () => jobXhrs.delete(xhr);

        xhr.open("PUT", `${API_URL}/public/upload/${jobId}/chunk?offset=${offset}&total=${totalSize}`);
        xhr.setRequestHeader("Authorization", `Bearer ${localStorage.getItem("token")}`);
        xhr.setRequestHeader("Content-Type", "application/octet-stream");

        xhr.onload = () => {
            done();

            if (xhr.status >= 200 && xhr.status < 300) {
                resolve();
                return;
            }

            let errorMessage = `Chunk upload failed (${xhr.status})`;

            try {
                const response = JSON.parse(xhr.responseText);
                errorMessage = response.detail || response.error || errorMessage;
            } catch {
                // Keep the HTTP status message if the response is not JSON.
            }

            const error = new Error(errorMessage);
            // 4xx errors will not be fixed by retrying.
            error.fatal = xhr.status >= 400 && xhr.status < 500;
            reject(error);
        };

        xhr.onerror = () => {
            done();
            reject(new Error("Network error while sending the file."));
        };

        xhr.onabort = () => {
            done();
            const error = new Error("File upload was cancelled.");
            error.fatal = true;
            reject(error);
        };

        xhr.send(blob);
    });

    /**
     * Upload a file in parallel chunks with retries and per-job cancellation.
     * @param {string} jobId
     * @param {File} selectedFile
     * @param {UploadCancelToken} cancelToken
     * @param {Set<XMLHttpRequest>} jobXhrs
     * @returns {Promise<void>}
     */
    const uploadFileInChunks = async (jobId, selectedFile, cancelToken, jobXhrs) => {
        const totalSize = selectedFile.size;
        // An empty file still sends one (empty) chunk so the server can finish.
        const chunkCount = Math.max(1, Math.ceil(totalSize / CHUNK_SIZE));
        let nextIndex = 0;

        // Each lane keeps taking the next unsent chunk, so chunks start in order.
        /**
         * Upload successive chunks until this worker reaches the end of the file.
         * @returns {Promise<void>}
         */
        const lane = async () => {
            while (!cancelToken.cancelled) {
                const index = nextIndex++;

                if (index >= chunkCount) return;

                const start = index * CHUNK_SIZE;
                const end = Math.min(start + CHUNK_SIZE, totalSize);
                const blob = selectedFile.slice(start, end);

                for (let attempt = 1; ; attempt++) {
                    if (cancelToken.cancelled) {
                        throw new Error("File upload was cancelled.");
                    }

                    try {
                        await sendChunk(jobId, blob, start, totalSize, jobXhrs);
                        break;
                    } catch (error) {
                        if (error.fatal || cancelToken.cancelled || attempt >= MAX_CHUNK_ATTEMPTS) {
                            throw error;
                        }

                        await sleep(1000 * attempt);
                    }
                }
            }
        };

        await Promise.all(Array.from({length: Math.min(PARALLEL_CHUNKS, chunkCount)}, lane));
    };

    /**
     * Create upload jobs and begin file-chunk transfers or link imports.
     * @returns {Promise<void>}
     */
    const handleUpload = async () => {
        if (!dataCenter) return;
        if (uploadMode === "file" && files.length === 0) return;
        if (uploadMode === "link" && links.length === 0) return;

        if (uploadMode === "link") {
            for (const [index, value] of links.entries()) {
                try {
                    const parsed = new URL(value);
                    const isHttp = ["http:", "https:"].includes(parsed.protocol);
                    const topics = [...parsed.searchParams.entries()]
                        .filter(([key]) => key.toLowerCase() === "xt")
                        .map(([, topic]) => topic);
                    const isMagnet = parsed.protocol === "magnet:" && value.toLowerCase().startsWith("magnet:?") && topics.some((topic) => /^(urn:btih:|urn:btmh:)/i.test(topic));
                    if (!isHttp && !isMagnet) throw new Error();
                } catch {
                    setMessage(`Invalid HTTP(S) URL or torrent magnet on line ${index + 1}: ${value}`);
                    setStatus("error");
                    return;
                }
            }
        }

        const torrentLinks = uploadMode === "link" ? links.map((value, index) => ({
            value, index
        })).filter(({value}) => value.toLowerCase().startsWith("magnet:?")) : [];

        if (uploadRunRef.current) return;
        uploadRunRef.current = true;
        setStatus("uploading");
        setMessage("");

        const token = localStorage.getItem("token");
        if (!token) {
            uploadRunRef.current = false;
            navigate("/");
            return;
        }

        const filesWithPaths = uploadMode === "file" ? files.map((selectedFile) => ({
            file: selectedFile, relativePath: getUploadRelativePath(selectedFile),
        })) : [];
        const queueItems = uploadMode === "file" ? filesWithPaths.map(({file, relativePath}) => ({
            name: relativePath, size: file.size
        })) : links.map((value) => ({name: value, size: 0}));
        const queueTotal = queueItems.length;
        setQueueProgress({current: 0, total: queueTotal});
        let batchId = "";
        let batchTransfer = null;

        try {
            const folderRoots = [...new Set(filesWithPaths.map(({relativePath}) => relativePath.split("/").filter(Boolean)[0]).filter(Boolean))];
            const batchName = uploadMode === "link" ? "Link import" : folderRoots.length === 1 && filesWithPaths.some(({relativePath}) => relativePath.includes("/")) ? `${folderRoots[0]} (${filesWithPaths.length} file${filesWithPaths.length === 1 ? "" : "s"})` : `${filesWithPaths.length} file${filesWithPaths.length === 1 ? "" : "s"}`;
            const batchResponse = await fetch(`${API_URL}/auth/upload-batches`, {
                method: "POST", headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"}, body: JSON.stringify({
                    name: batchName, kind: uploadMode, directory, data_center: dataCenter, items: queueItems,
                }),
            });
            if (!batchResponse.ok) {
                let detail = `Could not create upload task (${batchResponse.status})`;
                try {
                    const payload = await batchResponse.json();
                    detail = payload.detail || detail;
                } catch {
                    // Keep the HTTP status text if the response isn't JSON.
                }
                throw new Error(detail);
            }
            const batchPayload = await batchResponse.json();
            batchId = String(batchPayload.batch_id || "");
            if (!batchId) throw new Error("The server did not return an upload task ID.");

            const cancelToken = {cancelled: false};
            batchTransfer = {cancelToken, xhrs: new Set(), activeJobId: ""};
            if (!window.__storeLimitlessUploadTransfers) window.__storeLimitlessUploadTransfers = new Map();
            window.__storeLimitlessUploadTransfers.set(batchId, batchTransfer);

            /** Publish queue progress to the task entry shown in the dashboard tray. */
            const publishBatch = async (updates) => {
                if (!batchId) return;
                const currentToken = localStorage.getItem("token");
                if (!currentToken) throw new Error("Your session has expired.");
                const response = await fetch(`${API_URL}/auth/upload-batches/${encodeURIComponent(batchId)}`, {
                    method: "PATCH",
                    headers: {Authorization: `Bearer ${currentToken}`, "Content-Type": "application/json"},
                    body: JSON.stringify(updates),
                });
                if (!response.ok) {
                    let detail = `Could not update upload task (${response.status})`;
                    try {
                        const payload = await response.json();
                        detail = payload.detail || detail;
                    } catch {
                        // Preserve the status code if the response is not JSON.
                    }
                    throw new Error(detail);
                }
                const result = await response.json().catch(() => ({}));
                if (result.status === "cancelled" && updates.status !== "cancelled") {
                    cancelToken.cancelled = true;
                    throw new Error("Upload task was cancelled.");
                }
                return result;
            };

            // The tray record is persisted before any transfer begins, so it immediately
            // contains every selected file/link, even though transfers run one at a time.
            if (modal) onClose?.(); else navigate(directoryToRoute(directory));

            if (torrentLinks.length > 0) {
                if (torrentLinks.length !== links.length) {
                    throw new Error("Please submit torrent magnets separately from regular links so each import can be selected safely.");
                }

                // Inspection runs after the modal closes. The upload tray owns the task while
                // metadata resolves, so the rest of the website remains usable.
                void (async () => {
                    try {
                        await publishBatch({
                            status: "uploading", message: "Inspecting torrent metadata", current_name: "Inspecting torrent metadata"
                        });
                        const previews = await Promise.all(torrentLinks.map(async ({value, index}) => {
                            const params = new URLSearchParams({link: value});
                            const response = await fetch(`${API_URL}/auth/torrent-preview?${params.toString()}`, {
                                headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
                            });
                            if (!response.ok) {
                                let detail = `Could not inspect torrent (${response.status})`;
                                try {
                                    const payload = await response.json();
                                    detail = payload.detail || detail;
                                } catch {
                                    // Keep the HTTP status message when the response is not JSON.
                                }
                                throw new Error(detail);
                            }
                            const preview = await response.json();
                            if (!Array.isArray(preview.files) || preview.files.length === 0) {
                                throw new Error(`Torrent ${preview.name || value} contains no selectable files.`);
                            }
                            return {
                                link: value, queueIndex: index, name: String(preview.name || "Torrent"), files: preview.files.map((item) => ({
                                    index: Number(item.index),
                                    path: String(item.path || ""),
                                    name: String(item.path || "").split("/").slice(1).join("/") || String(item.path || "").split("/").pop(),
                                    size: Number(item.size) || 0,
                                })),
                            };
                        }));
                        await publishBatch({
                            status: "queued",
                            message: "Torrent metadata ready. Select files to begin.",
                            current_name: "",
                            torrent_files: previews,
                            items: previews.map((torrent) => ({
                                name: torrent.name,
                                size: torrent.files.reduce((total, item) => total + item.size, 0),
                                status: "queued",
                                message: "Ready to select files",
                            })),
                        });
                    } catch (error) {
                        try {
                            await publishBatch({
                                status: "error", message: error instanceof Error ? error.message : "Could not inspect torrent metadata.",
                            });
                        } catch (publishError) {
                            console.error("Could not update failed torrent inspection task:", publishError);
                        }
                    } finally {
                        window.__storeLimitlessUploadTransfers?.delete(batchId);
                    }
                })();
                return;
            }

            /** Create one child upload job, and fully finish it before starting the next item. */
            const createJob = async (selectedFile, selectedLink = "", destinationDirectory = directory, displayName = selectedFile?.name || selectedLink, itemNumber = 1) => {
                if (cancelToken.cancelled) throw new Error("Upload task was cancelled.");
                let response;
                if (uploadMode === "file") {
                    response = await fetch(`${API_URL}/auth/upload`, {
                        method: "POST", headers: {
                            Authorization: `Bearer ${token}`,
                            "X-File-Name": selectedFile.name,
                            "X-File-Size": String(selectedFile.size),
                            "X-Upload-Batch-Id": batchId,
                            "X-Data-Center": dataCenter,
                            "X-Directory": destinationDirectory,
                        },
                    });
                } else {
                    const params = new URLSearchParams({
                        link: selectedLink, data_center: dataCenter, directory: destinationDirectory, batch_id: batchId
                    });
                    if (selectedLink.toLowerCase().startsWith("magnet:?")) {
                        const selectedIndexes = torrentSelections[selectedLink];
                        if (Array.isArray(selectedIndexes) && selectedIndexes.length) {
                            params.set("selected_files", JSON.stringify(selectedIndexes));
                        }
                    }
                    response = await fetch(`${API_URL}/auth/upload-link?${params.toString()}`, {
                        method: "POST", headers: {Authorization: `Bearer ${token}`},
                    });
                }

                if (!response.ok) {
                    let errorMessage = `Upload setup failed (${response.status})`;
                    try {
                        const errorData = await response.json();
                        errorMessage = errorData.detail || errorData.error || errorMessage;
                    } catch {
                        // Keep the HTTP status message when the response is not JSON.
                    }
                    throw new Error(`${selectedFile?.name ? `${selectedFile.name}: ` : ""}${errorMessage}`);
                }

                const {job_id: jobId} = await response.json();
                if (!jobId) throw new Error("The server did not return an upload job ID.");
                batchTransfer.activeJobId = jobId;
                await publishBatch({
                    status: "uploading",
                    current_index: itemNumber - 1,
                    current_job_id: jobId,
                    current_name: displayName,
                    item_index: itemNumber - 1,
                    item_status: "uploading",
                    item_message: "Uploading",
                    message: `Uploading ${itemNumber} of ${queueTotal}`
                });

                if (uploadMode === "file") {
                    try {
                        await uploadFileInChunks(jobId, selectedFile, cancelToken, batchTransfer.xhrs);
                        if (cancelToken.cancelled) throw new Error("Upload task was cancelled.");
                        // Sending browser chunks is not enough: wait until the storage provider
                        // confirms completion before the next queued file starts.
                        await waitForJobCompletion(jobId, token, cancelToken);
                    } catch (error) {
                        const wasCancelled = cancelToken.cancelled || /cancelled/i.test(error instanceof Error ? error.message : "");
                        if (!wasCancelled) {
                            const currentToken = localStorage.getItem("token");
                            if (currentToken) {
                                try {
                                    await fetch(`${API_URL}/auth/upload-jobs/${encodeURIComponent(jobId)}/fail`, {
                                        method: "POST", headers: {
                                            Authorization: `Bearer ${currentToken}`, "Content-Type": "application/json",
                                        }, body: JSON.stringify({message: error instanceof Error ? error.message : "Browser file transfer failed"}),
                                    });
                                } catch (reportError) {
                                    console.error("Could not publish upload failure to the account:", reportError);
                                }
                            }
                        }
                        throw error;
                    } finally {
                        batchTransfer.activeJobId = "";
                    }
                } else {
                    try {
                        await waitForJobCompletion(jobId, token, cancelToken);
                    } finally {
                        batchTransfer.activeJobId = "";
                    }
                }
            };

            let successfulItems = 0;
            const failedItems = [];

            if (uploadMode === "file") {
                // Preserve the nested folder layout, but cancel checks prevent the next
                // directory or file request from starting after the user cancels the task.
                const folderPaths = new Set();
                for (const {relativePath} of filesWithPaths) {
                    const parts = relativePath.split("/").filter(Boolean);
                    parts.pop();
                    let path = directory;
                    for (const part of parts) {
                        path = `${path.replace(/\/$/, "")}/${part}`;
                        folderPaths.add(path);
                    }
                }

                if (folderPaths.size) {
                    await publishBatch({
                        status: "uploading", current_index: 0, current_job_id: "", current_name: "", message: "Preparing folder structure"
                    });
                    const treeResponse = await fetch(`${API_URL}/auth/tree`, {headers: {Authorization: `Bearer ${token}`}});
                    if (!treeResponse.ok) throw new Error(`Could not load existing folders (${treeResponse.status})`);
                    const tree = await treeResponse.json();
                    const existingPaths = new Set((tree.directories || [])
                        .filter((folder) => !folder.is_deleted)
                        .map((folder) => String(folder.path || "").replace(/\\/g, "/").replace(/\/$/, "")));
                    const orderedFolderPaths = Array.from(folderPaths).sort((a, b) => a.split("/").length - b.split("/").length);
                    for (const folderPath of orderedFolderPaths) {
                        if (cancelToken.cancelled) throw new Error("Upload task was cancelled.");
                        if (existingPaths.has(folderPath)) continue;
                        const parentPath = folderPath.slice(0, folderPath.lastIndexOf("/")) || "/";
                        const folderName = folderPath.slice(folderPath.lastIndexOf("/") + 1);
                        const params = new URLSearchParams({directory: parentPath, name: folderName});
                        const folderResponse = await fetch(`${API_URL}/auth/create-folder?${params.toString()}`, {
                            method: "POST", headers: {Authorization: `Bearer ${token}`},
                        });
                        if (!folderResponse.ok) {
                            let errorMessage = `Could not create folder ${folderName} (${folderResponse.status})`;
                            try {
                                const errorData = await folderResponse.json();
                                errorMessage = errorData.detail || errorMessage;
                            } catch {
                                // Keep the status message when no JSON response is available.
                            }
                            throw new Error(errorMessage);
                        }
                        existingPaths.add(folderPath);
                    }
                }

                for (const [index, {file: selectedFile, relativePath}] of filesWithPaths.entries()) {
                    if (cancelToken.cancelled) throw new Error("Upload task was cancelled.");
                    setQueueProgress({current: index + 1, total: filesWithPaths.length});
                    await publishBatch({
                        status: "uploading",
                        current_index: index,
                        completed_items: successfulItems,
                        current_job_id: "",
                        current_name: relativePath,
                        item_index: index,
                        item_status: "uploading",
                        item_message: "Uploading",
                        message: `Uploading ${index + 1} of ${queueTotal}`
                    });
                    const parts = relativePath.split("/").filter(Boolean);
                    parts.pop();
                    const destinationDirectory = parts.length ? `${directory.replace(/\/$/, "")}/${parts.join("/")}` : directory;
                    try {
                        await createJob(selectedFile, "", destinationDirectory, relativePath, index + 1);
                        successfulItems++;
                        await publishBatch({
                            status: "uploading",
                            current_index: index + 1,
                            completed_items: successfulItems,
                            current_job_id: "",
                            current_name: "",
                            item_index: index,
                            item_status: "completed",
                            item_message: "Uploaded successfully",
                            message: `Completed ${successfulItems} of ${queueTotal}`
                        });
                    } catch (error) {
                        if (cancelToken.cancelled || /cancelled/i.test(error instanceof Error ? error.message : "")) throw new Error("Upload task was cancelled.");
                        const failureMessage = error instanceof Error ? error.message : "Upload failed";
                        failedItems.push({name: relativePath, message: failureMessage});
                        await publishBatch({
                            status: "uploading",
                            current_index: index + 1,
                            completed_items: successfulItems,
                            current_job_id: "",
                            current_name: "",
                            item_index: index,
                            item_status: "error",
                            item_message: failureMessage,
                            message: `Failed ${failedItems.length} item${failedItems.length === 1 ? "" : "s"}; continuing queue`
                        });
                    }
                }
            } else {
                for (const [index, selectedLink] of links.entries()) {
                    if (cancelToken.cancelled) throw new Error("Upload task was cancelled.");
                    setQueueProgress({current: index + 1, total: links.length});
                    await publishBatch({
                        status: "uploading",
                        current_index: index,
                        completed_items: successfulItems,
                        current_job_id: "",
                        current_name: selectedLink,
                        message: `Queued ${index + 1} of ${queueTotal}`
                    });
                    try {
                        await createJob(null, selectedLink, directory, selectedLink, index + 1);
                        successfulItems++;
                        await publishBatch({
                            status: "uploading",
                            current_index: index + 1,
                            completed_items: successfulItems,
                            current_job_id: "",
                            current_name: "",
                            item_index: index,
                            item_status: "completed",
                            item_message: "Uploaded successfully",
                            message: `Completed ${successfulItems} of ${queueTotal}`
                        });
                    } catch (error) {
                        if (cancelToken.cancelled || /cancelled/i.test(error instanceof Error ? error.message : "")) throw new Error("Upload task was cancelled.");
                        const failureMessage = error instanceof Error ? error.message : "Import failed";
                        failedItems.push({name: selectedLink, message: failureMessage});
                        await publishBatch({
                            status: "uploading",
                            current_index: index + 1,
                            completed_items: successfulItems,
                            current_job_id: "",
                            current_name: "",
                            item_index: index,
                            item_status: "error",
                            item_message: failureMessage,
                            message: `Failed ${failedItems.length} item${failedItems.length === 1 ? "" : "s"}; continuing queue`
                        });
                    }
                }
            }

            if (cancelToken.cancelled) throw new Error("Upload task was cancelled.");
            if (failedItems.length) {
                await publishBatch({
                    status: "error",
                    current_index: queueTotal,
                    completed_items: successfulItems,
                    current_job_id: "",
                    current_name: "",
                    message: `${failedItems.length} of ${queueTotal} item(s) failed`
                });
            } else {
                await publishBatch({
                    status: "completed",
                    current_index: queueTotal,
                    completed_items: queueTotal,
                    current_job_id: "",
                    current_name: "",
                    message: `Completed all ${queueTotal} item(s)`
                });
            }
        } catch (error) {
            const detail = error instanceof Error ? error.message : "Upload task failed";
            const wasCancelled = Boolean(batchTransfer?.cancelToken.cancelled) || /cancelled/i.test(detail);
            if (batchTransfer && wasCancelled) batchTransfer.cancelToken.cancelled = true;
            if (batchId) {
                const currentToken = localStorage.getItem("token");
                if (currentToken) {
                    try {
                        await fetch(`${API_URL}/auth/upload-batches/${encodeURIComponent(batchId)}`, {
                            method: "PATCH",
                            headers: {Authorization: `Bearer ${currentToken}`, "Content-Type": "application/json"},
                            body: JSON.stringify({
                                status: wasCancelled ? "cancelled" : "error",
                                current_job_id: "",
                                current_name: "",
                                message: wasCancelled ? "Upload task cancelled" : detail
                            }),
                        });
                    } catch (publishError) {
                        console.error("Could not publish terminal upload task status:", publishError);
                    }
                }
            } else {
                setMessage(detail);
                setStatus("error");
            }
            if (!wasCancelled) console.error("Upload task failed:", error);
        } finally {
            if (batchId && window.__storeLimitlessUploadTransfers?.get(batchId) === batchTransfer) {
                window.__storeLimitlessUploadTransfers.delete(batchId);
            }
            uploadRunRef.current = false;
        }
    };

    // ==========================================
    // HANDLE DRAG AND DROP
    // ==========================================
    /**
     * Add dropped files unless an upload is already being configured.
     * @param {import("react").DragEvent<HTMLLabelElement>} event
     * @returns {void}
     */
    const handleDrop = async (event) => {
        event.preventDefault();

        setDragOver(false);

        if (status === "uploading" || uploadMode !== "file") return;

        try {
            const droppedFiles = await getFilesFromDrop(event.dataTransfer);
            if (droppedFiles.length) setFiles((current) => [...current, ...droppedFiles]);
        } catch (error) {
            setMessage(error instanceof Error ? error.message : "Could not read the dropped files or folders.");
            setStatus("error");
        }
    };

    // Choose a folder through the File System Access API when available.
    // Unlike <input webkitdirectory>, this avoids Chromium's extra "Upload N files"
    // confirmation while keeping the folder picker and preserving relative paths.
    const handleChooseFolder = async () => {
        if (status === "uploading") return;

        if (typeof window.showDirectoryPicker !== "function") {
            document.getElementById("folder-input-fallback")?.click();
            return;
        }

        try {
            const directoryHandle = await window.showDirectoryPicker({mode: "read"});
            const selectedFiles = [];

            const walkDirectory = async (handle, parentPath = "") => {
                for await (const entry of handle.values()) {
                    const relativePath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
                    if (entry.kind === "directory") {
                        await walkDirectory(entry, relativePath);
                    } else if (entry.kind === "file") {
                        const file = await entry.getFile();
                        Object.defineProperty(file, "webkitRelativePath", {value: relativePath});
                        selectedFiles.push(file);
                    }
                }
            };

            await walkDirectory(directoryHandle);
            if (selectedFiles.length) {
                setFiles((current) => [...current, ...selectedFiles]);
            } else {
                setMessage("The selected folder contains no files.");
                setStatus("error");
            }
        } catch (error) {
            if (error?.name === "AbortError") return;
            setMessage(error instanceof Error ? error.message : "Could not read the selected folder.");
            setStatus("error");
        }
    };

    // ==========================================
    // CHECK IF UPLOAD BUTTON SHOULD BE DISABLED
    // ==========================================
    const isUploadDisabled = (!dataCenter || status === "uploading" || (uploadMode === "file" && files.length === 0) || (uploadMode === "link" && links.length === 0));

    // Keep the latest state available to the one-time global keyboard listener.
    keyboardActionsRef.current = {isUploadDisabled, directory, navigate, modal, onClose};

    useEffect(() => {
        const handleKeyboardShortcuts = (event) => {
            if (event.altKey || event.ctrlKey || event.metaKey) return;

            const target = event.target;
            const isEditingText = target instanceof HTMLElement && (target.isContentEditable || (target instanceof HTMLInputElement && target.type !== "file") || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement);
            const {
                isUploadDisabled: uploadDisabled, directory: currentDirectory, navigate: goTo, modal: isModal, onClose: closeModal
            } = keyboardActionsRef.current;

            if (event.key === "Escape") {
                event.preventDefault();
                if (isModal) {
                    event.stopPropagation();
                    closeModal?.();
                } else {
                    goTo(directoryToRoute(currentDirectory));
                }
                return;
            }

            if (event.key === "Backspace") {
                if (isEditingText) return;

                event.preventDefault();
                goTo(directoryToRoute(currentDirectory));
                return;
            }

            if (event.key === "Enter") {
                // A newline is needed when pasting or typing multiple links in the textarea.
                if (target instanceof HTMLTextAreaElement) return;

                // Prevent key repeat from starting the same upload more than once.
                event.preventDefault();
                if (event.repeat || uploadDisabled) return;
                uploadButtonRef.current?.click();
            }
        };

        document.addEventListener("keydown", handleKeyboardShortcuts);
        return () => document.removeEventListener("keydown", handleKeyboardShortcuts);
    }, []);

    return (<div
        data-upload-dialog={modal ? "true" : undefined}
        role={modal ? "dialog" : undefined}
        aria-modal={modal ? "true" : undefined}
        aria-label={modal ? "Upload files" : undefined}
        className={modal ? "fixed inset-0 z-[230] flex items-center justify-center bg-black/65 px-4 py-6 backdrop-blur-sm" : "min-h-screen bg-[#171717] text-[#fafafa] flex flex-col"}
        onMouseDown={modal ? (event) => {
            if (event.target === event.currentTarget && status !== "uploading") onClose?.();
        } : undefined}
        style={{fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"}}
    >
        <div
            className={modal ? "max-h-[92vh] w-full max-w-[640px] overflow-y-auto rounded-xl border border-[#343434] bg-[#171717] text-[#fafafa] shadow-2xl" : "min-h-screen flex flex-col"}
            onClick={modal ? (event) => event.stopPropagation() : undefined}
        >
            {/* ==========================================
                TOP BAR
            ========================================== */}
            {!modal && (<header className="h-14 border-b border-[#2e2e2e] bg-[#171717] flex items-center px-6 gap-4">
                <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-md bg-[#3ecf8e] flex items-center justify-center">
                        <span
                            className="material-symbols-outlined text-[#0f0f0f] text-[18px]"
                            style={{fontVariationSettings: "'FILL' 1"}}
                        >
                            cloud
                        </span>
                    </div>
                    <span className="text-sm font-medium tracking-tight">StoreLimitless</span>
                </div>
            </header>)}
            {modal && (<header className="flex items-center justify-between border-b border-[#2e2e2e] px-5 py-4">
                <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#3ecf8e]/20 bg-[#3ecf8e]/10 text-[#3ecf8e]">
                        <span className="material-symbols-outlined text-[20px]" aria-hidden="true">cloud_upload</span>
                    </div>
                    <div>
                        <h2 className="text-sm font-medium text-[#fafafa]">Upload files</h2>
                        <p className="mt-0.5 text-xs text-[#898989]">Destination: {directory === "/home" ? "home" : directory.split("/").filter(Boolean).join(" / ")}</p>
                    </div>
                </div>
                <button type="button" onClick={() => {
                    if (status !== "uploading") onClose?.();
                }} aria-label="Close upload dialog"
                        disabled={status === "uploading"}
                        className="flex items-center gap-2 rounded-md border border-[#363636] px-2.5 py-1.5 text-xs text-[#b4b4b4] transition-colors hover:bg-[#2a2a2a] hover:text-[#fafafa] disabled:cursor-not-allowed disabled:opacity-50">
                    Close
                    <span className="material-symbols-outlined text-[17px]" aria-hidden="true">close</span>
                </button>
            </header>)}

            {/* ==========================================
                MAIN CONTENT
            ========================================== */}
            <div className={modal ? "px-5 py-5 sm:px-6" : "flex-1 flex items-center justify-center px-4 py-12"}>
                <div className={modal ? "w-full" : "w-full max-w-[480px]"}>
                    <h1 className="text-2xl font-medium tracking-tight text-[#fafafa] mb-2">
                        {modal ? "Choose files or links" : "Upload File"}
                    </h1>
                    <p className="text-sm text-[#898989] mb-8">
                        Select a data center and choose how you want to upload your file.
                    </p>

                    {/* ==========================================
                    SELECT DATA CENTER
                ========================================== */}
                    <div className="mb-6">
                        <p className={labelClass}>
                            Data center
                        </p>
                        <div className="flex gap-3">
                            {DATA_CENTERS.map((dc) => (<button
                                key={dc}
                                type="button"
                                onClick={() => setDataCenter(dc)}
                                disabled={status === "uploading"}
                                className={`${optionBase} ${dataCenter === dc ? optionOn : optionOff}`}
                            >
                                <span className={`material-symbols-outlined text-[18px] ${dataCenter === dc ? "text-[#3ecf8e]" : ""}`}>
                                    {dc === "Discord" ? "forum" : "send"}
                                </span>
                                {dc}
                            </button>))}
                        </div>
                    </div>

                    {/* ==========================================
                    SELECT UPLOAD METHOD
                ========================================== */}
                    <div className="mb-6">
                        <p className={labelClass}>
                            Upload method
                        </p>
                        <div className="flex gap-3">

                            {/* LOCAL FILE */}
                            <button
                                type="button"
                                disabled={status === "uploading"}
                                onClick={() => {
                                    setUploadMode("file");
                                    setStatus("idle");
                                }}
                                className={`${optionBase} flex-1 ${uploadMode === "file" ? optionOn : optionOff}`}
                            >
                            <span className={`material-symbols-outlined text-[18px] ${uploadMode === "file" ? "text-[#3ecf8e]" : ""}`}>
                                upload_file
                            </span>
                                Upload File
                            </button>

                            {/* LINK */}
                            <button
                                type="button"
                                disabled={status === "uploading"}
                                onClick={() => {
                                    setUploadMode("link");
                                    setStatus("idle");
                                }}
                                className={`${optionBase} flex-1 ${uploadMode === "link" ? optionOn : optionOff}`}
                            >
                            <span className={`material-symbols-outlined text-[18px] ${uploadMode === "link" ? "text-[#3ecf8e]" : ""}`}>
                                add_link
                            </span>
                                Link
                            </button>
                        </div>
                    </div>

                    {/* ==========================================
                    LOCAL FILE UPLOAD
                ========================================== */}
                    {uploadMode === "file" && (<div className="mb-6">
                        <p className={labelClass}>Files</p>
                        <label
                            htmlFor="file-input"
                            onDragOver={(event) => {
                                event.preventDefault();
                                if (status !== "uploading") setDragOver(true);
                            }}
                            onDragLeave={() => setDragOver(false)}
                            onDrop={handleDrop}
                            className={["flex flex-col items-center justify-center w-full min-h-40 rounded-lg", "border border-dashed cursor-pointer transition-colors", dragOver ? "border-[#3ecf8e] bg-[#3ecf8e]/5" : files.length ? "border-[#3ecf8e]/40 bg-[#1f1f1f]" : "border-[#363636] bg-[#1f1f1f] hover:border-[#4d4d4d] hover:bg-[#232323]", status === "uploading" ? "opacity-60 cursor-not-allowed" : "",].join(" ")}
                        >
                        <span className={`material-symbols-outlined text-[36px] mb-2 ${files.length ? "text-[#3ecf8e]" : "text-[#4d4d4d]"}`}>
                            {files.length ? "library_add_check" : "cloud_upload"}
                        </span>
                            <div className="text-center px-4 py-2">
                                <p className="text-sm text-[#898989]">
                                    Drop files or folders here, or <span
                                    className="text-[#fafafa] underline underline-offset-4 decoration-[#4d4d4d]">browse</span>
                                </p>
                                <p className="text-xs text-[#707070] mt-1">Select files, choose a folder with nested files, or drop files and folders
                                    here.</p>
                            </div>
                            <input
                                id="file-input"
                                type="file"
                                multiple
                                className="hidden"
                                disabled={status === "uploading"}
                                onChange={(event) => {
                                    const selectedFiles = Array.from(event.target.files || []);
                                    if (selectedFiles.length) setFiles((current) => [...current, ...selectedFiles]);
                                    event.target.value = "";
                                }}
                            />
                        </label>
                        <div className="mt-3 flex flex-wrap items-center gap-3">
                            <button type="button" onClick={handleChooseFolder} disabled={status === "uploading"}
                                    className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-[#363636] px-3 py-2 text-xs text-[#b4b4b4] transition-colors hover:border-[#4d4d4d] hover:text-[#fafafa] disabled:opacity-50">
                                <span className="material-symbols-outlined text-[17px]" aria-hidden="true">folder_open</span>
                                Choose folder
                            </button>
                            <input
                                id="folder-input-fallback"
                                type="file"
                                multiple
                                webkitdirectory=""
                                directory=""
                                className="hidden"
                                disabled={status === "uploading"}
                                onChange={(event) => {
                                    const selectedFiles = Array.from(event.target.files || []);
                                    if (selectedFiles.length) setFiles((current) => [...current, ...selectedFiles]);
                                    event.target.value = "";
                                }}
                            />
                            <p className="text-xs text-[#707070]">Nested folders are recreated during upload.</p>
                        </div>
                        {files.length > 0 && (<div className="mt-3 rounded-lg border border-[#2e2e2e] bg-[#1f1f1f] divide-y divide-[#2e2e2e]">
                            <div className="flex items-center justify-between px-3 py-2 text-xs text-[#898989]">
                                <span>{files.length} file{files.length === 1 ? "" : "s"} selected</span>
                                <button type="button" disabled={status === "uploading"} onClick={() => setFiles([])}
                                        className="text-[#898989] hover:text-[#ff6369] disabled:opacity-50">Clear all
                                </button>
                            </div>
                            <div className="max-h-48 overflow-y-auto">
                                {files.map((selectedFile, index) => (
                                    <div key={`${selectedFile.name}-${selectedFile.size}-${selectedFile.lastModified}-${index}`}
                                         className="flex items-center gap-3 px-3 py-2.5">
                                        <span className="material-symbols-outlined text-[18px] text-[#898989]">draft</span>
                                        <div className="min-w-0 flex-1">
                                            <p className="truncate text-sm text-[#fafafa]"
                                               title={getUploadRelativePath(selectedFile)}>{getUploadRelativePath(selectedFile)}</p>
                                            <p className="text-xs text-[#898989]">{formatBytes(selectedFile.size)}</p>
                                        </div>
                                        <button type="button" disabled={status === "uploading"}
                                                onClick={() => setFiles((current) => current.filter((_, fileIndex) => fileIndex !== index))}
                                                aria-label={`Remove ${selectedFile.name}`} title="Remove file"
                                                className="rounded p-1 text-[#898989] hover:bg-white/5 hover:text-[#ff6369] disabled:opacity-50">
                                            <span className="material-symbols-outlined text-[18px]">close</span>
                                        </button>
                                    </div>))}
                            </div>
                        </div>)}
                    </div>)}

                    {/* ==========================================
                    LINK INPUT
                ========================================== */}
                    {uploadMode === "link" && (<div className="mb-6">
                        <p className={labelClass}>
                            File link
                        </p>
                        <div className="bg-[#1f1f1f] border border-[#2e2e2e] rounded-lg p-4">
                            <div className="flex items-center gap-3 mb-4">
                                <div className="w-9 h-9 rounded-md border border-[#363636] bg-[#242424] flex items-center justify-center">
                                <span className="material-symbols-outlined text-[18px] text-[#898989]">
                                    link
                                </span>
                                </div>
                                <div>
                                    <p className="text-sm text-[#fafafa]">
                                        Import from Link
                                    </p>
                                    <p className="text-xs text-[#898989]">
                                        Import one HTTP(S) URL or one torrent magnet per task.
                                    </p>
                                </div>
                            </div>

                            {/* LINK INPUT */}
                            <div className="relative">
                            <span className="material-symbols-outlined absolute left-3 top-3 text-[18px] text-[#707070]">
                                link
                            </span>
                                <input
                                    type="text"
                                    inputMode="url"
                                    autoComplete="off"
                                    spellCheck={false}
                                    value={link}
                                    disabled={status === "uploading"}
                                    onPaste={(event) => {
                                        const pasted = event.clipboardData.getData("text/plain").trim();
                                        if (/[\r\n]/.test(pasted)) {
                                            event.preventDefault();
                                            setMessage("Only one link can be imported per task. Paste a single URL or torrent magnet.");
                                            setStatus("error");
                                        }
                                    }}
                                    onChange={(event) => {
                                        setLink(event.target.value);
                                        setMessage("");
                                        setStatus("idle");
                                        setTorrentSelectionQueue([]);
                                        setTorrentSelectionIndex(0);
                                        setTorrentSelections({});
                                        setTorrentSelectionReady(false);
                                        setTorrentSelectionModal(false);
                                    }}
                                    placeholder="https://example.com/file.zip or magnet:?xt=urn:btih:..."
                                    className={["w-full h-11 rounded-md border border-[#363636]", "bg-white/[0.026] pl-10 pr-3 py-2 text-sm text-[#fafafa]", "placeholder:text-[#707070] outline-none transition-colors", "focus:border-[#707070] focus:ring-2 focus:ring-[#4d4d4d]/50", "disabled:opacity-60",].join(" ")}
                                />
                            </div>

                            {/* LINK DETECTED */}
                            {links.length > 0 && (<div className="flex items-center gap-1.5 mt-3 text-[#3ecf8e]">
                            <span className="material-symbols-outlined text-[16px]">
                                check_circle
                            </span>
                                <p className="text-xs">
                                    Link ready to import
                                </p>
                            </div>)}
                            <p className="text-xs text-[#707070] mt-3 leading-relaxed">
                                Enter one publicly accessible HTTP(S) URL or one valid torrent magnet URI. Multiple links in a single task are
                                disabled. Torrent contents
                                are downloaded completely before their files are uploaded, and torrent folder paths are preserved.
                            </p>
                        </div>
                    </div>)}

                    {/* ==========================================
                    UPLOAD BUTTON
                ========================================== */}
                    <button
                        ref={uploadButtonRef}
                        type="button"
                        onClick={handleUpload}
                        disabled={isUploadDisabled || torrentPreviewLoading}
                        className={["w-full h-[38px] flex items-center justify-center gap-2 rounded-md border", "border-[#3ecf8e]/40 bg-[#006239] text-sm text-[#fafafa] transition-colors", "hover:border-[#3ecf8e]/70 hover:bg-[#007a46] disabled:opacity-50", "disabled:cursor-not-allowed",].join(" ")}
                    >
                        {torrentPreviewLoading ? (<>Inspecting torrent files...</>) : status === "uploading" ? (<>
                        <span className="material-symbols-outlined text-[16px] animate-spin">
                            autorenew
                        </span>
                            {uploadMode === "link" ? `Importing ${queueProgress.current} of ${queueProgress.total}...` : `Uploading ${queueProgress.current} of ${queueProgress.total}...`}
                        </>) : (<>
                        <span className="material-symbols-outlined text-[16px]">
                            cloud_upload
                        </span>
                            {uploadMode === "link" ? `${torrentSelectionReady ? "Start importing" : "Import link"}` : `Upload ${files.length} File${files.length === 1 ? "" : "s"}`}
                        </>)}
                    </button>

                    {status === "error" && (<p role="alert" className="mt-3 text-sm text-[#ff6369]">
                        {message || "Upload setup failed. Please try again."}
                    </p>)}
                </div>
            </div>
        </div>
        {torrentSelectionModal && torrentSelectionQueue[torrentSelectionIndex] && (() => {
            const currentTorrent = torrentSelectionQueue[torrentSelectionIndex];
            const selectedSet = new Set(currentTorrent.selected);
            const totalSize = currentTorrent.files.reduce((total, item) => selectedSet.has(Number(item.index)) ? total + Number(item.size || 0) : total, 0);
            const formatTorrentBytes = (bytes) => {
                if (bytes < 1000) return `${bytes} B`;
                const units = ["KB", "MB", "GB", "TB"];
                let value = bytes;
                let unit = -1;
                do {
                    value /= 1000;
                    unit++;
                } while (value >= 1000 && unit < units.length - 1);
                return `${value.toFixed(1)} ${units[unit]}`;
            };
            const updateSelected = (fileIndex, checked) => {
                setTorrentSelectionQueue((current) => current.map((torrent, index) => index !== torrentSelectionIndex ? torrent : ({
                    ...torrent,
                    selected: checked ? [...new Set([...torrent.selected, Number(fileIndex)])] : torrent.selected.filter((value) => value !== Number(fileIndex)),
                })));
            };
            const continueSelection = () => {
                if (currentTorrent.selected.length === 0) return;
                setTorrentSelections((current) => ({...current, [currentTorrent.link]: currentTorrent.selected}));
                if (torrentSelectionIndex + 1 < torrentSelectionQueue.length) {
                    setTorrentSelectionIndex((current) => current + 1);
                } else {
                    setTorrentSelectionReady(true);
                    setTorrentSelectionModal(false);
                    setMessage("Torrent file selections saved. Start the import to continue.");
                    setStatus("idle");
                }
            };
            return (<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4">
                <section role="dialog" aria-modal="true" aria-labelledby="torrent-selection-title"
                         className="w-full max-w-2xl overflow-hidden rounded-xl border border-[#363636] bg-[#1c1c1c] shadow-2xl">
                    <header className="flex items-start justify-between border-b border-[#2e2e2e] px-5 py-4">
                        <div className="min-w-0">
                            <p className="text-xs text-[#3ecf8e]">Torrent {torrentSelectionIndex + 1} of {torrentSelectionQueue.length}</p>
                            <h2 id="torrent-selection-title" className="mt-1 truncate text-lg font-medium text-[#fafafa]">{currentTorrent.name}</h2>
                            <p className="mt-1 text-xs text-[#898989]">All files are selected by default. Deselect anything you do not want
                                imported.</p>
                        </div>
                        <button type="button" onClick={() => setTorrentSelectionModal(false)} aria-label="Close torrent selection"
                                className="rounded-md p-1 text-[#898989] hover:bg-white/5 hover:text-white">
                            <span className="material-symbols-outlined">close</span>
                        </button>
                    </header>
                    <div className="flex items-center justify-between border-b border-[#2e2e2e] px-5 py-3 text-xs text-[#898989]">
                        <span>{currentTorrent.selected.length} of {currentTorrent.files.length} files selected</span>
                        <span>{formatTorrentBytes(totalSize)}</span>
                    </div>
                    <div className="max-h-[55vh] overflow-y-auto divide-y divide-[#292929]">
                        {currentTorrent.files.map((item) => {
                            const checked = selectedSet.has(Number(item.index));
                            return (<label key={item.index} className="flex cursor-pointer items-center gap-3 px-5 py-3 hover:bg-white/[0.025]">
                                <input type="checkbox" checked={checked} onChange={(event) => updateSelected(item.index, event.target.checked)}
                                       className="h-4 w-4 accent-[#3ecf8e]"/>
                                <span className="material-symbols-outlined text-[18px] text-[#898989]">draft</span>
                                <span className="min-w-0 flex-1 break-words text-sm text-[#e5e5e5]">{item.path}</span>
                                <span className="shrink-0 text-xs text-[#898989]">{formatTorrentBytes(Number(item.size || 0))}</span>
                            </label>);
                        })}
                    </div>
                    <footer className="flex items-center justify-between gap-3 border-t border-[#2e2e2e] px-5 py-4">
                        <button type="button" onClick={() => {
                            setTorrentSelectionQueue([]);
                            setTorrentSelectionIndex(0);
                            setTorrentSelections({});
                            setTorrentSelectionReady(false);
                            setTorrentSelectionModal(false);
                        }} className="rounded-md border border-[#363636] px-3 py-2 text-sm text-[#b4b4b4] hover:bg-white/5">Cancel
                        </button>
                        <button type="button" disabled={!currentTorrent.selected.length} onClick={continueSelection}
                                className="rounded-md border border-[#3ecf8e]/40 bg-[#006239] px-4 py-2 text-sm text-white hover:bg-[#007a46] disabled:cursor-not-allowed disabled:opacity-50">
                            {torrentSelectionIndex + 1 < torrentSelectionQueue.length ? "Next torrent" : "Save selections"}
                        </button>
                    </footer>
                </section>
            </div>);
        })()}
    </div>);
}
