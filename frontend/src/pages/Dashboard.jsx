import {useEffect, useMemo, useRef, useState} from "react";
import {useLocation, useNavigate} from "react-router-dom";
import MoveModal from "./dashboard/components/MoveModal.jsx";
import UploadPage from "./dashboard/components/UploadModal.jsx";
import Sidebar from "./dashboard/components/Sidebar.jsx";
import CreateFolderModal from "./dashboard/components/CreateFolderModal.jsx";
import RenameItemModal from "./dashboard/components/RenameItemModal.jsx";
import ItemDetailsModal from "./dashboard/components/ItemDetailsModal.jsx";
import DashboardContextMenu from "./dashboard/components/DashboardContextMenu.jsx";
import DashboardWorkspace from "./dashboard/components/DashboardWorkspace.jsx";
import UploadJobsTray from "./dashboard/components/UploadJobsTray.jsx";
import BulkActionConfirmModal from "./dashboard/components/BulkActionConfirmModal.jsx";
import useDashboardData, {clearDashboardTreeCache} from "./dashboard/hooks/useDashboardData.js";
import useGlobalFileSearch from "./dashboard/hooks/useGlobalFileSearch.js";
import useUploadJobs from "./dashboard/hooks/useUploadJobs.js";
import {API_URL, DASHBOARD_SELECTION_STORAGE_KEY, DASHBOARD_SELECTIONS_BY_VIEW_KEY,} from "./dashboard/constants.js";
import {decodePathSegment, folderPathToRoute, getFileIcon, getFileKind, getToken, makeSelectionViewKey,} from "./dashboard/utils.js";

/** @typedef {"files" | "trash"} DashboardTab */
/** @typedef {{icon: string, color: string}} FileIconStyle */
/**
 * @typedef {Object} FileEntry
 * @property {string | number} id
 * @property {string} name
 * @property {string} [type]
 * @property {number} [size]
 * @property {string | number} [directory_id]
 * @property {string} [directory]
 * @property {boolean} [is_deleted]
 */
/**
 * @typedef {Object} FolderEntry
 * @property {string | number} id
 * @property {string} path
 * @property {boolean} [is_deleted]
 */
/**
 * @typedef {Object} SelectableItem
 * @property {string} key
 * @property {"file" | "folder"} type
 * @property {string | number} id
 * @property {string} name
 * @property {string} [path]
 * @property {string} [directory]
 * @property {boolean} [is_deleted]
 * @property {FileEntry | FolderEntry} [object]
 */
/**
 * @typedef {Object} UploadJob
 * @property {string} jobId
 * @property {string} [name]
 * @property {string} [status]
 * @property {number} [progress]
 * @property {number} [transfer]
 * @property {number} [total]
 * @property {string} [message]
 */

/** @typedef {{kind: "background"} | {kind: "item", item: SelectableItem}} ContextMenuPayload */
/** @typedef {ContextMenuPayload & {x: number, y: number}} ContextMenuState */

function isPathWithinFolder(candidatePath, folderPath) {
    const candidate = String(candidatePath || "").replace(/\/$/, "");
    const folder = String(folderPath || "").replace(/\/$/, "");
    return Boolean(folder) && (candidate === folder || candidate.startsWith(`${folder}/`));
}

function replacePathPrefix(pathValue, oldPath, newPath) {
    const path = String(pathValue || "");
    if (path === oldPath) return newPath;
    if (path.startsWith(`${oldPath}/`)) return `${newPath}${path.slice(oldPath.length)}`;
    return path;
}

/**
 * Render file browsing, search, selection, and file-management interactions.
 * @returns {import("react").ReactElement}
 */
