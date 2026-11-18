import {useRef, useState} from "react";
import {btnPrimary, btnSecondary, inputClass} from "../constants.js";

export default function RenameItemModal({item, onClose, onRename}) {
    const [name, setName] = useState(item.name || "");
    const [error, setError] = useState("");
    const [renaming, setRenaming] = useState(false);
    const inputRef = useRef(null);
    const cancelButtonRef = useRef(null);
    const renameButtonRef = useRef(null);

    /**
     * Handle keyboard navigation within the rename dialog.
     * @param {import("react").KeyboardEvent<HTMLElement>} event
     * @returns {void}
     */
    const handleDialogKeyDown = (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            if (!renaming) onClose();
            return;
        }

        const target = event.target;
        const isActionButton = target === cancelButtonRef.current || target === renameButtonRef.current;

        // From the name field, Down enters the action buttons. Up returns to
        // editing, while Left/Right switches between Cancel and Rename.
        if (event.key === "ArrowDown" && target === inputRef.current) {
            event.preventDefault();
            event.stopPropagation();
            cancelButtonRef.current?.focus();
            return;
        }

        if (isActionButton && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
            event.preventDefault();
            event.stopPropagation();
            (target === cancelButtonRef.current ? renameButtonRef.current : cancelButtonRef.current)?.focus();
            return;
        }

        if (isActionButton && event.key === "ArrowUp") {
            event.preventDefault();
            event.stopPropagation();
            const input = inputRef.current;
            input?.focus();
            if (input) {
                const end = input.value.length;
                input.setSelectionRange(end, end);
            }
            return;
        }

        // Keep Enter on an action button local to the dialog so the dashboard's
        // global shortcuts cannot also react to it. Native button activation remains intact.
        if (isActionButton && event.key === "Enter") event.stopPropagation();
    };

    /**
     * Validate the new name and submit the rename operation.
     * @param {import("react").FormEvent<HTMLFormElement>} event
     * @returns {Promise<void>}
     */
    const handleSubmit = async (event) => {
        event.preventDefault();
        const cleanName = name.trim();
        if (!cleanName) {
            setError("Name cannot be empty.");
            return;
        }
        if (cleanName === "." || cleanName === ".." || cleanName.includes("/") || cleanName.includes("\\")) {
            setError("Name cannot contain path separators.");
            return;
        }
        if (cleanName.length > 255) {
            setError("Name must be 255 characters or fewer.");
            return;
        }

        setRenaming(true);
        setError("");
        try {
            await onRename(item, cleanName);
        } catch (renameError) {
            setError(renameError instanceof Error ? renameError.message : "Failed to rename item.");
        } finally {
            setRenaming(false);
        }
    };

    return (<div
        className="fixed inset-0 z-[260] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm"
        onClick={() => !renaming && onClose()}
    >
        <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="rename-item-title"
            className="w-full max-w-md rounded-lg border border-[#2e2e2e] bg-[#1c1c1c] p-6 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={handleDialogKeyDown}
        >
            <div className="mb-5 flex items-start gap-3">
                <div
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[#3ecf8e]/20 bg-[#3ecf8e]/10 text-[#3ecf8e]">
                        <span className="material-symbols-outlined text-[21px]" aria-hidden="true">
                            drive_file_rename_outline
                        </span>
                </div>
                <div className="min-w-0 flex-1">
                    <h2 id="rename-item-title" className="text-base font-medium text-[#fafafa]">
                        Rename {item.type === "folder" ? "folder" : "file"}
                    </h2>
                    <p className="mt-1.5 text-sm leading-5 text-[#898989]">
                        Enter a new name for “{item.name}”.
                    </p>
                </div>
            </div>

            <form onSubmit={handleSubmit}>
                <input
                    ref={inputRef}
                    type="text"
                    value={name}
                    onChange={(event) => {
                        setName(event.target.value);
                        setError("");
                    }}
                    autoFocus
                    maxLength={255}
                    disabled={renaming}
                    className={inputClass}
                    aria-label="New name"
                    placeholder="Enter a name"
                />
                {error && <p className="mt-2 text-sm text-[#ff6369]">{error}</p>}
                <div className="mt-6 flex justify-end gap-2">
                    <button
                        ref={cancelButtonRef}
                        type="button"
                        onClick={onClose}
                        disabled={renaming}
                        className={`${btnSecondary} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3ecf8e]/70`}
                    >
                        Cancel
                    </button>
                    <button
                        ref={renameButtonRef}
                        type="submit"
                        disabled={renaming || !name.trim()}
                        className={`${btnPrimary} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3ecf8e]/70`}
                    >
                        {renaming ? "Renaming…" : "Rename"}
                    </button>
                </div>
            </form>
        </section>
    </div>);
}

