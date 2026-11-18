import {useEffect, useRef, useState} from "react";
import {useNavigate, useSearchParams} from "react-router-dom";

/** @typedef {{cancelled: boolean}} UploadCancelToken */
/** @typedef {Error & {fatal?: boolean}} UploadRequestError */

/**
 * @typedef {Object} StoredUploadJob
 * @property {string} jobId
 * @property {string} name
 * @property {"file" | "link"} kind
 * @property {string} directory
 * @property {"Discord" | "Telegram"} dataCenter
 * @property {string} status
 * @property {number} progress
 * @property {number} transfer
 * @property {number} total
 * @property {string} message
 * @property {number} createdAt
 */
/**
 * Read a saved upload tray value while tolerating malformed or outdated browser storage.
 * @param {string} key
 * @returns {StoredUploadJob[]}
 */
function readStoredUploadJobs(key) {
    try {
        const parsed = JSON.parse(localStorage.getItem(key) || "[]");
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}


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
    if (bytes < 1024) return `${bytes} B`;

    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes;
    let unit = -1;

    do {
        value /= 1024;
        unit++;
    } while (value >= 1024 && unit < units.length - 1);

    return `${value.toFixed(1)} ${units[unit]}`;
};

/**
 * Render the file and URL upload workflow.
 * @returns {import("react").ReactElement}
 */
