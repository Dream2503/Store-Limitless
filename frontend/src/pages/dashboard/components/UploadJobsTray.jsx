import {useEffect, useRef, useState} from "react";
import {useLocation, useNavigate} from "react-router-dom";
import {API_URL, iconBtn, iconBtnDanger} from "../constants.js";
import {formatFileSize, getToken} from "../utils.js";

/* -------------------------------------------------------------------------- */
/*  Pure helpers                                                              */
/* -------------------------------------------------------------------------- */

const num = (value) => Math.max(0, Number(value) || 0);
const normalizePath = (value) => String(value ?? "").trim().replaceAll("\\", "/").replace(/\/+$/, "");
const leafOf = (value) => normalizePath(value).split("/").filter(Boolean).at(-1) || "";

/** Return only the leaf filename, never the torrent's enclosing directory. */
function getFileName(file) {
    const path = normalizePath(file?.path);
    const name = normalizePath(file?.name);
    return leafOf(path.includes("/") ? path : name || path) || "Unnamed file";
}

const getTorrentKey = (torrent, index) => String(torrent?.queueIndex ?? index);
const getItemSize = (item) => num(item?.total) || num(item?.size);
const isHttpUrl = (value) => /^https?:\/\//i.test(String(value ?? "").trim());

function isYouTubeUrl(value) {
    try {
        const host = new URL(String(value ?? "").trim()).hostname.toLowerCase().replace(/^www\./, "");
        return host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com");
    } catch {
        return false;
    }
}

const PLACEHOLDER_NAMES = new Set(["link", "upload", "torrent", "watch", "shorts", "embed", "live", "video", "update_name_from_backend"]);

/** True when the backend hasn't produced a real title yet (still a URL, video id or placeholder). */
function isUnresolvedName(value, sourceIsYouTube) {
    if (isHttpUrl(value)) return true;
    const name = leafOf(value);
    if (!name || PLACEHOLDER_NAMES.has(name.toLowerCase())) return true;
    return sourceIsYouTube && /^[a-z0-9_-]{11}$/i.test(name);
}

const LINK_STAGES = {
    "processing telegram message": "Processing Telegram message",
    "starting download": "Preparing download",
    "downloading file": "Downloading file",
    "downloading and uploading file": "Downloading file",
    "downloading video": "Downloading video",
    "downloading audio": "Downloading audio",
    "merging audio and video": "Merging audio and video",
    "uploading file": "Uploading",
    "uploading": "Uploading",
    "finalizing upload": "Finalizing upload",
};
const STAGES_WITH_PERCENT = new Set(["Downloading video", "Downloading audio", "Uploading"]);
const TORRENT_STATUS_POLL_MS = 500;

function formatLinkStage(message) {
    const stage = String(message ?? "").trim().toLowerCase();
    if (stage.startsWith("upload complete of id=")) return "Finalizing upload";
    return LINK_STAGES[stage] || "";
}

/** Does this batch item belong to the given torrent? Prefers the queue index, falls back to the link. */
function belongsToTorrent(item, torrent, index) {
    const queueIndex = Number(torrent.queueIndex ?? index);
    return item.torrent_index != null ? Number(item.torrent_index) === queueIndex : item.torrent_link === torrent.link;
}

function getItemProgress(item, status) {
    const size = getItemSize(item);
    const done = status === "completed";
    const percent = done ? 100 : size ? Math.min(100, num(item.transfer) * 100 / size) : 0;
    return {size, percent};
}

/* -------------------------------------------------------------------------- */
/*  Visual tokens                                                             */
/* -------------------------------------------------------------------------- */

const FILL = "bg-gradient-to-r from-[#279b68] to-[#3ecf8e]";
const TEXT = {
    success: "text-[#3ecf8e]", danger: "text-[#ff7278]", active: "text-[#e8f0eb]", muted: "text-[#8ca296]",
};

/** One source of truth for how each item status looks. */
const ITEM_STATUS = {
    completed: {icon: "check_circle", label: "Done", tone: "success"},
    error: {icon: "error", label: "Failed", tone: "danger"},
    cancelled: {icon: "cancel", label: "Cancelled", tone: "danger"},
    skipped: {icon: "cancel", label: "Skipped", tone: "danger"},
    downloading: {icon: "cloud_download", label: "Downloading", tone: "active", transferring: true},
    uploading: {icon: "cloud_upload", label: "Uploading", tone: "active", transferring: true},
    downloaded: {icon: "cloud_done", label: "Downloaded", tone: "success"},
    queued: {icon: "schedule", label: "Queued", tone: "muted"},
    available: {icon: "schedule", label: "Available", tone: "muted"},
};

const ROW_SELECTOR = '[data-torrent-file-row="true"]';

/* -------------------------------------------------------------------------- */
/*  Small building blocks                                                     */

/* -------------------------------------------------------------------------- */

function Icon({name, className = "", style}) {
    return <span className={`material-symbols-outlined shrink-0 ${className}`} style={style} aria-hidden="true">{name}</span>;
}

function ProgressBar({percent, muted = false, className = ""}) {
    return <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}
                className={`h-1 overflow-hidden rounded-full bg-[#29372e] ${className}`}>
        <div className={`h-full rounded-full transition-[width] duration-500 ease-linear motion-reduce:transition-none ${muted ? "bg-[#7f8f84]" : FILL}`}
             style={{width: `${percent}%`}}/>
    </div>;
}

