import {btnSecondary} from "../constants.js";

/** Confirmation dialog for trashing or permanently deleting selected items. */
export default function BulkActionConfirmModal({
                                                   confirmAction,
                                                   bulkActionError,
                                                   bulkActionLoading,
                                                   cancelConfirmButtonRef,
                                                   destructiveConfirmButtonRef,
                                                   setConfirmFocus,
                                                   cancelBulkConfirmation,
                                                   executeBulkAction,
                                               }) {
    return (<>
        {confirmAction && (<div
            className="fixed inset-0 z-[250] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm"
            onClick={cancelBulkConfirmation}
        >
            <section
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="bulk-confirm-title"
                aria-describedby="bulk-confirm-description"
                className="w-full max-w-md rounded-lg border border-[#2e2e2e] bg-[#1c1c1c] p-6 shadow-2xl"
                onClick={(event) => event.stopPropagation()}
            >
                <div className="mb-5 flex items-start gap-3">
                    <div
                        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[#e5484d]/25 bg-[#e5484d]/10 text-[#ff6369]">
                <span className="material-symbols-outlined text-[21px]" aria-hidden="true">
                    {confirmAction.mode === "permanent" ? "delete_forever" : "delete"}
                </span>
                    </div>
                    <div className="min-w-0 flex-1">
                        <h2 id="bulk-confirm-title" className="text-base font-medium text-[#fafafa]">
                            {confirmAction.mode === "permanent" ? "Delete permanently?" : "Move to Trash?"}
                        </h2>
                        <p id="bulk-confirm-description" className="mt-1.5 text-sm leading-5 text-[#898989]">
                            {confirmAction.mode === "permanent" ? `Permanently delete ${confirmAction.items.length} selected item${confirmAction.items.length === 1 ? "" : "s"}? This action cannot be undone.` : `Move ${confirmAction.items.length} selected item${confirmAction.items.length === 1 ? "" : "s"} to Trash? You can restore them later.`}
                        </p>
                        {confirmAction.items.length === 1 && <p className="mt-2 truncate text-xs text-[#b4b4b4]"
                                                                title={confirmAction.items[0].name}>{confirmAction.items[0].name}</p>}
                        {bulkActionError && <p className="mt-3 text-sm text-[#ff6369]">{bulkActionError}</p>}
                    </div>
                </div>
                <div className="flex justify-end gap-2">
                    <button
                        ref={cancelConfirmButtonRef}
                        type="button"
                        onClick={cancelBulkConfirmation}
                        onFocus={() => setConfirmFocus("cancel")}
                        disabled={bulkActionLoading}
                        className={`${btnSecondary} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3ecf8e]/70`}
                    >Cancel
                    </button>
                    <button
                        ref={destructiveConfirmButtonRef}
                        type="button"
                        onClick={executeBulkAction}
                        onFocus={() => setConfirmFocus("confirm")}
                        disabled={bulkActionLoading}
                        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[#e5484d]/40 bg-[#e5484d]/10 px-3 text-xs text-[#ff8589] transition-colors hover:bg-[#e5484d]/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#e5484d]/70 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {bulkActionLoading &&
                            <span className="material-symbols-outlined animate-spin text-[15px]" aria-hidden="true">progress_activity</span>}
                        {bulkActionLoading ? "Processing…" : confirmAction.mode === "permanent" ? "Delete permanently" : "Move to Trash"}
                    </button>
                </div>
            </section>
        </div>)}
    </>);
}
