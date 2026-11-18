/** Display non-blocking file operations that are still being persisted by the API. */
export default function BackgroundOperationsTray({operations = [], onDismiss}) {
    if (!operations.length) return null;

    return (<aside
        aria-label="Background file operations"
        className="fixed bottom-20 left-4 z-[120] w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[#303b34] bg-[#111713] shadow-2xl shadow-black/40 sm:bottom-4"
    >
        <div className="flex items-center justify-between border-b border-[#29362e] bg-[#151d18] px-4 py-3">
            <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-[18px] text-[#3ecf8e]" aria-hidden="true">sync</span>
                <span className="text-sm font-medium text-[#e8f0eb]">Background activity</span>
            </div>
            <span className="rounded-full border border-[#2b4938] bg-[#1c2c22] px-2 py-0.5 text-[11px] text-[#9bbba7]">
                {operations.filter((operation) => operation.status === "pending").length} active
            </span>
        </div>
        <div className="max-h-64 space-y-1 overflow-y-auto p-2">
            {operations.slice().reverse().map((operation) => {
                const isError = operation.status === "error";
                const isComplete = operation.status === "completed";
                return (<div
                    key={operation.id}
                    className="flex items-start gap-2 rounded-lg px-2 py-2 hover:bg-[#19231c]"
                >
                    <span
                        className={`material-symbols-outlined mt-0.5 text-[17px] ${isError ? "text-[#ff7278]" : isComplete ? "text-[#3ecf8e]" : "animate-spin text-[#779184]"}`}
                        aria-hidden="true"
                    >
                        {isError ? "error" : isComplete ? "check_circle" : "progress_activity"}
                    </span>
                    <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium text-[#e8f0eb]">{operation.label}</p>
                        <p className={`mt-1 break-words text-[11px] ${isError ? "text-[#ff7278]" : isComplete ? "text-[#3ecf8e]" : "text-[#8ca296]"}`}>
                            {operation.message || (isError ? "Operation failed" : isComplete ? "Completed" : "Working in the background")}
                        </p>
                    </div>
                    {operation.status !== "pending" && (<button
                        type="button"
                        onClick={() => onDismiss(operation.id)}
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[#898989] hover:bg-[#2a2a2a] hover:text-white"
                        aria-label={`Dismiss ${operation.label}`}
                        title="Dismiss"
                    >
                        <span className="material-symbols-outlined text-[16px]" aria-hidden="true">close</span>
                    </button>)}
                </div>);
            })}
        </div>
    </aside>);
}