function formatRate(bytesPerSecond) {
    return `${formatFileSize(bytesPerSecond)}/s`;
}

function formatEta(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "Calculating ETA";
    const value = Math.ceil(seconds);
    if (value < 60) return `${value}s left`;
    if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s left`;
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    return `${hours}h ${minutes}m left`;
}

/**
 * Estimate throughput from successive server progress snapshots.
 *
 * Keep this hook in the persistent file row/card rather than inside conditional
 * progress-detail markup. Brief status changes and a quiet polling interval should
 * not throw away the last useful rate estimate.
 */
function useTransferMetrics(transfer, total) {
    const [speed, setSpeed] = useState(0);
    const previous = useRef({transfer: num(transfer), at: Date.now()});

    useEffect(() => {
        const now = Date.now();
        const currentTransfer = num(transfer);
        const prior = previous.current;
        const elapsed = (now - prior.at) / 1000;

        if (currentTransfer < prior.transfer) {
            // A new transfer phase or resumed transfer reset its byte counter.
            setSpeed(0);
        } else if (elapsed >= 0.25 && currentTransfer > prior.transfer) {
            const measuredSpeed = (currentTransfer - prior.transfer) / elapsed;
            setSpeed((currentSpeed) => currentSpeed > 0
                ? currentSpeed * 0.7 + measuredSpeed * 0.3
                : measuredSpeed);
        }

        previous.current = {transfer: currentTransfer, at: now};
    }, [transfer]);

    const remaining = Math.max(0, num(total) - num(transfer));
    const eta = speed > 0 && remaining > 0 ? remaining / speed : null;
    return {speed, eta, remaining};
}

function TransferDetails({transfer, total, status, compact = false, speed = 0, eta = null}) {
    if (num(total) <= 0) return null;

    const transferLabel = status === "downloading" ? "Downloaded" : "Transferred";
    const done = status === "completed" || num(transfer) >= num(total);

    return <div
        className={`flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 font-mono tabular-nums text-[#789083] ${compact ? "ml-[23px] mt-1 text-[9px]" : "mt-1 text-[10px]"}`}>
        <span>{transferLabel} {formatFileSize(Math.min(num(transfer), num(total)))} / {formatFileSize(total)}</span>
        {!done && <>
            <span className="text-[#3b5144]">·</span>
            <span className="text-[#9ab5a5]">{formatRate(speed)}</span>
            <span className="text-[#3b5144]">·</span>
            <span>{eta !== null ? formatEta(eta) : "Calculating ETA"}</span>
        </>}
    </div>;
}

/** A single file/item line, shared by torrent panels and plain multi-item batches. */
function ItemRow({item, status}) {
    const meta = ITEM_STATUS[status] || ITEM_STATUS.queued;
    const {size, percent} = getItemProgress(item, status);
    const transfer = status === "completed" ? size : num(item.transfer);
    const {speed, eta} = useTransferMetrics(transfer, size);
    const name = getFileName(item);
    const showPercent = meta.transferring && size > 0;

    return <div className="min-w-0 rounded-md px-2 py-1.5 transition-colors hover:bg-white/[0.03]">
        <div className="flex min-w-0 items-center gap-2">
            <Icon name={meta.icon}
                  className={`text-[15px] ${meta.tone === "muted" ? "text-[#5f7567]" : meta.tone === "danger" ? TEXT.danger : TEXT.success}`}/>
            <span className="min-w-0 flex-1 truncate text-[11px] text-[#c6d2ca]" title={name}>{name}</span>
            <span className={`shrink-0 text-[10px] tabular-nums ${TEXT[meta.tone]}`}>
                {meta.label}{showPercent ? ` · ${percent.toFixed(0)}%` : ""}
            </span>
        </div>
        {showPercent && <>
            <ProgressBar percent={percent} className="ml-[23px] mt-1.5"/>
            <TransferDetails transfer={transfer} total={size} status={status} compact speed={speed} eta={eta}/>
        </>}
    </div>;
}

/* -------------------------------------------------------------------------- */
/*  Torrent progress panel (one per torrent inside a multi-link batch)        */

/* -------------------------------------------------------------------------- */

function TorrentLinkPanel({torrent, items, parentStatus, index}) {
    const torrentItems = Array.isArray(items) ? items : [];
    const previewFiles = Array.isArray(torrent?.files) ? torrent.files : [];
    const files = torrentItems.length ? torrentItems : previewFiles;
    const selectedCount = torrentItems.length;
    const has = (status) => torrentItems.some((item) => item.status === status);
    const completeCount = torrentItems.filter((item) => item.status === "completed").length;
    const allComplete = selectedCount > 0 && completeCount === selectedCount;
    const hasError = has("error");

    const totalBytes = torrentItems.reduce((sum, item) => sum + getItemSize(item), 0);
    const transferredBytes = torrentItems.reduce((sum, item) => sum + (item.status === "completed" ? getItemSize(item) : num(item.transfer)), 0);
    const percent = allComplete ? 100 : totalBytes > 0 ? Math.min(100, transferredBytes * 100 / totalBytes) : 0;

    let status;
    if (hasError) status = "Some files failed"; else if (allComplete) status = "Completed"; else if (has("uploading")) status = "Uploading"; else if (has("downloading")) status = "Downloading"; else if (has("downloaded")) status = "Downloaded, waiting to upload"; else if (parentStatus === "cancelled") status = "Cancelled"; else if (selectedCount) status = "Queued"; else if (parentStatus === "queued") status = `${previewFiles.length} files available`; else status = "Awaiting selection";

    const statusTone = hasError ? TEXT.danger : allComplete ? TEXT.success : TEXT.muted;
    const title = String(torrent?.name || `Torrent ${index + 1}`);

    return <section className="min-w-0 overflow-hidden rounded-lg border border-[#293b30] bg-black/10">
        <header className="flex min-w-0 items-start gap-2.5 bg-[#151d18]/70 px-3 py-2.5">
            <Icon name="folder_zip" className="mt-0.5 text-[16px] text-[#3ecf8e]"/>
            <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-[#dce9e0]" title={title}>{title}</p>
                <p className={`mt-0.5 text-[11px] ${statusTone}`} title={String(torrent?.link || "")}>
                    {status}{selectedCount ? `, ${completeCount} of ${selectedCount} files done` : ""}
                </p>
            </div>
            {selectedCount > 0 && <span className="shrink-0 font-mono text-[11px] tabular-nums text-[#718477]">{percent.toFixed(0)}%</span>}
        </header>
        {selectedCount > 0 && <ProgressBar percent={percent} className="rounded-none"/>}
        <div className="space-y-0.5 p-1.5">
            {files.length ? files.map((file, i) => <ItemRow key={`${file.file_index ?? file.index ?? getFileName(file)}-${i}`}
                                                            item={file} status={String(file.status || (selectedCount ? "queued" : "available"))}/>) :
                <p className="px-2 py-2 text-[11px] text-[#718477]">Waiting for torrent file selection.</p>}
        </div>
    </section>;
}

/* -------------------------------------------------------------------------- */
/*  Torrent import workflow (kept out of the component)                       */

/* -------------------------------------------------------------------------- */

function patchBatch(token, jobId, body) {
    return fetch(`${API_URL}/auth/upload-batches/${encodeURIComponent(jobId)}`, {
        method: "PATCH", headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"}, body: JSON.stringify(body),
    });
}

function mergeTorrentProgress(items, owns, progressItems) {
    const byIndex = new Map(progressItems.map((p) => [Number(p.file_index), p]));
    return items.map((item) => {
        if (!owns(item)) return item;

        // The API reports paths relative to the torrent root while the picker stores the
        // full path. Match by stable file index first, then normalize both names as fallback.
        const p = byIndex.get(Number(item.file_index)) || progressItems.find((candidate) =>
            normalizePath(candidate.name).toLowerCase() === normalizePath(item.path || item.name).toLowerCase() ||
            getFileName(candidate) === getFileName(item)
        );
        if (!p) return item;

        const status = String(p.status || "downloading");
        const message = status === "downloaded"
            ? "Downloaded · waiting to upload"
            : status === "downloading"
                ? "Downloading"
                : String(p.message || item.message || (status === "uploading" ? "Uploading" : status));
        return {...item, status, message, transfer: num(p.transfer), total: num(p.total) || num(item.size)};
    });
}

/**
 * Starts each selected torrent in queue order and waits for it to finish before the next,
 * keeping storage uploads sequential. `onStarted` fires once the batch is persisted so the
 * caller can dismiss the picker while the import continues.
 */
async function runTorrentImport(job, selection, onStarted) {
    const token = getToken();
    if (!token) throw new Error("Your session has expired.");

    const torrents = Array.isArray(job.torrentFiles) ? job.torrentFiles : [];
    if (torrents.length !== 1) {
        throw new Error("Import one torrent per task. Multiple torrent links are not supported.");
    }

    const selectedFor = (torrent, i) => selection[getTorrentKey(torrent, i)] || [];
    const patch = async (body) => {
        const response = await patchBatch(token, job.jobId, body);
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || `Could not update torrent task (HTTP ${response.status}).`);
        }
        return response;
    };
    const totalFiles = torrents.reduce((count, torrent, i) => count + selectedFor(torrent, i).length, 0);
    if (totalFiles === 0) throw new Error("Select at least one file from the torrent.");

    let items = torrents.flatMap((torrent, i) => torrent.files
        .filter((file) => selectedFor(torrent, i).includes(Number(file.index)))
        .map((file) => ({
            name: getFileName(file),
            size: Number(file.size) || 0,
            status: "queued",
            message: "Queued",
            torrent_link: torrent.link,
            torrent_index: Number(torrent.queueIndex ?? i),
            file_index: Number(file.index),
            path: file.path,
        })));

    await patch({
        status: "uploading",
        message: `Starting torrent download for ${totalFiles} selected file${totalFiles === 1 ? "" : "s"}`,
        items,
        total_items: totalFiles,
        completed_items: 0,
        current_name: items[0]?.name || "",
    });

    for (const [i, torrent] of torrents.entries()) {
        const selected = selectedFor(torrent, i);
        if (!selected.length) continue;

        const owns = (item) => belongsToTorrent(item, torrent, i);
        const updateOwn = (fn) => {
            items = items.map((item) => owns(item) ? fn(item) : item);
        };
        const firstName = items.find(owns)?.name || "Unnamed file";

        updateOwn((item) => ({...item, status: "downloading", message: `Downloading ${item.name}`}));
        await patch({items, current_name: firstName, message: `Downloading ${firstName}`});

        const params = new URLSearchParams({
            link: torrent.link, data_center: job.dataCenter, directory: job.directory, batch_id: job.jobId, selected_files: JSON.stringify(selected),
        });
        const response = await fetch(`${API_URL}/auth/upload-link?${params}`, {method: "POST", headers: {Authorization: `Bearer ${token}`}});
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(body.detail || `Could not start ${firstName}`);
        }
        const {job_id: importJobId} = await response.json();
        await patch({current_job_id: importJobId, current_name: firstName, message: `Importing ${firstName}`});

        // Keep the picker open until the backend has accepted the child job and its ID
        // has been recorded. A failed request can then be corrected and retried without
        // leaving an orphaned, terminal-looking batch in the tray.
        onStarted();

        while (true) {
            const statusResponse = await fetch(`${API_URL}/public/upload/${encodeURIComponent(importJobId)}/status`, {
                headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
            });
            if (!statusResponse.ok) {
                const err = await statusResponse.json().catch(() => ({}));
                const message = `Could not check status for ${firstName}: ${err.detail || err.message || `HTTP ${statusResponse.status}`}`;
                const failure = new Error(message);
                if (statusResponse.status === 401 || statusResponse.status === 403 || statusResponse.status === 404) {
                    failure.terminalStatus = "error";
                }
                throw failure;
            }
            const jobStatus = await statusResponse.json();

            if (Array.isArray(jobStatus.torrent_items) && jobStatus.torrent_items.length) {
                items = mergeTorrentProgress(items, owns, jobStatus.torrent_items);
                const own = items.filter(owns);
                const active = own.find((item) => item.status === "downloading" || item.status === "uploading") || own.find((item) => item.status === "downloaded") || own[0];
                const activeName = active?.name || firstName;
                const progressPatch = await patch({items, current_name: activeName, message: `${active?.message || "Processing"}: ${activeName}`});
                if (!progressPatch.ok) {
                    throw new Error(`Torrent is running, but its progress could not be saved (HTTP ${progressPatch.status}).`);
                }
            }

            if (jobStatus.status === "completed") break;
            if (jobStatus.status === "cancelled" || jobStatus.status === "error") {
                const failure = new Error(jobStatus.message || `${firstName} failed`);
                failure.terminalStatus = jobStatus.status;
                throw failure;
            }
            await new Promise((resolve) => window.setTimeout(resolve, TORRENT_STATUS_POLL_MS));
        }

        updateOwn((item) => {
            const size = Number(item.size) || Number(item.total) || 0;
            return {...item, status: "completed", message: "Completed", transfer: size, total: size};
        });
        const completedPatch = await patch({
            items,
            completed_items: items.filter((item) => item.status === "completed").length,
            current_name: items.find(owns)?.name || firstName,
            message: `Completed ${firstName}`,
        });
        if (!completedPatch.ok) throw new Error(`Torrent file finished, but completion state could not be saved (HTTP ${completedPatch.status}).`);
    }

    const final = await patch({status: "completed", message: "All selected torrent files uploaded", completed_items: items.length});
    if (!final.ok) throw new Error("Files finished, but task status could not be saved.");
}