export default function UploadPage({modal = false, targetDirectory = "", onClose = null} = {}) {
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();

    // Current directory from dashboard
    const directory = targetDirectory || searchParams.get("directory") || "/home";

    // Upload states
    /** @type {[File[], import("react").Dispatch<import("react").SetStateAction<File[]>>]} */
    const [files, setFiles] = useState([]);
    const [link, setLink] = useState("");
    const links = link.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    /** @type {["file" | "link", import("react").Dispatch<import("react").SetStateAction<"file" | "link">>]} */
    const [uploadMode, setUploadMode] = useState("file");

    // Other states
    /** @type {["Discord" | "Telegram", import("react").Dispatch<import("react").SetStateAction<"Discord" | "Telegram">>]} */
    const [dataCenter, setDataCenter] = useState("Discord");
    const [message, setMessage] = useState("");
    const [status, setStatus] = useState("idle");
    const [dragOver, setDragOver] = useState(false);
    const uploadButtonRef = useRef(null);
    const keyboardActionsRef = useRef({isUploadDisabled: true, directory, navigate});

    /**
     * Wait for a specified number of milliseconds.
     * @param {number} milliseconds
     * @returns {Promise<void>}
     */
    const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

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
                    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
                } catch {
                    setMessage(`Invalid HTTP(S) URL on line ${index + 1}: ${value}`);
                    setStatus("error");
                    return;
                }
            }
        }

        setStatus("uploading");

        const jobsKey = "storelimitless_upload_jobs";
        const token = localStorage.getItem("token");
        if (!token) {
            navigate("/");
            return;
        }

        try {
            /**
             * Create a server job, persist its tray entry, and start its transfer if needed.
             * @param {File | null} selectedFile
             * @param {string} selectedLink
             * @returns {Promise<void>}
             */
            const createJob = async (selectedFile, selectedLink = "") => {
                let res;
                if (uploadMode === "file") {
                    res = await fetch(`${API_URL}/auth/upload`, {
                        method: "POST", headers: {
                            Authorization: `Bearer ${token}`, "X-File-Name": selectedFile.name, "X-Data-Center": dataCenter, "X-Directory": directory,
                        },
                    });
                } else {
                    const params = new URLSearchParams({link: selectedLink, data_center: dataCenter, directory});
                    res = await fetch(`${API_URL}/auth/upload-link?${params.toString()}`, {
                        method: "POST", headers: {Authorization: `Bearer ${token}`},
                    });
                }

                if (!res.ok) {
                    let errorMessage = `Upload setup failed (${res.status})`;
                    try {
                        const errorData = await res.json();
                        errorMessage = errorData.detail || errorData.error || errorMessage;
                    } catch {
                        // Keep the HTTP status message when the response is not JSON.
                    }
                    throw new Error(`${selectedFile?.name ? `${selectedFile.name}: ` : ""}${errorMessage}`);
                }

                const {job_id: jobId} = await res.json();
                if (!jobId) throw new Error("The server did not return an upload job ID.");

                const job = {
                    jobId,
                    name: uploadMode === "file" ? selectedFile.name : selectedLink,
                    kind: uploadMode,
                    directory,
                    dataCenter,
                    status: "uploading",
                    progress: 0,
                    transfer: 0,
                    total: uploadMode === "file" ? selectedFile.size : 0,
                    message: uploadMode === "file" ? "Sending file to server" : "Importing from link",
                    createdAt: Date.now(),
                };
                const existingJobs = readStoredUploadJobs(jobsKey);
                localStorage.setItem(jobsKey, JSON.stringify([job, ...existingJobs.filter(item => item.jobId !== jobId)]));

                if (!window.__storeLimitlessUploadTransfers) window.__storeLimitlessUploadTransfers = new Map();
                const cancelToken = {cancelled: false};
                /** @type {Set<XMLHttpRequest>} */
                const jobXhrs = new Set();
                window.__storeLimitlessUploadTransfers.set(jobId, {cancelToken, xhrs: jobXhrs});

                if (uploadMode === "file") {
                    void uploadFileInChunks(jobId, selectedFile, cancelToken, jobXhrs).then(() => {
                        window.__storeLimitlessUploadTransfers?.delete(jobId);
                    }).catch((error) => {
                        cancelToken.cancelled = true;
                        window.__storeLimitlessUploadTransfers?.delete(jobId);
                        for (const xhr of jobXhrs) xhr.abort();
                        const jobs = JSON.parse(localStorage.getItem(jobsKey) || "[]");
                        localStorage.setItem(jobsKey, JSON.stringify(jobs.map(item => item.jobId === jobId ? {
                            ...item, status: "error", message: error instanceof Error ? error.message : "Upload failed"
                        } : item)));
                        console.error(`Upload transfer failed (${selectedFile.name}):`, error);
                    });
                }
            };

            if (uploadMode === "file") {
                // Create a separate backend job for every selected file. Transfers
                // start independently and continue after this page navigates away.
                for (const selectedFile of files) {
                    await createJob(selectedFile);
                }
            } else {
                // Submit one independent server-side import job for every URL line.
                for (const selectedLink of links) {
                    await createJob(null, selectedLink);
                }
            }

            if (modal) onClose?.(); else navigate(directoryToRoute(directory));
        } catch (error) {
            console.error("Upload setup error:", error);
            setMessage(error instanceof Error ? error.message : "Upload failed");
            setStatus("error");
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
    const handleDrop = (event) => {
        event.preventDefault();

        setDragOver(false);

        if (status === "uploading" || uploadMode !== "file") return;

        const droppedFiles = Array.from(event.dataTransfer.files || []);
        if (droppedFiles.length) setFiles((current) => [...current, ...droppedFiles]);
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
                <button type="button" onClick={() => onClose?.()} disabled={status === "uploading"} aria-label="Close upload dialog"
                        className="flex h-8 w-8 items-center justify-center rounded-md text-[#898989] transition-colors hover:bg-[#2a2a2a] hover:text-[#fafafa] disabled:opacity-50">
                    <span className="material-symbols-outlined text-[19px]" aria-hidden="true">close</span>
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
                                    Drop files here or <span
                                    className="text-[#fafafa] underline underline-offset-4 decoration-[#4d4d4d]">browse</span>
                                </p>
                                <p className="text-xs text-[#707070] mt-1">Select multiple files at once, or add more by dropping them here.</p>
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
                                            <p className="truncate text-sm text-[#fafafa]" title={selectedFile.name}>{selectedFile.name}</p>
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
                            File links
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
                                        Import from Links
                                    </p>
                                    <p className="text-xs text-[#898989]">
                                        Paste one link per line. All links will be imported.
                                    </p>
                                </div>
                            </div>

                            {/* LINK INPUT */}
                            <div className="relative">
                            <span className="material-symbols-outlined absolute left-3 top-3 text-[18px] text-[#707070]">
                                link
                            </span>
                                <textarea
                                    rows={5}
                                    value={link}
                                    disabled={status === "uploading"}
                                    onChange={(event) => setLink(event.target.value)}
                                    placeholder={"https://example.com/file-one.zip\nhttps://example.com/file-two.mkv\nhttps://example.com/file-three.pdf"}
                                    className={["w-full min-h-28 rounded-md border border-[#363636]", "bg-white/[0.026] pl-10 pr-3 py-2 text-sm text-[#fafafa]", "placeholder:text-[#707070] outline-none transition-colors", "focus:border-[#707070] focus:ring-2 focus:ring-[#4d4d4d]/50", "disabled:opacity-60",].join(" ")}
                                />
                            </div>

                            {/* LINK DETECTED */}
                            {links.length > 0 && (<div className="flex items-center gap-1.5 mt-3 text-[#3ecf8e]">
                            <span className="material-symbols-outlined text-[16px]">
                                check_circle
                            </span>
                                <p className="text-xs">
                                    {links.length} link{links.length === 1 ? "" : "s"} ready to import
                                </p>
                            </div>)}
                            <p className="text-xs text-[#707070] mt-3 leading-relaxed">
                                Use one publicly accessible HTTP(S) link per line. Each link gets its own upload
                                job and appears separately in the dashboard upload tray.
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
                        disabled={isUploadDisabled}
                        className={["w-full h-[38px] flex items-center justify-center gap-2 rounded-md border", "border-[#3ecf8e]/40 bg-[#006239] text-sm text-[#fafafa] transition-colors", "hover:border-[#3ecf8e]/70 hover:bg-[#007a46] disabled:opacity-50", "disabled:cursor-not-allowed",].join(" ")}
                    >
                        {status === "uploading" ? (<>
                        <span className="material-symbols-outlined text-[16px] animate-spin">
                            autorenew
                        </span>
                            {uploadMode === "link" ? "Importing..." : "Uploading..."}
                        </>) : (<>
                        <span className="material-symbols-outlined text-[16px]">
                            cloud_upload
                        </span>
                            {uploadMode === "link" ? `Import ${links.length} Link${links.length === 1 ? "" : "s"}` : `Upload ${files.length} File${files.length === 1 ? "" : "s"}`}
                        </>)}
                    </button>

                    {status === "error" && (<p role="alert" className="mt-3 text-sm text-[#ff6369]">
                        {message || "Upload setup failed. Please try again."}
                    </p>)}
                </div>
            </div>
        </div>
    </div>);
}