export default function DashboardPage() {
    const navigate = useNavigate();
    const location = useLocation();

    /** @type {[DashboardTab, import("react").Dispatch<import("react").SetStateAction<DashboardTab>>]} */
    const [activeTab, setActiveTab] = useState("files");
    // The URL is the source of truth for the current folder. This keeps links
    // shareable and makes browser back/forward restore the previous folder.
    const currentFolder = useMemo(() => {
        const segments = location.pathname.split("/").filter(Boolean);
        if (segments[0] !== "home") return "";
        const folderSegments = segments.slice(1).map(decodePathSegment);
        return folderSegments.length ? `/home/${folderSegments.join("/")}` : "";
    }, [location.pathname]);

    const [sidebarOpen, setSidebarOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");

    const {
        files, setFiles, folders, setFolders, storageUsed, loadingFiles,
        loadedFolder, pageError, setPageError, fetchFiles, refreshDirectory,
    } = useDashboardData(currentFolder, navigate);
    const {searchResults, searchLoading, searchError} = useGlobalFileSearch(searchQuery, activeTab, files);
    const {uploadJobs, dismissUploadJob, cancelUploadJob} = useUploadJobs(refreshDirectory);
    const [showFolderModal, setShowFolderModal] = useState(false);
    const [showUploadModal, setShowUploadModal] = useState(false);
    /** @type {[SelectableItem[] | null, import("react").Dispatch<import("react").SetStateAction<SelectableItem[] | null>>]} */
    const [moveDialogItems, setMoveDialogItems] = useState(null);
    const [deletingId, setDeletingId] = useState(null);
    const [openMenuFileId, setOpenMenuFileId] = useState(null);
    const [clipboard, setClipboard] = useState(null);
    const [menuPosition, setMenuPosition] = useState(null);
    /** @type {[ContextMenuState | null, import("react").Dispatch<import("react").SetStateAction<ContextMenuState | null>>]} */
    const [contextMenu, setContextMenu] = useState(null);
    /** @type {[Set<string>, import("react").Dispatch<import("react").SetStateAction<Set<string>>>]} */
    const [selectedItems, setSelectedItems] = useState(() => new Set());
    const [marquee, setMarquee] = useState(null);
    const [dropTargetKey, setDropTargetKey] = useState(null);
    const [draggingKeys, setDraggingKeys] = useState(() => new Set());
    const [movingItems, setMovingItems] = useState(false);
    const moveDragRef = useRef(null);
    const movePointerRef = useRef(null);
    const moveDndCallbacksRef = useRef(null);
    const dragPreviewNodeRef = useRef(null);
    const [confirmAction, setConfirmAction] = useState(null);
    const [confirmFocus, setConfirmFocus] = useState("cancel");
    const [bulkActionLoading, setBulkActionLoading] = useState(false);
    const [bulkActionError, setBulkActionError] = useState("");
    const [renameTarget, setRenameTarget] = useState(null);
    const [detailsTarget, setDetailsTarget] = useState(null);
    const renameSelectionSnapshotRef = useRef(null);
    const cancelConfirmButtonRef = useRef(null);
    const destructiveConfirmButtonRef = useRef(null);
    const selectionSurfaceRef = useRef(null);
    const dragStateRef = useRef(null);
    const suppressClickRef = useRef(false);
    const selectionAnchorRef = useRef(null);
    const selectionRestoreRef = useRef({checked: false, snapshot: null});
    const currentSelectionViewKey = makeSelectionViewKey(location.pathname, activeTab, searchQuery);
    const selectionSnapshotsByViewRef = useRef(null);
    if (selectionSnapshotsByViewRef.current === null) {
        try {
            const stored = JSON.parse(sessionStorage.getItem(DASHBOARD_SELECTIONS_BY_VIEW_KEY) || "{}");
            selectionSnapshotsByViewRef.current = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
        } catch {
            selectionSnapshotsByViewRef.current = {};
        }
    }
    const lastSelectionSnapshotViewKeyRef = useRef(currentSelectionViewKey);
    const selectionSnapshotInitialRenderRef = useRef(true);
    const skipSelectionSnapshotPersistRef = useRef(false);
    /**
     * Persist the current selection so it can be restored after navigation.
     * @param {string[]} [keys]
     * @param {string | null} [anchor]
     * @returns {void}
     */
    const saveCurrentSelectionSnapshot = (keys = Array.from(selectedItems), anchor = selectionAnchorRef.current) => {
        const snapshots = selectionSnapshotsByViewRef.current || (selectionSnapshotsByViewRef.current = {});
        snapshots[currentSelectionViewKey] = {
            pathname: location.pathname, activeTab, searchQuery, keys: Array.from(keys), anchor, savedAt: Date.now(),
        };

        // Keep session storage bounded when users browse many directories.
        const entries = Object.entries(snapshots).sort((a, b) => (b[1]?.savedAt || 0) - (a[1]?.savedAt || 0));
        for (const [key] of entries.slice(80)) delete snapshots[key];
        try {
            sessionStorage.setItem(DASHBOARD_SELECTIONS_BY_VIEW_KEY, JSON.stringify(snapshots));
        } catch (error) {
            console.warn("Could not preserve directory selection:", error);
        }
    };
    if (!selectionRestoreRef.current.checked) {
        selectionRestoreRef.current.checked = true;
        try {
            const rawSnapshot = sessionStorage.getItem(DASHBOARD_SELECTION_STORAGE_KEY);
            if (rawSnapshot) {
                const snapshot = JSON.parse(rawSnapshot);
                if (snapshot?.pathname === location.pathname) {
                    selectionRestoreRef.current.snapshot = snapshot;
                } else {
                    sessionStorage.removeItem(DASHBOARD_SELECTION_STORAGE_KEY);
                }
            }
        } catch {
            sessionStorage.removeItem(DASHBOARD_SELECTION_STORAGE_KEY);
        }
    }

    /**
     * Save selection state and open the selected file in its preview route.
     * @param {FileEntry} file
     * @returns {void}
     */
    const handleFileClick = (file) => {
        saveCurrentSelectionSnapshot();
        const kind = getFileKind(file.name);
        const params = new URLSearchParams({name: file.name});
        if (kind) params.set("kind", kind);

        // Save the current selection before the dashboard unmounts for the viewer.
        // Backspace, Escape, and browser Back then restore the same selection.
        try {
            sessionStorage.setItem(DASHBOARD_SELECTION_STORAGE_KEY, JSON.stringify({
                pathname: location.pathname, activeTab, searchQuery, keys: Array.from(selectedItems), anchor: selectionAnchorRef.current,
            }));
        } catch (error) {
            console.warn("Could not preserve dashboard selection:", error);
        }

        navigate(`/view/${file.id}?${params.toString()}`);
    };


    useEffect(() => {
        if (!sidebarOpen) return;
        /**
         * Handle dashboard-level keyboard shortcuts.
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        const onKeyDown = (event) => {
            if (event.key === "Escape") setSidebarOpen(false);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [sidebarOpen]);

    /* ----------------------------- download ------------------------------ */

    /**
     * Create a signed public link and trigger a browser download.
     * @param {string | number} fileId
     * @returns {Promise<void>}
     */
    const downloadFile = async (fileId) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        try {
            const response = await fetch(`${API_URL}/auth/file/${fileId}/public-link`, {
                method: "POST", headers: {Authorization: `Bearer ${token}`},
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (response.status === 401) {
                    localStorage.removeItem("token");
                    navigate("/");
                    return;
                }
                throw new Error(data.detail || "Failed to prepare the download.");
            }
            if (!data.public_token) throw new Error("The server did not return a download token.");
            const downloadUrl = `${API_URL}/public/download/${data.public_token}`;

            const anchor = document.createElement("a");
            anchor.href = downloadUrl;
            anchor.setAttribute("download", "");
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
        } catch (error) {
            console.error("Download failed:", error);
            alert(error.message || "Failed to download file.");
        }
    };

    /* ------------------------------ delete/restore ----------------------- */

    /**
     * Confirm and delete or trash a file.
     * @param {string | number} fileId
     * @param {string} filename
     * @param {boolean} permanent
     * @returns {Promise<void>}
     */
    const deleteFile = async (fileId, filename, permanent = false) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        const confirmMsg = permanent ? `Permanently delete "${filename}"? This action cannot be undone.` : `Move "${filename}" to Trash?`;

        if (!window.confirm(confirmMsg)) return;

        setDeletingId(fileId);

        try {
            const endpoint = permanent ? `${API_URL}/auth/trash/file/${fileId}` : `${API_URL}/auth/file/${fileId}`;

            const response = await fetch(endpoint, {
                method: "DELETE", headers: {Authorization: `Bearer ${token}`},
            });

            if (!response.ok) {
                let message = "Failed to delete file.";
                try {
                    const data = await response.json();
                    message = data.detail || message;
                } catch {
                }
                throw new Error(message);
            }

            if (permanent) {
                setFiles((current) => current.filter((file) => String(file.id) !== String(fileId)));
            } else {
                setFiles((current) => current.map((file) => String(file.id) === String(fileId)
                    ? {...file, is_deleted: true, modified_at: new Date().toISOString()}
                    : file));
            }
        } catch (error) {
            console.error("Delete failed:", error);
            alert(error.message || "Failed to delete file.");
        } finally {
            setDeletingId(null);
        }
    };

    /**
     * Restore a trashed file.
     * @param {string | number} itemId
     * @returns {Promise<void>}
     */
    const restoreItem = async (itemId) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        try {
            const response = await fetch(`${API_URL}/auth/trash/file/${itemId}/restore`, {
                method: "POST", headers: {Authorization: `Bearer ${token}`},
            });

            if (!response.ok) {
                let message = "Failed to restore item.";
                try {
                    const data = await response.json();
                    message = data.detail || message;
                } catch {
                }
                throw new Error(message);
            }

            setFiles((current) => current.map((file) => String(file.id) === String(itemId)
                ? {...file, is_deleted: false, deleted_at: null, modified_at: new Date().toISOString()}
                : file));
        } catch (error) {
            console.error("Restore failed:", error);
            alert(error.message || "Failed to restore item.");
        }
    };


    /**
     * Restore a trashed folder.
     * @param {string | number} folderId
     * @returns {Promise<void>}
     */
    const restoreFolder = async (folderId) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        try {
            const response = await fetch(`${API_URL}/auth/trash/directory/${folderId}/restore`, {
                method: "POST", headers: {Authorization: `Bearer ${token}`},
            });

            if (!response.ok) {
                let message = "Failed to restore folder.";
                try {
                    const data = await response.json();
                    message = data.detail || message;
                } catch {
                }
                throw new Error(message);
            }

            setFolders((current) => current.map((folder) => String(folder.id) === String(folderId)
                ? {...folder, is_deleted: false, deleted_at: null, modified_at: new Date().toISOString()}
                : folder));
        } catch (error) {
            console.error("Folder restore failed:", error);
            alert(error.message || "Failed to restore folder.");
        }
    };


    /* ------------------------------ folders -------------------------------- */

    /**
     * Create a folder under the selected directory.
     * @param {string} cleanName
     * @returns {Promise<void>}
     */
    const createFolder = async (cleanName) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        const directory = currentFolder || "/home";
        const response = await fetch(`${API_URL}/auth/create-folder?directory=${encodeURIComponent(directory)}&name=${encodeURIComponent(cleanName)}`, {
            method: "POST", headers: {Authorization: `Bearer ${token}`},
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Failed to create folder.");

        await refreshDirectory(directory);
        setShowFolderModal(false);
    };


    /**
     * Confirm and delete or permanently remove a folder.
     * @param {string | number} folderId
     * @param {string} folderPath
     * @param {boolean} permanent
     * @returns {Promise<void>}
     */
    const deleteFolder = async (folderId, folderPath, permanent = false) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        const folderName = folderPath.split("/").pop();
        const confirmMsg = permanent ? `Permanently delete folder "${folderName}"?` : `Move folder "${folderName}" to Trash?`;

        if (!window.confirm(confirmMsg)) return;

        setDeletingId(`folder-${folderId}`);

        try {
            const endpoint = permanent ? `${API_URL}/auth/trash/directory/${folderId}` : `${API_URL}/auth/directory/${folderId}`;

            const response = await fetch(endpoint, {
                method: "DELETE", headers: {Authorization: `Bearer ${token}`},
            });

            if (!response.ok) {
                let data = {};
                try {
                    data = await response.json();
                } catch {
                }
                throw new Error(data.detail || "Failed to delete folder.");
            }

            if (permanent) {
                setFolders((current) => current.filter((folder) => !isPathWithinFolder(folder.path, folderPath)));
                setFiles((current) => current.filter((file) => !isPathWithinFolder(file.directory, folderPath)));
            } else {
                setFolders((current) => current.map((folder) => String(folder.id) === String(folderId)
                    ? {...folder, is_deleted: true, modified_at: new Date().toISOString()}
                    : folder));
            }
        } catch (error) {
            console.error("Folder deletion failed:", error);
            alert(error.message || "Failed to delete folder.");
        } finally {
            setDeletingId(null);
        }
    };


    /**
     * Submit a bulk delete request for the selected items.
     * @param {string} endpoint
     * @returns {Promise<void>}
     */
    const requestSelectionDelete = async (endpoint) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            throw new Error("Your session has expired. Please sign in again.");
        }

        const response = await fetch(`${API_URL}${endpoint}`, {
            method: "DELETE", headers: {Authorization: `Bearer ${token}`},
        });

        if (!response.ok) {
            let message = `Request failed (${response.status})`;
            try {
                const data = await response.json();
                message = data.detail || data.message || message;
            } catch {
                // Keep the HTTP status message if the response is not JSON.
            }
            throw new Error(message);
        }
    };

    /**
     * Execute the currently confirmed bulk action.
     * @returns {Promise<void>}
     */
    const executeBulkAction = async () => {
        if (!confirmAction || bulkActionLoading) return;

        setBulkActionLoading(true);
        setBulkActionError("");
        setPageError("");

        const items = confirmAction.items;
        // If a folder and any of its descendants are both selected, handle only
        // the folder. Its backend operation already includes its descendants.
        const topLevelItems = items.filter((item) => !items.some((parent) => {
            if (parent.type !== "folder" || parent.key === item.key) return false;
            if (item.type === "folder") return item.path.startsWith(`${parent.path.replace(/\/$/, "")}/`);
            const directory = (item.directory || "").replace(/\/$/, "");
            const parentPath = parent.path.replace(/\/$/, "");
            return directory === parentPath || directory.startsWith(`${parentPath}/`);
        }));

        let succeeded = 0;
        const failures = [];
        const cacheMutations = [];
        const inTrash = activeTab === "trash";

        try {
            for (const item of topLevelItems) {
                const kind = item.type === "folder" ? "directory" : "file";
                const ordinaryEndpoint = `/auth/${kind}/${item.id}`;
                const permanentEndpoint = `/auth/trash/${kind}/${item.id}`;

                try {
                    if (confirmAction.mode === "trash") {
                        await requestSelectionDelete(ordinaryEndpoint);
                        cacheMutations.push({item, mode: "trash"});
                    } else if (inTrash) {
                        await requestSelectionDelete(permanentEndpoint);
                        cacheMutations.push({item, mode: "permanent"});
                    } else {
                        // Shift+Delete first moves the item to Trash, then permanently deletes it.
                        await requestSelectionDelete(ordinaryEndpoint);
                        try {
                            await requestSelectionDelete(permanentEndpoint);
                            cacheMutations.push({item, mode: "permanent"});
                        } catch (error) {
                            // The first request succeeded, so reflect its Trash state even if
                            // the permanent-delete request failed.
                            cacheMutations.push({item, mode: "trash"});
                            throw error;
                        }
                    }
                    succeeded += 1;
                } catch (error) {
                    failures.push(`${item.name}: ${error.message || "Operation failed"}`);
                }
            }

            for (const {item, mode} of cacheMutations) {
                if (item.type === "file") {
                    setFiles((current) => mode === "permanent"
                        ? current.filter((file) => String(file.id) !== String(item.id))
                        : current.map((file) => String(file.id) === String(item.id)
                            ? {...file, is_deleted: true, modified_at: new Date().toISOString()}
                            : file));
                } else if (mode === "permanent") {
                    setFolders((current) => current.filter((folder) => !isPathWithinFolder(folder.path, item.path)));
                    setFiles((current) => current.filter((file) => !isPathWithinFolder(file.directory, item.path)));
                } else {
                    setFolders((current) => current.map((folder) => String(folder.id) === String(item.id)
                        ? {...folder, is_deleted: true, modified_at: new Date().toISOString()}
                        : folder));
                }
            }

            setConfirmAction(null);
            setSelectedItems(new Set());
            selectionAnchorRef.current = null;

            if (failures.length) {
                const firstFailures = failures.slice(0, 3).join("; ");
                const more = failures.length > 3 ? `; and ${failures.length - 3} more` : "";
                setPageError(`${succeeded} item(s) processed. ${failures.length} failed: ${firstFailures}${more}`);
            }
        } finally {
            setBulkActionLoading(false);
        }
    };

    /**
     * Clear the access token and navigate to the authentication page.
     * @returns {void}
     */
    const handleLogout = () => {
        clearDashboardTreeCache(getToken());
        localStorage.removeItem("token");
        navigate("/");
    };

    /**
     * Navigate to a folder route.
     * @param {string} folderPath
     * @returns {void}
     */
    const openFolder = (folderPath) => {
        // Save the current directory's selection before changing the route. When
        // browser Back/Backspace returns here, restore the same selected items.
        saveCurrentSelectionSnapshot();
        navigate(folderPathToRoute(folderPath));
        setSearchQuery("");
    };

    /**
     * Switch between dashboard tabs and reset the folder search.
     * @param {DashboardTab} tabId
     * @returns {void}
     */
    const handleTabChange = (tabId) => {
        setActiveTab(tabId);
        navigate("/home");
        setSearchQuery("");
    };


    /**
     * Clamp the context menu position to the visible viewport.
     * @param {number} x
     * @param {number} y
     * @param {ContextMenuPayload} payload
     * @returns {void}
     */
    const showContextMenuAt = (x, y, payload) => {
        const menuWidth = 192;
        const selectedCount = payload.kind === "item" && selectedItems.has(payload.item?.key)
            ? selectableItems.filter((item) => selectedItems.has(item.key)).length
            : 1;
        const multiSelection = selectedCount > 1;
        const menuHeight = payload.kind === "background" ? 80
            : payload.item?.is_deleted ? 80
                : multiSelection ? 80
                    : payload.item?.type === "folder" ? 120 : 160;
        setContextMenu({
            ...payload,
            x: Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8)),
            y: Math.max(8, Math.min(y, window.innerHeight - menuHeight - 8)),
        });
    };

    /**
     * Open an item context menu and ensure the clicked item is selected.
     * @param {import("react").MouseEvent<HTMLElement>} event
     * @param {SelectableItem} item
     * @returns {void}
     */
    const handleItemContextMenu = (event, item) => {
        event.preventDefault();
        event.stopPropagation();
        if (!selectedItems.has(item.key)) {
            setSelectedItems(new Set([item.key]));
            selectionAnchorRef.current = item.key;
        }
        showContextMenuAt(event.clientX, event.clientY, {kind: "item", item});
    };

    /**
     * Open an item context menu aligned to its action button.
     * @param {import("react").MouseEvent<HTMLElement>} event
     * @param {SelectableItem} item
     * @returns {void}
     */
    const handleItemMenuButton = (event, item) => {
        event.preventDefault();
        event.stopPropagation();
        const rect = event.currentTarget.getBoundingClientRect();
        showContextMenuAt(rect.right - 192, rect.bottom + 4, {kind: "item", item});
    };

    /**
     * Open the background context menu when empty space is right-clicked.
     * @param {import("react").MouseEvent<HTMLElement>} event
     * @returns {void}
     */
    const handleBackgroundContextMenu = (event) => {
        if (event.target.closest("[data-selection-id], [data-selection-ignore], button, input, textarea, select, a")) return;
        event.preventDefault();
        event.stopPropagation();
        showContextMenuAt(event.clientX, event.clientY, {kind: "background"});
    };

    /**
     * Dispatch the selected context-menu action.
     * @param {string} action
     * @returns {void}
     */
    const performContextMenuAction = (action) => {
        const menu = contextMenu;
        setContextMenu(null);
        if (!menu) return;

        if (action === "upload") {
            setShowUploadModal(true);
            return;
        }
        if (action === "create-folder") {
            setShowFolderModal(true);
            return;
        }

        const item = menu.item;
        if (!item) return;

        const actionItems = selectedItems.has(item.key)
            ? selectableItems.filter((candidate) => selectedItems.has(candidate.key))
            : [item];

        if (action === "move") {
            // If a parent folder and any of its descendants are selected together,
            // move only the top-level selection. Moving both would either duplicate
            // work or try to move a child out of a folder that is moving with it.
            const topLevelItems = reduceMoveItemsToTopLevel(actionItems);
            if (topLevelItems.length) setMoveDialogItems(topLevelItems);
            return;
        }
        if (action === "details") {
            setDetailsTarget(item);
            return;
        }
        if (action === "delete") {
            const mode = activeTab === "trash" || item.is_deleted ? "permanent" : "trash";
            const snapshot = selectedItems.has(item.key) ? Array.from(selectedItems) : [item.key];
            setSelectedItems(new Set(snapshot));
            selectionAnchorRef.current = item.key;
            setBulkActionError("");
            setConfirmFocus("cancel");
            setConfirmAction({
                mode,
                items: actionItems,
                selectionSnapshot: snapshot,
                selectionAnchor: item.key,
            });
            return;
        }
        if (action === "rename" && actionItems.length === 1) openRenameDialog(item);
        else if (action === "download" && item.type === "file") downloadFile(item.id);
        else if (action === "restore" && item.type === "file") restoreItem(item.id);
        else if (action === "restore" && item.type === "folder") restoreFolder(item.id);
    };

    /* ------------------------------ derived data ---------------------------- */

    const isTrash = activeTab === "trash";
    const visibleFolders = useMemo(() => {
        return folders.filter(({path, is_deleted}) => {
            if (isTrash) return Boolean(is_deleted);
            if (is_deleted) return false;
            if (!currentFolder) {
                if (!path.startsWith("/home/")) return false;
                const remainder = path.slice(6);
                return remainder && !remainder.includes("/");
            }
            if (!path.startsWith(`${currentFolder}/`)) return false;
            const remainder = path.slice(currentFolder.length + 1);
            return remainder && !remainder.includes("/");
        });
    }, [folders, currentFolder, isTrash]);

    const visibleFiles = useMemo(() => {
        return files.filter((file) => {
            if (file.name === ".__folder__") return false;
            if (isTrash) return Boolean(file.is_deleted);
            return !file.is_deleted && (file.directory || "") === currentFolder;
        });
    }, [files, currentFolder, isTrash]);

    const isSearching = Boolean(searchQuery.trim());

    const filteredFiles = useMemo(() => {
        if (isSearching) return searchResults;
        return visibleFiles;
    }, [visibleFiles, searchResults, isSearching]);

    const filteredFolders = useMemo(() => {
        if (isSearching) return [];
        return visibleFolders;
    }, [visibleFolders, isSearching]);

    const selectableItems = useMemo(() => [...filteredFolders.map((folder) => ({
        key: `folder:${folder.id}`,
        type: "folder",
        id: folder.id,
        name: folder.path.split("/").pop() || folder.path,
        path: folder.path,
        is_deleted: Boolean(folder.is_deleted),
        object: folder,
    })), ...filteredFiles
        .filter((file) => file && file.name !== ".__folder__")
        .map((file) => ({
            key: `file:${file.id}`,
            type: "file",
            id: file.id,
            name: file.name,
            directory: file.directory || "",
            is_deleted: Boolean(file.is_deleted),
            object: file,
        })),], [filteredFolders, filteredFiles]);

    /**
     * Normalize an empty directory path to the virtual home directory.
     * @param {string | null | undefined} path
     * @returns {string}
     */
    const normalizeDirectoryPath = (path) => path || "/home";

    /**
     * Remove selected descendants when an ancestor folder is also selected.
     * @param {SelectableItem[]} items
     * @returns {SelectableItem[]}
     */
    const reduceMoveItemsToTopLevel = (items) => items.filter((item) => !items.some((parent) => {
        if (parent.type !== "folder" || parent.key === item.key) return false;
        const parentPath = parent.path.replace(/\/$/, "");
        if (item.type === "folder") {
            return item.path === parentPath || item.path.startsWith(`${parentPath}/`);
        }
        const itemDirectory = normalizeDirectoryPath(item.directory || "").replace(/\/$/, "");
        return itemDirectory === parentPath || itemDirectory.startsWith(`${parentPath}/`);
    }));

    /**
     * Validate that selected items can be moved to the target directory.
     * @param {SelectableItem[]} items
     * @param {string} destinationPath
     * @returns {boolean}
     */
    const canDropItemsIntoDirectory = (items, destinationPath) => {
        if (isTrash || !items?.length || movingItems) return false;
        const destination = normalizeDirectoryPath(destinationPath);

        // Do not move a folder into itself or any descendant. Normalize each source
        // path because files use `directory`, while folders use their full `path`.
        if (items.some((item) => {
            if (item.type !== "folder" || !item.path) return false;
            const sourcePath = normalizeDirectoryPath(item.path).replace(/\/$/, "");
            return destination === sourcePath || destination.startsWith(`${sourcePath}/`);
        })) return false;

        // A move is useful only if at least one selected top-level item changes parent.
        return !items.every((item) => {
            if (item.type === "folder") {
                const sourcePath = normalizeDirectoryPath(item.path || "");
                const parentPath = sourcePath.slice(0, sourcePath.lastIndexOf("/")) || "/home";
                return normalizeDirectoryPath(parentPath) === destination;
            }
            return normalizeDirectoryPath(item.directory || "") === destination;
        });
    };

    /**
     * Create the floating DOM preview used while dragging files and folders.
     * @param {SelectableItem[]} items
     * @param {number} pointerX
     * @param {number} pointerY
     * @returns {HTMLDivElement}
     */
    const createMoveDragPreview = (items, pointerX, pointerY) => {
        const preview = document.createElement("div");
        Object.assign(preview.style, {
            position: "fixed",
            left: `${Math.max(0, Math.min(pointerX, window.innerWidth - 330))}px`,
            top: `${Math.max(0, Math.min(pointerY, window.innerHeight - 220))}px`,
            width: "320px",
            maxWidth: "320px",
            padding: "10px",
            border: "1px solid rgba(62, 207, 142, 0.8)",
            borderRadius: "10px",
            background: "#1f1f1f",
            color: "#fafafa",
            boxShadow: "0 16px 40px rgba(0, 0, 0, 0.4)",
            fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
            pointerEvents: "none",
            zIndex: "2147483647",
            // Keep the preview visible immediately. Waiting for requestAnimationFrame
            // left it transparent when the browser began a native text drag from a
            // filename and paused the custom mouse-move lifecycle.
            opacity: "1",
            transform: "translate3d(0, 0, 0) scale(1)",
            transformOrigin: "top left",
            transition: "opacity 150ms ease-out, transform 150ms cubic-bezier(0.2, 0.75, 0.25, 1)",
            willChange: "opacity, transform",
        });

        const heading = document.createElement("div");
        heading.textContent = `Moving ${items.length} ${items.length === 1 ? "item" : "items"}`;
        Object.assign(heading.style, {
            color: "#3ecf8e", fontSize: "11px", fontWeight: "600", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "7px",
        });
        preview.appendChild(heading);

        const visibleItems = items.slice(0, 6);
        for (const item of visibleItems) {
            const row = document.createElement("div");
            Object.assign(row.style, {
                display: "flex",
                alignItems: "center",
                gap: "9px",
                minWidth: "0",
                padding: "6px 7px",
                borderRadius: "6px",
                background: "rgba(255,255,255,0.045)",
                marginTop: "3px",
                fontSize: "12px",
            });

            const icon = document.createElement("span");
            // Keep drag-preview rendering independent from API data shape. A malformed
            // or missing filename must never prevent the preview from being created.
            let fileIcon = "draft";
            if (item.type === "folder") {
                fileIcon = "folder";
            } else {
                try {
                    fileIcon = getFileIcon(String(item.name ?? "")).icon || "draft";
                } catch {
                    fileIcon = "draft";
                }
            }
            icon.textContent = fileIcon;
            icon.style.fontFamily = "'Material Symbols Outlined'";
            Object.assign(icon.style, {
                flex: "0 0 auto", fontSize: "18px", color: item.type === "folder" ? "#3ecf8e" : "#70b8ff",
            });

            const name = document.createElement("span");
            name.textContent = String(item.name ?? "Untitled");
            Object.assign(name.style, {
                display: "block", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: "1",
            });
            row.append(icon, name);
            preview.appendChild(row);
        }

        if (items.length > visibleItems.length) {
            const extra = document.createElement("div");
            extra.textContent = `+ ${items.length - visibleItems.length} more`;
            Object.assign(extra.style, {fontSize: "11px", color: "#a0a0a0", padding: "7px 7px 0"});
            preview.appendChild(extra);
        }

        document.body.appendChild(preview);
        return preview;
    };

    /**
     * Fade out and remove a detached drag preview.
     * @param {HTMLDivElement | null} preview
     * @returns {void}
     */
    const animateMoveDragPreviewOut = (preview) => {
        if (!preview?.isConnected) return;
        preview.style.transition = "opacity 120ms ease-in, transform 120ms ease-in";
        preview.style.opacity = "0";
        preview.style.transform = "translate3d(0, 4px, 0) scale(0.98)";
        window.setTimeout(() => preview.remove(), 130);
    };

    // Use pointer-driven dragging instead of the browser's native HTML5 drag
    // lifecycle. Native dragging was being cancelled when React updated the
    // source rows during dragstart, especially for a multi-selection.
    /**
     * Capture the item selection and pointer coordinates for a move drag.
     * @param {import("react").MouseEvent<HTMLElement>} event
     * @param {SelectableItem} item
     * @returns {void}
     */
    const beginMoveDrag = (event, item) => {
        if (event.button !== 0 || isTrash || item.is_deleted || movingItems || confirmAction) return;
        if (event.target.closest("[data-selection-ignore]")) return;

        // File names are rendered as text inside a button. Prevent the browser's
        // native text-drag operation on mousedown so it cannot take over the
        // pointer stream before our floating preview becomes visible.
        if (item.type === "file") event.preventDefault();

        const isPartOfSelection = selectedItems.has(item.key);
        const selected = isPartOfSelection ? selectableItems.filter((candidate) => selectedItems.has(candidate.key) && !candidate.is_deleted) : [item];
        const itemsToMove = reduceMoveItemsToTopLevel(selected).map(({key, type, id, name, path, directory}) => ({
            key, type, id, name, path, directory,
        }));
        if (!itemsToMove.length) return;

        movePointerRef.current = {
            startX: event.clientX,
            startY: event.clientY,
            pointerX: event.clientX,
            pointerY: event.clientY,
            item,
            payload: itemsToMove,
            active: false,
            destinationPath: null,
            targetKey: null,
            wasSelected: isPartOfSelection,
        };
    };


    /**
     * Move the selected top-level items to a destination directory.
     * @param {SelectableItem[]} items
     * @param {string} destinationPath
     * @returns {Promise<{ok: boolean, partial: boolean, failed?: Array<{type: string, id: string | number, error?: string}>, message: string}>}
     */
    const moveItemsToDirectory = async (items, destinationPath) => {
        if (movingItems) return {ok: false, partial: false, message: "Another move is already in progress."};
        const token = getToken();
        if (!token) {
            navigate("/");
            return {ok: false, partial: false, message: "Your session has expired. Sign in again before moving items."};
        }

        const targetPath = normalizeDirectoryPath(destinationPath);
        const topLevelItems = reduceMoveItemsToTopLevel(items);
        if (!topLevelItems.length) return {ok: false, partial: false, message: "There are no items available to move."};

        let moveResult = {ok: false, partial: false, message: "The move could not be completed."};
        setMovingItems(true);
        setPageError("");

        try {
            // Send the complete mixed file/folder selection in one request. The
            // backend returns per-item results so name conflicts do not hide which
            // items succeeded or failed.
            const response = await fetch(`${API_URL}/auth/move`, {
                method: "PATCH", headers: {
                    Authorization: `Bearer ${token}`, "Content-Type": "application/json",
                }, body: JSON.stringify({
                    directory: targetPath, items: topLevelItems.map(({type, id}) => ({type, id})),
                }),
            });

            let data = {};
            try {
                data = await response.json();
            } catch {
                // Keep the generic error below if the response is not JSON.
            }
            if (!response.ok) throw new Error(data.detail || data.message || "Failed to move selected items.");

            const movedCount = Array.isArray(data.moved) ? data.moved.length : 0;
            const unchangedCount = Array.isArray(data.unchanged) ? data.unchanged.length : 0;
            const failedItems = Array.isArray(data.failed) ? data.failed : [];
            if (failedItems.length) {
                const itemsByIdentity = new Map(topLevelItems.map((item) => [`${item.type}:${item.id}`, item]));
                const failures = failedItems.map((failure) => {
                    const item = itemsByIdentity.get(`${failure.type}:${failure.id}`);
                    return `${item?.name || `${failure.type} ${failure.id}`}: ${failure.error || "Move failed"}`;
                });
                const firstFailures = failures.slice(0, 3).join("; ");
                const more = failures.length > 3 ? `; and ${failures.length - 3} more` : "";
                const message = `${movedCount} item(s) moved${unchangedCount ? `, ${unchangedCount} already in the destination` : ""}. ${failures.length} failed: ${firstFailures}${more}`;
                setPageError(message);
                moveResult = {
                    ok: false,
                    partial: movedCount + unchangedCount > 0,
                    failed: failedItems,
                    message,
                };
            } else {
                moveResult = {
                    ok: true,
                    partial: false,
                    message: `${movedCount} item(s) moved${unchangedCount ? `; ${unchangedCount} already in the destination` : ""}.`,
                };
            }

            const sourceItems = new Map(topLevelItems.map((item) => [`${item.type}:${String(item.id)}`, item]));
            for (const movedItem of Array.isArray(data.moved) ? data.moved : []) {
                const source = sourceItems.get(`${movedItem.type}:${String(movedItem.id)}`);
                if (!source) continue;
                if (movedItem.type === "file") {
                    const destination = movedItem.directory || targetPath;
                    setFiles((current) => current.map((file) => String(file.id) === String(movedItem.id)
                        ? {...file, directory: destination === "/home" ? "" : destination, directory_id: movedItem.directory_id ?? file.directory_id}
                        : file));
                } else if (movedItem.type === "folder" && source.path) {
                    const nextPath = movedItem.path || `${targetPath.replace(/\/$/, "")}/${source.name}`;
                    setFolders((current) => current.map((folder) => ({
                        ...folder,
                        path: replacePathPrefix(folder.path, source.path, nextPath),
                    })));
                    setFiles((current) => current.map((file) => ({
                        ...file,
                        directory: replacePathPrefix(file.directory || "", source.path, nextPath),
                    })));
                }
            }

            clearSelection();
            setSearchQuery("");
        } catch (error) {
            const message = error.message || "Failed to move selected items.";
            setPageError(message);
            moveResult = {ok: false, partial: false, message};
        } finally {
            setMovingItems(false);
            // Clear only drag state that is actually declared and used by the dashboard.
            // A stale, undeclared reference here throws after the server move
            // succeeds, causing the modal to display a false failure.
            moveDragRef.current = null;
            movePointerRef.current = null;
            dragStateRef.current = null;
            setDraggingKeys(new Set());
            setDropTargetKey(null);
        }
        return moveResult;
    };

    // The document-level pointer handlers are installed once, so give them the
    // latest React callbacks on every render (not stale folder/loading closures).
    moveDndCallbacksRef.current = {canDropItemsIntoDirectory, moveItemsToDirectory};

    /**
     * Update file selection, including modifier-key and range selection.
     * @param {SelectableItem} item
     * @param {import("react").MouseEvent<HTMLElement>} event
     * @returns {void}
     */
    const handleSelectItem = (item, event) => {
        if (suppressClickRef.current) {
            suppressClickRef.current = false;
            return;
        }

        const itemIndex = selectableItems.findIndex((candidate) => candidate.key === item.key);
        const anchorIndex = selectableItems.findIndex((candidate) => candidate.key === selectionAnchorRef.current);

        if (event.shiftKey && anchorIndex >= 0 && itemIndex >= 0) {
            const next = event.ctrlKey || event.metaKey ? new Set(selectedItems) : new Set();
            const start = Math.min(anchorIndex, itemIndex);
            const end = Math.max(anchorIndex, itemIndex);
            for (let i = start; i <= end; i += 1) next.add(selectableItems[i].key);
            setSelectedItems(next);
        } else if (event.ctrlKey || event.metaKey) {
            const next = new Set(selectedItems);
            if (next.has(item.key)) next.delete(item.key); else next.add(item.key);
            setSelectedItems(next);
            selectionAnchorRef.current = item.key;
        } else {
            setSelectedItems(new Set([item.key]));
            selectionAnchorRef.current = item.key;
        }
    };

    /**
     * Clear all selected items and the range-selection anchor.
     * @returns {void}
     */
    const clearSelection = () => {
        setSelectedItems(new Set());
        selectionAnchorRef.current = null;
    };

    /**
     * Open a selected folder or previewable file outside the trash.
     * @param {SelectableItem} item
     * @returns {void}
     */
    const openSelectedItem = (item) => {
        if (!item || activeTab === "trash") return;
        if (item.type === "folder") openFolder(item.path); else handleFileClick(item.object);
    };

    /**
     * Rename a file or folder and synchronize affected dashboard paths.
     * @param {SelectableItem} item
     * @param {string} newName
     * @returns {Promise<void>}
     */
    const renameItem = async (item, newName) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            throw new Error("Your session has expired. Please sign in again.");
        }

        if (newName === item.name) {
            setRenameTarget(null);
            renameSelectionSnapshotRef.current = null;
            return;
        }

        const resource = item.type === "folder" ? "directory" : "file";
        const response = await fetch(`${API_URL}/auth/${resource}/${item.id}/rename?name=${encodeURIComponent(newName)}`, {
            method: "PATCH", headers: {Authorization: `Bearer ${token}`},
        },);

        let data = {};
        try {
            data = await response.json();
        } catch {
            // Keep the fallback response below if the server sends no JSON.
        }
        if (!response.ok) {
            throw new Error(data.detail || data.message || `Failed to rename ${item.type}.`);
        }

        const finalName = data.name || newName;
        if (item.type === "file") {
            setFiles((current) => current.map((file) => (String(file.id) === String(item.id) ? {...file, name: finalName} : file)));
        } else {
            const oldPath = item.path;
            const newPath = data.path || `${oldPath.slice(0, oldPath.lastIndexOf("/"))}/${finalName}`;
            setFolders((current) => current.map((folder) => ({
                ...folder, path: replacePathPrefix(folder.path, oldPath, newPath),
            })));
            setFiles((current) => current.map((file) => ({
                ...file, directory: replacePathPrefix(file.directory || "", oldPath, newPath),
            })));
        }

        setRenameTarget(null);
        renameSelectionSnapshotRef.current = null;
    };

    /**
     * Save the current selection before showing the rename dialog.
     * @param {SelectableItem} item
     * @returns {void}
     */
    const openRenameDialog = (item) => {
        // Keep selection and the range-selection anchor intact when the dialog
        // is cancelled. The selected item remains selected after a successful rename too.
        renameSelectionSnapshotRef.current = {
            keys: Array.from(selectedItems), anchor: selectionAnchorRef.current,
        };
        setRenameTarget(item);
    };

    /**
     * Close rename UI and restore the selection snapshot.
     * @returns {void}
     */
    const closeRenameDialog = () => {
        const snapshot = renameSelectionSnapshotRef.current;
        if (snapshot) {
            setSelectedItems(new Set(snapshot.keys));
            selectionAnchorRef.current = snapshot.anchor;
        }
        renameSelectionSnapshotRef.current = null;
        setRenameTarget(null);
    };

    /**
     * Capture the selected items and request confirmation for a bulk action.
     * @param {string} mode
     * @returns {void}
     */
    const requestBulkConfirmation = (mode) => {
        const items = selectableItems.filter((item) => selectedItems.has(item.key));
        if (!items.length) return;
        setBulkActionError("");
        setConfirmFocus("cancel");
        setConfirmAction({
            mode, items, // Keep the exact pre-dialog selection so Escape/Cancel restores it.
            selectionSnapshot: Array.from(selectedItems), selectionAnchor: selectionAnchorRef.current,
        });
    };

    /**
     * Cancel a pending bulk action and restore its prior selection.
     * @returns {void}
     */
    const cancelBulkConfirmation = () => {
        if (!confirmAction || bulkActionLoading) return;
        setSelectedItems(new Set(confirmAction.selectionSnapshot ?? confirmAction.items.map((item) => item.key)));
        selectionAnchorRef.current = confirmAction.selectionAnchor ?? confirmAction.items[0]?.key ?? null;
        setConfirmAction(null);
        setBulkActionError("");
    };

    useEffect(() => {
        if (!confirmAction) return undefined;
        setConfirmFocus("cancel");
        const frame = window.requestAnimationFrame(() => cancelConfirmButtonRef.current?.focus());
        return () => window.cancelAnimationFrame(frame);
    }, [confirmAction]);

    /**
     * Begin a marquee-selection gesture on empty dashboard space.
     * @param {import("react").MouseEvent<HTMLElement>} event
     * @returns {void}
     */
    const handleSelectionMouseDown = (event) => {
        if (event.button !== 0 || confirmAction) return;
        if (event.target.closest("[data-selection-ignore]")) return;

        const itemNode = event.target.closest("[data-selection-id]");
        // Item drags are native HTML drag-and-drop; the marquee starts only on empty space.
        if (itemNode) return;
        if (event.target.closest("button, input, textarea, select, a")) return;

        dragStateRef.current = {
            startX: event.clientX,
            startY: event.clientY,
            dragging: false,
            baseSelection: new Set(selectedItems),
            preserve: event.ctrlKey || event.metaKey || event.shiftKey,
        };
        event.preventDefault();
    };

    useEffect(() => {
        /**
         * Update move-drag feedback or the marquee selection while the pointer moves.
         * @param {MouseEvent} event
         * @returns {void}
         */
        const onMouseMove = (event) => {
            const move = movePointerRef.current;
            if (move) {
                move.pointerX = event.clientX;
                move.pointerY = event.clientY;
                const dx = event.clientX - move.startX;
                const dy = event.clientY - move.startY;

                if (!move.active && Math.hypot(dx, dy) >= 6) {
                    // Build and attach the floating preview before hiding the source
                    // item. Previously a preview-rendering exception could leave a
                    // file invisible with nothing following the pointer.
                    let preview = null;
                    try {
                        preview = createMoveDragPreview(move.payload, event.clientX + 12, event.clientY + 12);
                    } catch (error) {
                        console.error("Could not create the move drag preview:", error);
                    }
                    if (!preview?.isConnected) {
                        movePointerRef.current = null;
                        move.active = false;
                        return;
                    }

                    move.active = true;
                    suppressClickRef.current = true;
                    moveDragRef.current = move.payload;
                    dragPreviewNodeRef.current = preview;
                    if (!move.wasSelected) {
                        setSelectedItems(new Set([move.item.key]));
                        selectionAnchorRef.current = move.item.key;
                    }
                    setDraggingKeys(new Set(move.payload.map((entry) => entry.key)));
                }

                if (move.active) {
                    const preview = dragPreviewNodeRef.current;
                    if (preview?.isConnected) {
                        preview.style.left = `${Math.max(8, Math.min(event.clientX + 12, window.innerWidth - 328))}px`;
                        preview.style.top = `${Math.max(8, Math.min(event.clientY + 12, window.innerHeight - 220))}px`;
                    }

                    const hovered = document.elementFromPoint(event.clientX, event.clientY);
                    const dropNode = hovered?.closest?.("[data-drop-path][data-drop-key]");
                    const destinationPath = dropNode?.getAttribute("data-drop-path") || null;
                    const targetKey = dropNode?.getAttribute("data-drop-key") || null;
                    const callbacks = moveDndCallbacksRef.current;
                    const canDrop = Boolean(destinationPath && callbacks?.canDropItemsIntoDirectory?.(move.payload, destinationPath));

                    move.destinationPath = canDrop ? destinationPath : null;
                    move.targetKey = canDrop ? targetKey : null;
                    setDropTargetKey(canDrop ? targetKey : null);
                }
                return;
            }

            const drag = dragStateRef.current;
            const surface = selectionSurfaceRef.current;
            if (!drag || !surface) return;

            const dx = event.clientX - drag.startX;
            const dy = event.clientY - drag.startY;
            if (!drag.dragging && Math.hypot(dx, dy) < 5) return;
            drag.dragging = true;
            suppressClickRef.current = true;

            const surfaceRect = surface.getBoundingClientRect();
            const left = Math.min(drag.startX, event.clientX);
            const top = Math.min(drag.startY, event.clientY);
            const right = Math.max(drag.startX, event.clientX);
            const bottom = Math.max(drag.startY, event.clientY);
            setMarquee({
                left: left - surfaceRect.left, top: top - surfaceRect.top, width: right - left, height: bottom - top,
            });

            const next = drag.preserve ? new Set(drag.baseSelection) : new Set();
            surface.querySelectorAll("[data-selection-id]").forEach((node) => {
                const rect = node.getBoundingClientRect();
                const intersects = rect.right >= left && rect.left <= right && rect.bottom >= top && rect.top <= bottom;
                if (intersects) next.add(node.getAttribute("data-selection-id"));
            });
            setSelectedItems(next);
        };

        /**
         * Finish a move drag or marquee selection when the pointer is released.
         * @param {MouseEvent} event
         * @returns {void}
         */
        const onMouseUp = (event) => {
            const move = movePointerRef.current;
            if (move) {
                movePointerRef.current = null;
                if (move.active) {
                    suppressClickRef.current = true;
                    const hovered = document.elementFromPoint(event.clientX, event.clientY);
                    const dropNode = hovered?.closest?.("[data-drop-path][data-drop-key]");
                    const destinationPath = dropNode?.getAttribute("data-drop-path") || move.destinationPath;
                    const callbacks = moveDndCallbacksRef.current;
                    const canDrop = Boolean(destinationPath && callbacks?.canDropItemsIntoDirectory?.(move.payload, destinationPath));

                    animateMoveDragPreviewOut(dragPreviewNodeRef.current);
                    dragPreviewNodeRef.current = null;
                    setDropTargetKey(null);

                    if (canDrop) {
                        setDraggingKeys(new Set(move.payload.map((entry) => entry.key)));
                        callbacks.moveItemsToDirectory(move.payload, destinationPath);
                    } else {
                        moveDragRef.current = null;
                        setDraggingKeys(new Set());
                    }
                    window.requestAnimationFrame(() => {
                        suppressClickRef.current = false;
                    });
                }
                return;
            }

            const drag = dragStateRef.current;
            if (!drag) return;
            dragStateRef.current = null;
            if (drag.dragging) {
                setMarquee(null);
                // Suppress the click that the browser emits after a marquee drag.
                window.requestAnimationFrame(() => {
                    suppressClickRef.current = false;
                });
            }
        };

        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
        return () => {
            document.removeEventListener("mousemove", onMouseMove);
            document.removeEventListener("mouseup", onMouseUp);
        };
    }, []);

    // Persist selection changes under the route where they occurred, so both
    // browser Back/Forward and Backspace can restore each directory independently.
    useEffect(() => {
        if (selectionSnapshotInitialRenderRef.current) {
            // Don't overwrite a saved selection with the initial empty React state
            // before the current directory has finished loading and restoration runs.
            selectionSnapshotInitialRenderRef.current = false;
            return;
        }
        if (lastSelectionSnapshotViewKeyRef.current !== currentSelectionViewKey) {
            lastSelectionSnapshotViewKeyRef.current = currentSelectionViewKey;
            return;
        }
        if (skipSelectionSnapshotPersistRef.current) {
            skipSelectionSnapshotPersistRef.current = false;
            return;
        }
        saveCurrentSelectionSnapshot();
        // The callback intentionally reads the latest selection anchor from its ref.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentSelectionViewKey, selectedItems]);

    useEffect(() => {
        const legacySnapshot = selectionRestoreRef.current.snapshot;
        const legacyMatchesCurrentView = legacySnapshot && legacySnapshot.pathname === location.pathname && legacySnapshot.activeTab === activeTab && legacySnapshot.searchQuery === searchQuery;
        const directorySnapshot = selectionSnapshotsByViewRef.current?.[currentSelectionViewKey];
        const shouldRestoreSelection = Boolean(legacyMatchesCurrentView || directorySnapshot);

        if (!shouldRestoreSelection) {
            if (selectedItems.size > 0) {
                // The next render is the intentional reset caused by navigation;
                // don't persist that reset over the target view's saved snapshot.
                skipSelectionSnapshotPersistRef.current = true;
                setSelectedItems(new Set());
                selectionAnchorRef.current = null;
            }
        }

        // A completed or interrupted drag must never leave matching item IDs
        // hidden after navigation. Clear transient drag styling on every view change.
        setDraggingKeys(new Set());
        setDropTargetKey(null);
        movePointerRef.current = null;
        if (dragPreviewNodeRef.current?.isConnected) {
            animateMoveDragPreviewOut(dragPreviewNodeRef.current);
        }
        dragPreviewNodeRef.current = null;
    }, [location.pathname, activeTab, searchQuery]);

    useEffect(() => {
        const legacySnapshot = selectionRestoreRef.current.snapshot;
        const legacyMatchesCurrentView = legacySnapshot && legacySnapshot.pathname === location.pathname && legacySnapshot.activeTab === activeTab && legacySnapshot.searchQuery === searchQuery;

        if (legacySnapshot && !legacyMatchesCurrentView) {
            selectionRestoreRef.current.snapshot = null;
            sessionStorage.removeItem(DASHBOARD_SELECTION_STORAGE_KEY);
        }

        const snapshot = legacyMatchesCurrentView ? legacySnapshot : selectionSnapshotsByViewRef.current?.[currentSelectionViewKey];
        if (!snapshot) return;

        // Wait for directory/search data to finish before matching saved IDs.
        // A route change can render once before the new directory fetch flips
        // loadingFiles to true. Wait until results for this exact folder are loaded,
        // otherwise stale rows from the previous directory can erase its snapshot.
        if (loadingFiles || loadedFolder !== (currentFolder || "") || (searchQuery.trim() && searchLoading)) return;

        const availableKeys = new Set(selectableItems.map((item) => item.key));
        const restoredKeys = (Array.isArray(snapshot.keys) ? snapshot.keys : [])
            .filter((key) => availableKeys.has(key));
        setSelectedItems(new Set(restoredKeys));
        selectionAnchorRef.current = availableKeys.has(snapshot.anchor) ? snapshot.anchor : (restoredKeys[0] ?? null);

        if (legacyMatchesCurrentView) {
            selectionRestoreRef.current.snapshot = null;
            sessionStorage.removeItem(DASHBOARD_SELECTION_STORAGE_KEY);
        }
    }, [currentSelectionViewKey, location.pathname, activeTab, searchQuery, currentFolder, loadedFolder, loadingFiles, searchLoading, selectableItems,]);

    useEffect(() => {
        /**
         * Handle the dashboard selection and bulk-action keyboard shortcuts.
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        const onKeyDown = (event) => {
            if (showUploadModal || moveDialogItems) return;
            if (contextMenu && event.key === "Escape") {
                event.preventDefault();
                setContextMenu(null);
                return;
            }
            // The rename dialog owns all keyboard input while open, including
            // Escape, so background shortcuts cannot clear or alter the selection.
            if (renameTarget) return;

            if (event.key === "Escape") {
                if (confirmAction) {
                    cancelBulkConfirmation();
                    return;
                }
                clearSelection();
                setMarquee(null);
                dragStateRef.current = null;
                suppressClickRef.current = false;
                setBulkActionError("");
                return;
            }

            // While a confirmation dialog is open, arrow keys switch between
            // Cancel and the destructive action, and Enter activates the focused
            // choice. Keep these keys out of the normal file-selection handler.
            if (confirmAction) {
                if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
                    event.preventDefault();
                    if (bulkActionLoading) return;
                    const nextFocus = confirmFocus === "cancel" ? "confirm" : "cancel";
                    setConfirmFocus(nextFocus);
                    (nextFocus === "cancel" ? cancelConfirmButtonRef : destructiveConfirmButtonRef).current?.focus();
                    return;
                }
                if (event.key === "Enter") {
                    event.preventDefault();
                    if (bulkActionLoading) return;
                    if (confirmFocus === "confirm") executeBulkAction(); else cancelBulkConfirmation();
                    return;
                }
                return;
            }

            const target = event.target;
            if (target instanceof HTMLElement && (target.isContentEditable || target.closest("input, textarea, select, [contenteditable='true']"))) return;

            if (event.altKey && event.key === "Enter") {
                const selected = selectableItems.filter((item) => selectedItems.has(item.key));
                if (selected.length === 1) {
                    event.preventDefault();
                    setDetailsTarget(selected[0]);
                }
                return;
            }

            if (event.key === "F2") {
                const selected = selectableItems.filter((item) => selectedItems.has(item.key));
                if (activeTab !== "trash" && selected.length === 1 && !bulkActionLoading) {
                    event.preventDefault();
                    openRenameDialog(selected[0]);
                }
                return;
            }

            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
                event.preventDefault();
                setSelectedItems(new Set(selectableItems.map((item) => item.key)));
                selectionAnchorRef.current = selectableItems[0]?.key || null;
                return;
            }

            if (event.key === "Delete") {
                const selected = selectableItems.filter((item) => selectedItems.has(item.key));
                if (!selected.length) return;
                event.preventDefault();
                requestBulkConfirmation(activeTab === "trash" || event.shiftKey ? "permanent" : "trash");
                return;
            }

            if (event.key === "Backspace") {
                event.preventDefault();
                if (activeTab !== "trash" && currentFolder) {
                    const parts = currentFolder.split("/").filter(Boolean);
                    if (parts.length > 1) {
                        const parentPath = `/${parts.slice(0, -1).join("/")}`;
                        navigate(folderPathToRoute(parentPath));
                    } else {
                        navigate("/home");
                    }
                    saveCurrentSelectionSnapshot();
                    setSearchQuery("");
                }
                return;
            }

            if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
                if (!selectableItems.length) return;
                event.preventDefault();

                const direction = {
                    ArrowUp: {axis: "y", sign: -1, fallback: -1},
                    ArrowDown: {axis: "y", sign: 1, fallback: 1},
                    ArrowLeft: {axis: "x", sign: -1, fallback: -1},
                    ArrowRight: {axis: "x", sign: 1, fallback: 1},
                }[event.key];

                // Prefer the selection anchor; if the current selection came from
                // a marquee, fall back to the first selected item in display order.
                const currentKey = selectedItems.has(selectionAnchorRef.current) ? selectionAnchorRef.current : selectableItems.find((item) => selectedItems.has(item.key))?.key;
                const currentIndex = selectableItems.findIndex((item) => item.key === currentKey);
                let nextItem = null;

                if (currentKey && currentIndex >= 0) {
                    const surface = selectionSurfaceRef.current;
                    const nodes = surface ? Array.from(surface.querySelectorAll("[data-selection-id]")) : [];
                    const currentNode = nodes.find((node) => node.getAttribute("data-selection-id") === currentKey);

                    if (currentNode) {
                        const currentRect = currentNode.getBoundingClientRect();
                        const currentX = currentRect.left + currentRect.width / 2;
                        const currentY = currentRect.top + currentRect.height / 2;
                        let bestScore = Infinity;

                        for (const node of nodes) {
                            const key = node.getAttribute("data-selection-id");
                            if (!key || key === currentKey) continue;
                            const candidate = selectableItems.find((item) => item.key === key);
                            if (!candidate) continue;

                            const rect = node.getBoundingClientRect();
                            const dx = rect.left + rect.width / 2 - currentX;
                            const dy = rect.top + rect.height / 2 - currentY;
                            const primaryDelta = direction.axis === "x" ? dx * direction.sign : dy * direction.sign;
                            if (primaryDelta <= 2) continue;

                            const crossDelta = Math.abs(direction.axis === "x" ? dy : dx);
                            // Favor items in the same row for left/right and in the
                            // same column for up/down, while still allowing grid edges.
                            const score = primaryDelta + crossDelta * 2;
                            if (score < bestScore) {
                                bestScore = score;
                                nextItem = candidate;
                            }
                        }
                    }

                    // File rows occupy the same horizontal span, so left/right may
                    // have no spatial neighbour. Fall back to the display order.
                    if (!nextItem) {
                        const nextIndex = Math.max(0, Math.min(selectableItems.length - 1, currentIndex + direction.fallback));
                        nextItem = selectableItems[nextIndex];
                    }
                } else {
                    // With no selection, start at the nearest end for the direction.
                    nextItem = direction.fallback < 0 ? selectableItems[selectableItems.length - 1] : selectableItems[0];
                }

                if (nextItem) {
                    setSelectedItems(new Set([nextItem.key]));
                    selectionAnchorRef.current = nextItem.key;
                    const node = selectionSurfaceRef.current ? Array.from(selectionSurfaceRef.current.querySelectorAll("[data-selection-id]")).find((element) => element.getAttribute("data-selection-id") === nextItem.key,) : null;
                    node?.scrollIntoView({block: "nearest", inline: "nearest", behavior: "auto"});
                }
                return;
            }

            if (event.key === "Enter") {
                const selected = selectableItems.filter((item) => selectedItems.has(item.key));
                if (selected.length === 1 && activeTab !== "trash") {
                    event.preventDefault();
                    openSelectedItem(selected[0]);
                }
            }
        };

        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [activeTab, bulkActionLoading, cancelBulkConfirmation, confirmAction, confirmFocus, contextMenu, currentFolder, moveDialogItems, navigate, renameTarget, searchQuery, selectableItems, selectedItems, showUploadModal]);

    const breadcrumbSegments = useMemo(() => {
        // Keep the internal folder paths for navigation, but show `home` as the top-level location label.
        const parts = (currentFolder || "").split("/").filter(Boolean);
        const segments = [{label: "home", path: "", isHome: true}];
        let accumulatedPath = "";

        for (const part of parts) {
            accumulatedPath += `/${part}`;
            if (part.toLowerCase() === "home" && segments.length === 1) continue;
            segments.push({label: part, path: accumulatedPath, isHome: false});
        }

        return segments;
    }, [currentFolder]);

    const isEmpty = filteredFiles.length === 0 && filteredFolders.length === 0;
    const isSearchBusy = isSearching && searchLoading;

    return (<div
        className="flex h-screen overflow-hidden bg-[#1c1c1c] text-[#fafafa]"
        style={{fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"}}
    >
        {/* Desktop sidebar */}
        <div className="fixed left-0 top-0 hidden h-screen md:flex">
            <Sidebar
                activeTab={activeTab}
                onTabSelect={handleTabChange}
                onLogout={handleLogout}
                storageUsed={storageUsed}
            />
        </div>

        {/* Mobile sidebar */}
        {sidebarOpen && (<div className="fixed inset-0 z-50 flex md:hidden">
            <div className="flex-shrink-0">
                <Sidebar
                    activeTab={activeTab}
                    onTabSelect={(tab) => {
                        handleTabChange(tab);
                        setSidebarOpen(false);
                    }}
                    onLogout={handleLogout}
                    storageUsed={storageUsed}
                />
            </div>
            <div
                className="flex-1 bg-black/60"
                onClick={() => setSidebarOpen(false)}
                aria-hidden="true"
            />
        </div>)}

        <DashboardWorkspace
            activeTab={activeTab}
            handleTabChange={handleTabChange}
            handleLogout={handleLogout}
            storageUsed={storageUsed}
            sidebarOpen={sidebarOpen}
            setSidebarOpen={setSidebarOpen}
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            isTrash={isTrash}
            selectionSurfaceRef={selectionSurfaceRef}
            handleSelectionMouseDown={handleSelectionMouseDown}
            handleBackgroundContextMenu={handleBackgroundContextMenu}
            suppressClickRef={suppressClickRef}
            clearSelection={clearSelection}
            breadcrumbSegments={breadcrumbSegments}
            openFolder={openFolder}
            dropTargetKey={dropTargetKey}
            pageError={pageError}
            fetchFiles={fetchFiles}
            searchError={searchError}
            isSearching={isSearching}
            loadingFiles={loadingFiles}
            isSearchBusy={isSearchBusy}
            searchLoading={searchLoading}
            filteredFolders={filteredFolders}
            filteredFiles={filteredFiles}
            isEmpty={isEmpty}
            marquee={marquee}
            selectedItems={selectedItems}
            handleSelectItem={handleSelectItem}
            beginMoveDrag={beginMoveDrag}
            handleItemContextMenu={handleItemContextMenu}
            openSelectedItem={openSelectedItem}
            handleItemMenuButton={handleItemMenuButton}
            draggingKeys={draggingKeys}
            deletingId={deletingId}
        />

        {contextMenu && (<DashboardContextMenu
            menu={contextMenu}
            multipleSelected={Boolean(contextMenu.item && selectedItems.has(contextMenu.item.key) && selectableItems.filter((item) => selectedItems.has(item.key)).length > 1)}
            onClose={() => setContextMenu(null)}
            onAction={performContextMenuAction}
        />)}

        <UploadJobsTray
            uploadJobs={uploadJobs}
            cancelUploadJob={cancelUploadJob}
            dismissUploadJob={dismissUploadJob}
        />

        <BulkActionConfirmModal
            confirmAction={confirmAction}
            bulkActionError={bulkActionError}
            bulkActionLoading={bulkActionLoading}
            cancelConfirmButtonRef={cancelConfirmButtonRef}
            destructiveConfirmButtonRef={destructiveConfirmButtonRef}
            setConfirmFocus={setConfirmFocus}
            cancelBulkConfirmation={cancelBulkConfirmation}
            executeBulkAction={executeBulkAction}
        />

        {renameTarget && (<RenameItemModal
            item={renameTarget}
            onClose={closeRenameDialog}
            onRename={renameItem}
        />)}

        {detailsTarget && (<ItemDetailsModal
            item={detailsTarget}
            onClose={() => setDetailsTarget(null)}
        />)}

        {showFolderModal && (<CreateFolderModal
            targetPath={currentFolder}
            onClose={() => setShowFolderModal(false)}
            onCreate={createFolder}
        />)}

        {showUploadModal && (<UploadPage
            modal
            targetDirectory={currentFolder || "/home"}
            onClose={() => setShowUploadModal(false)}
        />)}

        {moveDialogItems && (<MoveModal
            items={moveDialogItems}
            currentFolder={currentFolder || "/home"}
            folders={folders}
            canMoveTo={canDropItemsIntoDirectory}
            onClose={() => setMoveDialogItems(null)}
            onMove={(items, destinationPath) => moveItemsToDirectory(items, destinationPath)}
        />)}
    </div>);
}
