import {useEffect, useState} from "react";
import {btnPrimary, btnSecondary, iconBtn, inputClass} from "../constants.js";

export default function CreateFolderModal({targetPath, onClose, onCreate}) {
    const [name, setName] = useState("");
    const [error, setError] = useState("");
    const [creating, setCreating] = useState(false);

    useEffect(() => {
        /**
         * Close the create-folder dialog when Escape is pressed.
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        const onKeyDown = (e) => {
            if (e.key === "Escape" && !creating) onClose();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [creating, onClose]);

    /**
     * Validate and submit a new folder name.
     * @param {import("react").FormEvent<HTMLFormElement>} event
     * @returns {Promise<void>}
     */
    const handleSubmit = async (event) => {
        event.preventDefault();
        const cleanName = name.trim();

        if (!cleanName) {
            setError("Folder name cannot be empty.");
            return;
        }
        if (cleanName === "." || cleanName === ".." || cleanName.includes("/") || cleanName.includes("\\")) {
            setError("Folder name contains invalid characters.");
            return;
        }

        setCreating(true);
        setError("");
        try {
            await onCreate(cleanName);
        } catch (err) {
            setError(err.message || "Failed to create folder.");
        } finally {
            setCreating(false);
        }
    };

    return (<div
        className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm"
        onClick={() => !creating && onClose()}
    >
        <div
            className="w-full max-w-md rounded-lg border border-[#2e2e2e] bg-[#1c1c1c] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
        >
            <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                    <h2 className="text-base font-medium text-[#fafafa]">Create New Folder</h2>
                    <p className="mt-1 text-sm text-[#898989]">
                        Will be created inside <span
                        className="text-[#fafafa]">{targetPath ? targetPath.split("/").filter(Boolean).join(" / ") : "home"}</span>.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    disabled={creating}
                    aria-label="Close"
                    className={iconBtn}
                >
                    <span className="material-symbols-outlined text-[18px]" aria-hidden="true">close</span>
                </button>
            </div>

            <form onSubmit={handleSubmit}>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => {
                        setName(e.target.value);
                        setError("");
                    }}
                    placeholder="Folder name"
                    autoFocus
                    disabled={creating}
                    className={inputClass}
                />
                {error && <p className="mt-2 text-sm text-[#ff6369]">{error}</p>}
                <div className="mt-6 flex justify-end gap-2">
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={creating}
                        className={btnSecondary}
                    >
                        Cancel
                    </button>
                    <button
                        type="submit"
                        disabled={creating}
                        className={btnPrimary}
                    >
                        {creating ? "Creating…" : "Create Folder"}
                    </button>
                </div>
            </form>
        </div>
    </div>);
}