/* -------------------------------------------------------------------------- */
/*  File-selection dialog                                                     */

/* -------------------------------------------------------------------------- */

function Kbd({children}) {
    return <kbd className="rounded border border-[#2f4237] bg-[#1a251e] px-1.5 py-0.5 font-sans text-[10px] text-[#9bbba7]">{children}</kbd>;
}

function TorrentSelectionDialog({job, torrentIndex, selection, starting, error, onToggle, onSetAll, onClose, onNext, onStart}) {
    const dialogRef = useRef(null);
    const startRef = useRef(null);

    const torrents = job.torrentFiles;
    const torrent = torrents[torrentIndex];
    const key = getTorrentKey(torrent, torrentIndex);
    const selected = new Set(selection[key] || []);
    const isLast = torrentIndex >= torrents.length - 1;
    const selectedSize = torrent.files.reduce((sum, file) => selected.has(Number(file.index)) ? sum + num(file.size) : sum, 0);
    const nothingSelected = torrents.every((t, i) => !(selection[getTorrentKey(t, i)] || []).length);
    const allSelected = selected.size === torrent.files.length;

    // Move focus to the first file whenever a new torrent is shown.
    useEffect(() => {
        const firstRow = dialogRef.current?.querySelector(ROW_SELECTOR);
        (firstRow || dialogRef.current)?.focus();
    }, [torrentIndex]);

    function handleKeyDown(event) {
        const stop = () => {
            event.preventDefault();
            event.stopPropagation();
        };
        if (event.key === "Escape") {
            stop();
            if (!starting) onClose();
            return;
        }
        if (event.key === "Enter") {
            stop();
            if (!starting) startRef.current?.click();
            return;
        }

        const rows = Array.from(dialogRef.current?.querySelectorAll(ROW_SELECTOR) || []);
        if (!rows.length) return;
        const current = rows.indexOf(document.activeElement);

        if (event.key === "ArrowDown") {
            stop();
            rows[current < 0 ? 0 : Math.min(rows.length - 1, current + 1)]?.focus();
        } else if (event.key === "ArrowUp") {
            stop();
            rows[current < 0 ? rows.length - 1 : Math.max(0, current - 1)]?.focus();
        } else if ((event.key === " " || event.code === "Space") && current >= 0) {
            stop();
            rows[current].click();
        }
    }

    return <div className="fixed inset-0 z-[160] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
        <section ref={dialogRef} tabIndex={-1} onKeyDown={handleKeyDown} role="dialog" aria-modal="true" aria-labelledby="torrent-selection-title"
                 className="flex max-h-[min(88dvh,54rem)] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-[#303b34] bg-[#111713] shadow-2xl shadow-black/50 outline-none">
            <header className="flex items-start justify-between gap-3 border-b border-[#29362e] px-5 py-4">
                <div className="min-w-0">
                    {torrents.length > 1 && <p className="text-xs text-[#3ecf8e]">Torrent {torrentIndex + 1} of {torrents.length}</p>}
                    <h2 id="torrent-selection-title" className="mt-1 truncate text-base font-medium text-[#e8f0eb]"
                        title={torrent.name}>{torrent.name}</h2>
                    <p className="mt-1 text-xs text-[#8ca296]">
                        Choose which files to download{torrents.length > 1 ? ". The next torrent opens when you continue." : "."}
                    </p>
                </div>
                <button type="button" disabled={starting} onClick={onClose} aria-label="Close file selection"
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[#9bbba7] transition-colors hover:bg-white/5 hover:text-white disabled:cursor-not-allowed disabled:opacity-50">
                    <Icon name="close" className="text-[18px]"/>
                </button>
            </header>

            <div className="flex items-center justify-between gap-3 border-b border-[#29362e] bg-[#151d18] px-5 py-2 text-xs">
                <span className="tabular-nums text-[#8ca296]">
                    {selected.size} of {torrent.files.length} selected{selected.size ? `, ${formatFileSize(selectedSize)}` : ""}
                </span>
                <button type="button" onClick={() => onSetAll(key, allSelected ? [] : torrent.files.map((f) => Number(f.index)))}
                        className="rounded px-2 py-1 font-medium text-[#9be8bd] transition-colors hover:bg-white/5">
                    {allSelected ? "Clear all" : "Select all"}
                </button>
            </div>

            <div className="upload-tray-scrollbar min-h-0 flex-1 divide-y divide-[#222d26] overflow-y-auto">
                {torrent.files.map((file) => {
                    const checked = selected.has(Number(file.index));
                    const name = getFileName(file);
                    return <div key={`${torrent.link}-${file.index}`} data-torrent-file-row="true" role="checkbox" aria-checked={checked} tabIndex={0}
                                onClick={() => onToggle(key, file.index)}
                                className={`group grid cursor-pointer select-none grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-5 py-2.5 transition-colors motion-reduce:transition-none hover:bg-[#19231c] focus:outline focus:outline-1 focus:-outline-offset-2 focus:outline-[#3ecf8e]/60 ${checked ? "bg-[#3ecf8e]/[0.04]" : ""}`}>
                        <span className="flex min-w-0 items-center gap-3">
                            <Icon name={checked ? "check_box" : "check_box_outline_blank"}
                                  className={`text-[19px] ${checked ? TEXT.success : "text-[#5f7567] group-hover:text-[#9bbba7]"}`}
                                  style={checked ? {fontVariationSettings: "'FILL' 1"} : undefined}/>
                            <span className={`min-w-0 flex-1 break-words text-sm ${checked ? "text-[#f3f7f4]" : "text-[#b9c7be]"}`}
                                  title={name}>{name}</span>
                        </span>
                        <span className="shrink-0 font-mono text-xs tabular-nums text-[#7f9586]">{formatFileSize(Number(file.size) || 0)}</span>
                    </div>;
                })}
            </div>

            {error && <p role="alert" className="border-t border-[#29362e] px-5 py-3 text-xs text-[#ff7278]">{error}</p>}

            <footer className="flex items-center justify-between gap-3 border-t border-[#29362e] px-5 py-3.5">
                <div className="hidden items-center gap-1.5 text-[11px] text-[#7f9586] sm:flex">
                    <Kbd>↑</Kbd><Kbd>↓</Kbd> move <Kbd>Space</Kbd> toggle <Kbd>Enter</Kbd> {isLast ? "start" : "next"} <Kbd>Esc</Kbd> cancel
                </div>
                <div className="ml-auto flex items-center gap-2">
                    <button type="button" disabled={starting} onClick={onClose}
                            className="rounded-md border border-[#713b3d] bg-[#3a1c20] px-3.5 py-2 text-xs font-medium text-[#ff9298] transition-colors hover:border-[#a94a50] hover:bg-[#512329] disabled:cursor-not-allowed disabled:opacity-50">
                        Cancel
                    </button>
                    <button ref={startRef} type="button" disabled={starting || (isLast && nothingSelected)} onClick={isLast ? onStart : onNext}
                            className="rounded-md border border-[#2e8058] bg-[#17643d] px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-[#1d794a] disabled:cursor-not-allowed disabled:opacity-50">
                        {starting ? "Starting…" : isLast ? "Start download" : "Next torrent"}
                    </button>
                </div>
            </footer>
        </section>
    </div>;
}

/* -------------------------------------------------------------------------- */
/*  One tray row per upload batch                                             */

/* -------------------------------------------------------------------------- */

function describeJob(job) {
    const isComplete = job.status === "completed";
    const isError = job.status === "error";
    const isCancelled = job.status === "cancelled";
    const isQueued = job.status === "queued";
    const isActive = isQueued || job.status === "uploading";

    const items = Array.isArray(job.items) ? job.items : [];
    const torrents = Array.isArray(job.torrentFiles) ? job.torrentFiles : [];
    const itemCount = Number(job.totalItems) || items.length || 0;
    const singleItem = itemCount === 1 ? items[0] : null;
    const rawItemName = String(singleItem?.name || "");
    const rawCurrentName = String(job.currentName || "");
    const sourceIsYouTube = isYouTubeUrl(rawItemName) || isYouTubeUrl(rawCurrentName);

    const currentName = leafOf(rawCurrentName);
    const usableCurrent = currentName && !isUnresolvedName(rawCurrentName, sourceIsYouTube) ? currentName : "";
    const singleName = singleItem ? getFileName(singleItem) : "";
    const usableItem = singleName && !isUnresolvedName(rawItemName, sourceIsYouTube) ? singleName : "";
    const title = singleItem ? usableCurrent || usableItem || (sourceIsYouTube ? "YouTube video" : singleName || job.name || "Upload task") : job.kind === "link" && (itemCount > 1 || torrents.length > 1) ? "Link import" : job.name || "Upload task";

    /*
     * Prefer item-level byte counts when available. This gives multi-file tasks a
     * true whole-task byte total rather than showing only the current child's size.
     * Torrent tasks use the same aggregation so their parent counter agrees with
     * the nested per-file progress panels.
     */
    const hasTorrentItems = torrents.length > 0 && items.length > 0;
    const hasDetailedItems = items.length > 0 && items.some((item) => num(item.size) > 0 || num(item.total) > 0);
    const itemTotal = hasDetailedItems ? items.reduce((sum, item) => sum + (num(item.size) || num(item.total)), 0) : 0;

    // Torrent files are downloaded to the server before they are uploaded to the
    // selected storage provider. During the upload phase, `downloaded` means the
    // source download is ready, not that the file has been stored. Count only
    // completed storage uploads plus the bytes of the currently uploading file.
    // While the torrent itself is still downloading, keep using its download
    // progress so the parent bar remains useful during that earlier phase.
    const torrentUploadPhase = hasTorrentItems && items.some((item) => item.status === "uploading" || item.status === "completed");
    const itemTransfer = hasDetailedItems ? items.reduce((sum, item) => {
        const size = num(item.size) || num(item.total);
        let transferred;
        if (item.status === "completed") {
            transferred = size;
        } else if (torrentUploadPhase && hasTorrentItems) {
            transferred = item.status === "uploading" ? num(item.transfer) : 0;
        } else {
            transferred = num(item.transfer);
        }
        return sum + (size > 0 ? Math.min(size, transferred) : transferred);
    }, 0) : 0;
    const total = itemTotal > 0 ? itemTotal : num(job.total);
    const transfer = itemTotal > 0 ? Math.min(itemTotal, itemTransfer) : num(job.transfer);
    const rawPercent = itemTotal > 0 ? transfer * 100 / itemTotal : Number(job.progress) || 0;
    const percent = isComplete ? 100 : Math.max(0, Math.min(99, rawPercent));

    const stage = job.kind === "link" ? formatLinkStage(job.message) : "";
    const stagePercent = total > 0 ? Math.max(0, Math.min(99, transfer * 100 / total)) : null;
    const stageText = stage ? `${stage}${stagePercent !== null && STAGES_WITH_PERCENT.has(stage) ? ` · ${stagePercent.toFixed(0)}%` : ""}` : "";
    const finishedFromItems = hasTorrentItems ? items.filter((item) => item.status === "completed").length : Number(job.completedItems) || 0;
    const finished = Math.min(itemCount, finishedFromItems);
    const itemsLabel = `${itemCount} items`;

    let statusText;
    if (isComplete) statusText = singleItem ? "Completed" : `Completed · ${itemsLabel}`; else if (isError) statusText = job.message || "Some items failed"; else if (isCancelled) statusText = "Cancelled"; else if (isQueued) statusText = singleItem ? "Queued" : `Queued · ${itemsLabel}`; else if (stageText) statusText = stageText; else if (singleItem) statusText = sourceIsYouTube ? "Processing video" : "Uploading"; else statusText = `${finished} of ${itemCount} complete${currentName && !isHttpUrl(rawCurrentName) ? ` · ${currentName}` : ""}`;

    const tone = isError || isCancelled ? "danger" : isComplete ? "success" : "muted";
    const icon = isError ? "error" : isCancelled ? "cancel" : isComplete ? "check_circle" : isQueued ? "schedule" : "upload_file";
    const iconTone = tone === "muted" ? (isQueued ? TEXT.success : "text-[#779184]") : TEXT[tone];

    return {isQueued, isActive, itemCount, title, statusText, statusTone: TEXT[tone], icon, iconTone, percent, transfer, total};
}

function UploadJobCard({job, onSelectFiles, onCancel, onDismiss}) {
    const {isQueued, isActive, itemCount, title, statusText, statusTone, icon, iconTone, percent, transfer, total} = describeJob(job);
    const {speed, eta} = useTransferMetrics(transfer, total);
    const torrents = job.torrentFiles || [];
    const items = Array.isArray(job.items) ? job.items : [];
    const hasPicker = torrents.length === 1 && job.status === "queued";

    return <article
        className="rounded-lg border border-[#212c25] bg-[#151d18]/50 px-3 py-2.5 transition-colors hover:border-[#2d4337] hover:bg-[#19231c]">
        <div className="flex items-start gap-2.5">
            <Icon name={icon} className={`mt-0.5 text-[19px] ${iconTone}`}/>

            <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-medium text-[#e8f0eb]" title={title}>{title}</p>
                <p className={`mt-0.5 text-[11px] ${statusTone}`}
                   title={job.message}>{torrents.length > 1 && job.status === "queued" ? "Multiple-link imports are disabled. Submit one link per task." : statusText}</p>

                {hasPicker && <button type="button" onClick={() => onSelectFiles(job)}
                                      className="mt-2 rounded-md border border-[#2e8058] bg-[#173c29] px-2.5 py-1.5 text-[11px] font-medium text-[#9be8bd] transition-colors hover:bg-[#205538]">
                    Select files and start
                </button>}

                {isActive && <div className="mt-2.5">
                    <ProgressBar percent={isQueued ? 0 : percent} muted={isQueued}/>
                    <p className="mt-1 text-right font-mono text-[10px] tabular-nums text-[#718477]">
                        {isQueued ? "Waiting" : `${percent.toFixed(1)}%`}
                    </p>
                    {!isQueued && <TransferDetails transfer={transfer} total={total}
                                                   status={job.status === "completed" ? "completed" : (/download/i.test(String(job.message || "")) ? "downloading" : "uploading")}
                                                   speed={speed} eta={eta}/>}
                </div>}


                {itemCount > 1 && items.length > 0 && <div className="mt-2.5 space-y-0.5 rounded-lg border border-[#29362e] bg-black/10 p-1.5">
                    {items.map((item, i) => <ItemRow key={`${getFileName(item)}-${i}`} item={item} status={String(item.status || "queued")}/>)}
                </div>}
            </div>

            {isActive ? <button type="button" onClick={() => onCancel(job.jobId)} className={`${iconBtnDanger} shrink-0`}
                                title="Cancel this upload task and skip queued items" aria-label={`Cancel upload task ${job.name}`}>
                <Icon name="close" className="text-[17px]"/>
            </button> : <button type="button" onClick={() => onDismiss(job.jobId)} className={`${iconBtn} shrink-0`}
                                title="Remove task from tray" aria-label={`Remove upload task ${job.name}`}>
                <Icon name="close" className="text-[16px]"/>
            </button>}
        </div>
    </article>;
}

/* -------------------------------------------------------------------------- */
/*  Tray                                                                      */
/* -------------------------------------------------------------------------- */

export default function UploadJobsTray({uploadJobs, cancelUploadJob, dismissUploadJob, clearAllUploadJobs, fullscreen = false}) {
    const navigate = useNavigate();
    const location = useLocation();
    const returnTo = typeof location.state?.returnTo === "string" && /^\/home(?:\/|$)/.test(location.state.returnTo)
        ? location.state.returnTo
        : "/home";
    const [selectionJob, setSelectionJob] = useState(null); // non-null means the picker is open
    const [selection, setSelection] = useState({});         // torrentKey -> selected file indexes
    const [selectionIndex, setSelectionIndex] = useState(0);
    const [starting, setStarting] = useState(false);
    const [error, setError] = useState("");
    // Batches that have already prompted automatically, so the 1-second polling refresh
    // can't reopen a picker the user deliberately closed.
    const promptedRef = useRef(new Set());

    function openSelection(job) {
        if (!job || !Array.isArray(job.torrentFiles) || job.torrentFiles.length !== 1) return;
        promptedRef.current.add(job.jobId);
        setSelectionJob(job);
        setSelectionIndex(0);
        setError("");
        setSelection(Object.fromEntries(job.torrentFiles.map((torrent, i) => [getTorrentKey(torrent, i), (Array.isArray(torrent.files) ? torrent.files : []).map((file) => Number(file.index)),])));
    }

    function closeSelection() {
        setSelectionJob(null);
        setError("");
    }

    // Once torrent metadata has resolved, prompt for file selection automatically.
    useEffect(() => {
        if (selectionJob) return;
        const queued = uploadJobs.find((job) => job.status === "queued" && job.torrentFiles?.length === 1 && !promptedRef.current.has(job.jobId));
        if (queued) openSelection(queued);
    }, [uploadJobs, selectionJob]);

    function toggleFile(torrentKey, fileIndex) {
        setSelection((current) => {
            const next = new Set(current[torrentKey] || []);
            const index = Number(fileIndex);
            if (next.has(index)) next.delete(index); else next.add(index);
            return {...current, [torrentKey]: [...next]};
        });
    }

    async function startImport() {
        if (!selectionJob || starting) return;
        const job = selectionJob;
        let pickerClosed = false;
        setStarting(true);
        setError("");
        try {
            await runTorrentImport(job, selection, () => {
                pickerClosed = true;
                setSelectionJob(null);
                setStarting(false);
            });
        } catch (err) {
            const message = err instanceof Error ? err.message : "Could not start torrent download.";
            const token = getToken();

            if (!pickerClosed) {
                setError(message);
                // A start failure must remain retryable. Restore the queued state instead
                // of making this task terminal before the torrent child was accepted.
                if (token) {
                    void patchBatch(token, job.jobId, {
                        status: "queued",
                        message: `Could not start torrent import: ${message}`,
                    }).catch((patchError) => console.error("Could not restore torrent task after start failure:", patchError));
                }
            } else if (err?.terminalStatus) {
                if (token) {
                    void patchBatch(token, job.jobId, {
                        status: err.terminalStatus,
                        message,
                    }).catch((patchError) => console.error("Could not save torrent task failure:", patchError));
                }
            } else {
                // A temporary progress/API failure after the child started must not mark
                // the parent terminal; the backend reconciles the batch from child state.
                if (token) {
                    void patchBatch(token, job.jobId, {
                        status: "uploading",
                        message: `Torrent import started; waiting for server progress: ${message}`,
                    }).catch((patchError) => console.error("Could not save torrent recovery state:", patchError));
                }
            }
        } finally {
            if (!pickerClosed) setStarting(false);
        }
    }

    if (!uploadJobs.length && !selectionJob && !fullscreen) return null;

    const activeCount = uploadJobs.filter((job) => job.status === "queued" || job.status === "uploading").length;

    return (<>
        <style>{`
            .upload-tray-scrollbar { scrollbar-width: thin; scrollbar-color: #3b6955 transparent; overscroll-behavior: contain; }
            .upload-tray-scrollbar::-webkit-scrollbar { width: 5px; }
            .upload-tray-scrollbar::-webkit-scrollbar-track { background: transparent; margin-block: 8px; }
            .upload-tray-scrollbar::-webkit-scrollbar-thumb { background: #355343; border: 1px solid transparent; border-radius: 999px; background-clip: padding-box; transition: background-color 180ms ease; }
            .upload-tray-scrollbar::-webkit-scrollbar-thumb:hover { background: #3ecf8e; background-clip: padding-box; }
            @media (prefers-reduced-motion: reduce) { .upload-tray-scrollbar::-webkit-scrollbar-thumb { transition: none; } }
        `}</style>

        <aside aria-label="Uploads"
               className={`min-w-0 overflow-hidden bg-[#111713] ${fullscreen ? "flex h-[100dvh] w-full flex-col" : "fixed bottom-4 right-4 z-[120] w-[min(25rem,calc(100vw-2rem))] rounded-xl border border-[#303b34] shadow-2xl shadow-black/40"}`}>

            <header className="flex items-center justify-between gap-3 border-b border-[#29362e] bg-[#151d18] px-4 py-3">
                <div className="flex min-w-0 items-center gap-2.5">
                    <Icon name="cloud_upload" className="text-[19px] text-[#3ecf8e]"/>
                    <h2 className="text-sm font-medium text-[#e8f0eb]">Upload tasks</h2>
                    {activeCount > 0 &&
                        <span className="rounded-full border border-[#2b4938] bg-[#1c2c22] px-2 py-0.5 text-[11px] tabular-nums text-[#9bbba7]">
                        {activeCount} active
                    </span>}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    <button type="button" onClick={() => fullscreen
                        ? navigate(returnTo, {replace: true})
                        : navigate("/upload-tray", {
                            state: {returnTo: `${location.pathname}${location.search}${location.hash}`},
                        })}
                            className="flex h-7 w-7 items-center justify-center rounded-md text-[#9bbba7] transition-colors hover:bg-white/5 hover:text-[#f3f7f4]"
                            title={fullscreen ? "Return to previous directory" : "Open full-screen upload tray"}
                            aria-label={fullscreen ? "Minimize upload tray and return to previous directory" : "Maximize upload tray"}>
                        <Icon name={fullscreen ? "close_fullscreen" : "open_in_full"} className="text-[17px]"/>
                    </button>
                    <button type="button" onClick={() => clearAllUploadJobs?.()}
                            className="rounded-md px-2 py-1 text-[11px] text-[#9bbba7] transition-colors hover:bg-white/5 hover:text-[#f3f7f4]"
                            title="Cancel active tasks and clear all task history">
                        Clear all
                    </button>
                </div>
            </header>

            <div className={`upload-tray-scrollbar min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2 ${fullscreen ? "" : "max-h-80"}`}>
                {!uploadJobs.length && <div className="flex min-h-48 flex-col items-center justify-center px-4 py-10 text-center">
                    <Icon name="cloud_upload" className="text-3xl text-[#3b5144]"/>
                    <p className="mt-3 text-sm text-[#c6d2ca]">No upload tasks yet</p>
                    <p className="mt-1 text-xs text-[#718477]">New uploads appear here with live progress.</p>
                </div>}
                {uploadJobs.slice().reverse().map((job) => <UploadJobCard key={job.jobId} job={job} onSelectFiles={openSelection}
                                                                          onCancel={cancelUploadJob} onDismiss={dismissUploadJob}/>)}
            </div>

            {selectionJob && selectionJob.torrentFiles?.[selectionIndex] && <TorrentSelectionDialog
                job={selectionJob}
                torrentIndex={selectionIndex}
                selection={selection}
                starting={starting}
                error={error}
                onToggle={toggleFile}
                onSetAll={(key, indexes) => setSelection((current) => ({...current, [key]: indexes}))}
                onClose={closeSelection}
                onNext={() => {
                    setError("");
                    setSelectionIndex((i) => i + 1);
                }}
                onStart={startImport}
            />}
        </aside>
    </>);
}