import {iconBtn, iconBtnDanger} from "../constants.js";
import {formatFileSize} from "../utils.js";

/** Displays active and completed upload jobs in the dashboard tray. */
export default function UploadJobsTray({
                                           uploadJobs, cancelUploadJob, dismissUploadJob,
                                       }) {
    return (<>
        <style>{`
                .upload-tray-scrollbar {
                    scrollbar-width: thin;
                    scrollbar-color: #3b6955 transparent;
                    overscroll-behavior: contain;
                }

                .upload-tray-scrollbar::-webkit-scrollbar {
                    width: 5px;
                }

                .upload-tray-scrollbar::-webkit-scrollbar-track {
                    background: transparent;
                    margin-block: 8px;
                }

                .upload-tray-scrollbar::-webkit-scrollbar-thumb {
                    background: #355343;
                    border-radius: 999px;
                    border: 1px solid transparent;
                    background-clip: padding-box;
                    transition: background-color 180ms ease;
                }

                .upload-tray-scrollbar::-webkit-scrollbar-thumb:hover {
                    background: #3ecf8e;
                    background-clip: padding-box;
                }

                .upload-tray-scrollbar::-webkit-scrollbar-corner {
                    background: transparent;
                }

                @media (prefers-reduced-motion: reduce) {
                    .upload-tray-scrollbar::-webkit-scrollbar-thumb {
                        transition: none;
                    }
                }
            `}</style>

        {uploadJobs.length > 0 && (<aside
            aria-label="Uploads"
            className="fixed bottom-4 right-4 z-[120] w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[#303b34] bg-[#111713] shadow-2xl shadow-black/40"
        >
            <div className="flex items-center justify-between border-b border-[#29362e] bg-[#151d18] px-4 py-3">
                <div className="flex items-center gap-2">
                            <span
                                className="material-symbols-outlined text-[18px] text-[#3ecf8e]"
                                aria-hidden="true"
                            >
                                cloud_upload
                            </span>

                    <span className="text-sm font-medium text-[#e8f0eb]">
                                Uploads
                            </span>

                    <span className="rounded-full border border-[#2b4938] bg-[#1c2c22] px-2 py-0.5 text-[11px] text-[#9bbba7]">
                                {uploadJobs.filter((job) => job.status === "uploading",).length}{" "}
                        active
                            </span>
                </div>
            </div>

            <div className="upload-tray-scrollbar max-h-72 space-y-1 overflow-y-auto p-2">
                {uploadJobs.slice().reverse().map((job) => {
                    const isComplete = job.status === "completed";
                    const isError = job.status === "error";

                    const percent = isComplete ? 100 : Math.max(0, Math.min(99, Number(job.progress) || 0,),);

                    return (<div
                        key={job.jobId}
                        className="rounded-lg border border-transparent px-2 py-2 transition-colors duration-150 hover:border-[#293b30] hover:bg-[#19231c]"
                    >
                        <div className="flex items-start gap-2">
                                        <span
                                            className={`material-symbols-outlined mt-0.5 text-[18px] ${isError ? "text-[#ff7278]" : isComplete ? "text-[#3ecf8e]" : "text-[#779184]"}`}
                                            aria-hidden="true"
                                        >
                                            {isError ? "error" : isComplete ? "check_circle" : "upload_file"}
                                        </span>

                            <div className="min-w-0 flex-1">
                                <p
                                    className="truncate text-xs font-medium text-[#e8f0eb]"
                                    title={job.name}
                                >
                                    {job.name || "Upload"}
                                </p>

                                <p
                                    className={`mt-1 text-[11px] ${isError ? "text-[#ff7278]" : isComplete ? "text-[#3ecf8e]" : "text-[#8ca296]"}`}
                                >
                                    {isError ? job.message || "Upload failed" : isComplete ? "Completed" : job.message || "Uploading"}
                                </p>

                                {!isError && (<div className="mt-2 h-1 overflow-hidden rounded-full bg-[#29372e]">
                                    <div
                                        className={`h-full rounded-full transition-[width] duration-300 ${isComplete ? "bg-[#3ecf8e]" : "bg-gradient-to-r from-[#279b68] to-[#3ecf8e]"}`}
                                        style={{
                                            width: `${percent}%`,
                                        }}
                                    />
                                </div>)}

                                {!isError && (<p className="mt-1 text-right font-mono text-[10px] text-[#718477]">
                                    {job.total > 0 ? `${formatFileSize(job.transfer || 0,)} / ${formatFileSize(job.total,)} · ` : ""}
                                    {percent.toFixed(1)}%
                                </p>)}
                            </div>

                            {job.status === "uploading" && job.kind === "file" ? (<button
                                type="button"
                                onClick={() => cancelUploadJob(job.jobId)}
                                className={`${iconBtnDanger} shrink-0`}
                                title="Cancel upload"
                                aria-label={`Cancel upload ${job.name || "file"}`}
                            >
                                                <span
                                                    className="material-symbols-outlined text-[17px]"
                                                    aria-hidden="true"
                                                >
                                                    close
                                                </span>
                            </button>) : job.status !== "uploading" ? (<button
                                type="button"
                                onClick={() => dismissUploadJob(job.jobId)}
                                className={`${iconBtn} shrink-0`}
                                title="Remove upload"
                                aria-label={`Remove upload ${job.name || "file"}`}
                            >
                                                <span
                                                    className="material-symbols-outlined text-[16px]"
                                                    aria-hidden="true"
                                                >
                                                    close
                                                </span>
                            </button>) : null}
                        </div>
                    </div>);
                })}
            </div>
        </aside>)}
    </>);
}
