import {useEffect} from "react";
import {createPortal} from "react-dom";

export default function DashboardContextMenu({menu, onClose, onAction, multipleSelected = false, canDownloadZip = false}) {
    useEffect(() => {
        const closeOnOutsideClick = () => onClose();
        const closeOnScroll = () => onClose();
        window.addEventListener("click", closeOnOutsideClick);
        window.addEventListener("scroll", closeOnScroll, true);
        return () => {
            window.removeEventListener("click", closeOnOutsideClick);
            window.removeEventListener("scroll", closeOnScroll, true);
        };
    }, [onClose]);

    let items;
    if (menu.kind === "background") {
        items = [{id: "create-folder", label: "Create folder", icon: "create_new_folder"}, {
            id: "upload", label: "Upload files", icon: "cloud_upload"
        },];
    } else if (menu.item?.is_deleted) {
        items = [{id: "restore", label: "Restore", icon: "restore"}, ...(!multipleSelected ? [{
            id: "details", label: "Details", icon: "info"
        }] : []), {id: "delete", label: "Delete permanently", icon: "delete_forever", danger: true},];
    } else if (menu.item?.type === "folder") {
        items = [{id: "move", label: "Move to folder", icon: "drive_file_move"}, ...(!multipleSelected ? [{
            id: "rename", label: "Rename", icon: "drive_file_rename_outline"
        }, {id: "details", label: "Details", icon: "info"},] : []), ...(canDownloadZip ? [{
            id: "download-zip", label: "Download as ZIP", icon: "folder_zip"
        }] : []), {id: "delete", label: "Move to Trash", icon: "delete", danger: true},];
    } else {
        items = [...(!multipleSelected ? [{id: "share", label: "Share", icon: "share"}] : []), {id: "move", label: "Move to folder", icon: "drive_file_move"}, ...(!multipleSelected ? [{
            id: "rename", label: "Rename", icon: "drive_file_rename_outline"
        }, {id: "details", label: "Details", icon: "info"}, {
            id: "download", label: "Download", icon: "download"
        }] : []), ...(canDownloadZip ? [{id: "download-zip", label: "Download as ZIP", icon: "folder_zip"}] : []), {
            id: "delete", label: "Move to Trash", icon: "delete", danger: true
        },];
    }

    return createPortal(<div
        role="menu"
        data-selection-ignore
        className="fixed z-[220] w-48 overflow-hidden rounded-lg border border-[#363636] bg-[#1f1f1f] py-1 shadow-2xl"
        style={{top: menu.y, left: menu.x}}
        onClick={(event) => event.stopPropagation()}
        onContextMenu={(event) => event.preventDefault()}
    >
        {items.map((item) => <button
            key={item.id}
            type="button"
            role="menuitem"
            onClick={() => onAction(item.id)}
            className={`flex h-9 w-full items-center gap-2.5 px-3 text-left text-sm transition-colors focus:bg-[#2a2a2a] focus:outline-none ${item.danger ? "text-[#ff8589] hover:bg-[#e5484d]/10" : "text-[#fafafa] hover:bg-[#2a2a2a]"}`}
        >
            <span className="material-symbols-outlined text-[18px] text-[#a0a0a0]" aria-hidden="true">{item.icon}</span>
            {item.label}
        </button>)}
    </div>, document.body);
}

/* -------------------------------------------------------------------- */
/* Dashboard Page                                                      */

/* -------------------------------------------------------------------- */

