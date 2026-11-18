import {useEffect} from "react";
import {formatFileSize, getFileIcon} from "../utils.js";

function displayPath(path) {
    if (!path || path === "/home" || path === "home") return "Home";
    const parts = String(path).split("/").filter(Boolean);
    if (parts[0] === "home") parts.shift();
    return ["Home", ...parts].join(" / ");
}

function formatTimestamp(value) {
    if (value === undefined || value === null || value === "") return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return date.toLocaleString();
}

function detailValue(value) {
    return value === undefined || value === null || value === "" ? null : String(value);
}

export default function ItemDetailsModal({item, onClose}) {
    const object = item?.object || {};
    const isFolder = item?.type === "folder";
    const iconInfo = isFolder ? {icon: "folder", color: "text-[#a3a3a3]"} : getFileIcon(item?.name);
    const fullFolderPath = item?.path || object.path;
    const locationPath = isFolder ? (fullFolderPath ? fullFolderPath.split("/").slice(0, -1).join("/") : "") : (item?.directory || object.directory || "");
    const size = object.size ?? object.file_size;
    const extension = item?.name?.includes(".") ? item.name.split(".").pop().toUpperCase() : "";
    const type = isFolder ? "Folder" : (detailValue(object.type) || extension || "File");
    const modified = formatTimestamp(object.modified_at ?? object.updated_at ?? object.updatedAt);
    const created = formatTimestamp(object.created_at ?? object.createdAt);

    const rows = [{label: "Type", value: type}, ...(isFolder ? [{
        label: "Total size",
        value: formatFileSize(Number(object.total_size_bytes ?? 0))
    }] : Number.isFinite(Number(size)) && size !== undefined && size !== null ? [{
        label: "Size",
        value: formatFileSize(Number(size))
    }] : []), {label: "Location", value: displayPath(locationPath)}, ...(isFolder && fullFolderPath ? [{
        label: "Folder path",
        value: displayPath(fullFolderPath)
    }] : []), ...(!isFolder && detailValue(object.data_center) ? [{
        label: "Data center",
        value: detailValue(object.data_center)
    }] : []), ...(created ? [{label: "Created", value: created}] : []), ...(modified ? [{
        label: "Last modified",
        value: modified
    }] : []), {
        label: "Status",
        value: item?.is_deleted || object.is_deleted ? "In Trash" : "Available"
    }, ...(detailValue(item?.id ?? object.id) ? [{label: "ID", value: detailValue(item?.id ?? object.id)}] : []),];

    useEffect(() => {
        const handleKeyDown = (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                onClose();
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [onClose]);

    return (<div
        className="fixed inset-0 z-[260] flex items-center justify-center bg-black/60 px-4 py-6 backdrop-blur-sm"
        onClick={onClose}
    >
        <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="item-details-title"
            className="w-full max-w-md overflow-hidden rounded-xl border border-[#363636] bg-[#1c1c1c] shadow-2xl"
            onClick={(event) => event.stopPropagation()}
        >
            <header className="flex items-start gap-3 border-b border-[#303030] px-5 py-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[#3ecf8e]/20 bg-[#3ecf8e]/10">
                        <span className={`material-symbols-outlined text-[22px] ${iconInfo.color}`} aria-hidden="true">
                            {iconInfo.icon}
                        </span>
                </div>
                <div className="min-w-0 flex-1">
                    <h2 id="item-details-title" className="text-base font-medium text-[#fafafa]">Details</h2>
                    <p className="mt-1 break-words text-sm text-[#a3a3a3]">{item?.name || "Untitled"}</p>
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[#898989] transition-colors hover:bg-white/[0.06] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                    aria-label="Close details"
                >
                    <span className="material-symbols-outlined text-[19px]" aria-hidden="true">close</span>
                </button>
            </header>

            <dl className="divide-y divide-[#2d2d2d] px-5">
                {rows.map(({label, value}) => (<div key={label} className="grid grid-cols-[112px_minmax(0,1fr)] gap-3 py-3 text-sm">
                    <dt className="text-[#898989]">{label}</dt>
                    <dd className="min-w-0 break-words text-right text-[#e5e5e5]">{value}</dd>
                </div>))}
            </dl>

            <footer className="flex justify-end border-t border-[#303030] px-5 py-4">
                <button
                    type="button"
                    onClick={onClose}
                    className="rounded-md border border-[#3a3a3a] px-4 py-2 text-sm text-[#e5e5e5] transition-colors hover:bg-white/[0.05] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
                >
                    Close
                </button>
            </footer>
        </section>
    </div>);
}
