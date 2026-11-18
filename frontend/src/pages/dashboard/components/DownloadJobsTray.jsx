import React from "react";

function formatBytes(value) {
    const bytes = Math.max(0, Number(value) || 0);
    if (bytes < 1000) return `${Math.round(bytes)} B`;
    const units = ["KB", "MB", "GB", "TB", "PB"];
    let size = bytes / 1000;
    let index = 0;
    while (size >= 1000 && index < units.length - 1) {
        size /= 1000;
        index += 1;
    }
    return `${size.toFixed(1)} ${units[index]}`;
}

export default function DownloadJobsTray({jobs, onPause, onResume, onRemove, onClearCompleted, readOnly = false, embedded = false}) {
    if (!jobs.length) return null;
    const activeCount = jobs.filter((job) => job.status === "downloading").length;
    const queuedCount = jobs.filter((job) => job.status === "queued").length;
    return <section
        className={embedded
            ? "overflow-hidden rounded-lg border border-[#29362e] bg-black/10 text-[#e7eee9]"
            : "fixed bottom-4 left-4 z-[120] w-[min(25rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[#303b34] bg-[#111713] text-[#e7eee9] shadow-2xl shadow-black/40"}>
        <header className={`flex items-center gap-2 border-b border-[#29352d] ${embedded ? "px-3 py-2" : "px-4 py-3"}`}>
            <span className="material-symbols-outlined text-[18px] text-[#3ecf8e]" aria-hidden="true">download</span>
            <span className={embedded ? "text-xs font-medium" : "text-sm font-medium"}>Downloads</span>
            <span className="rounded-full border border-[#315642] bg-[#1a2a20] px-2 py-0.5 text-[10px] text-[#a5d9b8]">{activeCount} active</span>
            {queuedCount > 0 && <span className="rounded-full border border-[#424a45] bg-[#232b25] px-2 py-0.5 text-[10px] text-[#c6d2ca]">{queuedCount} queued</span>}
            {!readOnly && <button type="button" onClick={onClearCompleted} className="ml-auto text-xs text-[#a8b7ad] hover:text-white">Clear completed</button>}
        </header>
        <div className={embedded ? "space-y-2 p-2" : "max-h-72 space-y-2 overflow-y-auto p-2"}>
            {jobs.map((job) => {
                const percent = job.total > 0 ? Math.min(100, Math.floor((job.transferred / job.total) * 100)) : 0;
                const terminal = job.status === "completed" || job.status === "error";
                return <article key={job.id} className="rounded-lg border border-[#29352d] bg-[#151e18] p-3">
                    <div className="flex items-start gap-2">
                        <span className="material-symbols-outlined mt-0.5 text-[17px] text-[#76d6a8]"
                              aria-hidden="true">{job.status === "completed" ? "check_circle" : job.status === "error" ? "error" : job.status === "queued" ? "schedule" : job.status === "paused" ? "pause_circle" : "downloading"}</span>
                        <div className="min-w-0 flex-1">
                            <p className="truncate text-xs font-medium" title={job.name}>{job.name}</p>
                            <p className="mt-1 text-[11px] text-[#9bad9f]">{job.status === "queued" ? job.message || "Queued · waiting for the previous download" : job.status === "downloading" ? (job.streamOnly || job.blobFallback || job.viewerDownload) && job.total <= 0 ? `${job.message || "Downloading"} · ${formatBytes(job.transferred)} downloaded` : `${formatBytes(job.transferred)} / ${formatBytes(job.total)} · ${percent}%` : job.status === "paused" ? job.streamOnly ? job.message || "Paused · ZIP will restart from the beginning" : job.blobFallback ? "Paused · resume continues from saved progress" : job.viewerDownload ? job.message || "Paused" : `Paused · ${formatBytes(job.transferred)} of ${formatBytes(job.total)}` : job.status === "completed" ? `Completed · ${formatBytes(job.total || job.transferred)}` : job.message || "Download failed"}</p>
                        </div>
                        {!readOnly && !terminal && ["downloading", "queued"].includes(job.status) &&
                            <button type="button" onClick={() => onPause(job.id)} title={job.status === "queued" ? "Pause queued download" : "Pause download"} aria-label={`Pause ${job.name}`}
                                    className="rounded p-1 text-[#a8b7ad] hover:bg-[#27342b] hover:text-white"><span
                                className="material-symbols-outlined text-[17px]">pause</span></button>}
                        {!readOnly && !terminal && job.status === "paused" &&
                            <button type="button" onClick={() => onResume(job.id)} title="Resume download" aria-label={`Resume ${job.name}`}
                                    className="rounded p-1 text-[#3ecf8e] hover:bg-[#27342b]"><span
                                className="material-symbols-outlined text-[17px]">play_arrow</span></button>}
                        {!readOnly && <button type="button" onClick={() => onRemove(job.id)} title="Remove task" aria-label={`Remove ${job.name}`}
                                className="rounded p-1 text-[#849389] hover:bg-[#27342b] hover:text-white"><span
                            className="material-symbols-outlined text-[16px]">close</span></button>}
                    </div>
                    {job.status === "downloading" && <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#29352d]">
                        <div className={`h-full rounded-full bg-[#3ecf8e] transition-[width] duration-200 ${(job.streamOnly || job.blobFallback || job.viewerDownload) && job.total <= 0 ? "w-1/3 animate-pulse" : ""}`} style={(job.streamOnly || job.blobFallback || job.viewerDownload) && job.total <= 0 ? undefined : {width: `${percent}%`}}/>
                    </div>}
                </article>;
            })}
        </div>
    </section>;
}
