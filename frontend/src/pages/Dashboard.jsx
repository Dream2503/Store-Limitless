import {useCallback, useEffect, useMemo, useState} from "react";
import {useNavigate} from "react-router-dom";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const NAV_ITEMS = [{id: "files", icon: "folder_open", label: "All Files", enabled: true}, {
    id: "trash", icon: "delete", label: "Trash", enabled: true
},];

const FILE_ICONS = {
    pdf: {icon: "picture_as_pdf", color: "text-error"},
    jpg: {icon: "image", color: "text-secondary"},
    jpeg: {icon: "image", color: "text-secondary"},
    png: {icon: "image", color: "text-secondary"},
    gif: {icon: "image", color: "text-secondary"},
    webp: {icon: "image", color: "text-secondary"},
    txt: {icon: "subject", color: "text-outline"},
    zip: {icon: "folder_zip", color: "text-tertiary"},
    rar: {icon: "folder_zip", color: "text-tertiary"},
    mp4: {icon: "movie", color: "text-primary"},
    mkv: {icon: "movie", color: "text-primary"},
    mp3: {icon: "music_note", color: "text-primary"},
    wav: {icon: "music_note", color: "text-primary"},
    py: {icon: "code", color: "text-primary"},
    js: {icon: "javascript", color: "text-primary"},
    jsx: {icon: "code", color: "text-primary"},
    default: {icon: "draft", color: "text-on-surface-variant"},
};

function getFileIcon(filename) {
    const extension = filename?.split(".").pop()?.toLowerCase();
    return FILE_ICONS[extension] || FILE_ICONS.default;
}

function formatDate(dateStr) {
    if (!dateStr) return "—";
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleDateString("en-US", {
        month: "short", day: "numeric", year: "numeric",
    });
}

function formatFileSize(bytes) {
    if (!bytes || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function getToken() {
    return localStorage.getItem("token");
}

/* -------------------------------------------------------------------- */
/* Sidebar                                                             */

/* -------------------------------------------------------------------- */

function Sidebar({activeTab, onTabSelect, onUpload, onLogout}) {
    return (<nav className="flex h-screen w-72 flex-col gap-4 border-r border-outline-variant/20 bg-surface/70 p-6 backdrop-blur-xl">
        <div className="mb-4 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary">
                    <span
                        className="material-symbols-outlined text-on-primary"
                        style={{fontVariationSettings: "'FILL' 1"}}
                        aria-hidden="true"
                    >
                        cloud
                    </span>
            </div>
            <div>
                <h1 className="text-lg font-bold text-primary">DriveBot</h1>
                <p className="font-geist text-xs uppercase tracking-widest text-on-surface-variant">
                    File Management
                </p>
            </div>
        </div>

        <button
            type="button"
            onClick={onUpload}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-on-primary shadow-[0_0_15px_rgba(192,193,255,0.2)] transition-colors hover:bg-primary/90"
        >
                <span className="material-symbols-outlined text-[18px]" aria-hidden="true">
                    add
                </span>
            Upload Files
        </button>

        <div className="mt-2 flex flex-1 flex-col gap-1">
            {NAV_ITEMS.map(({id, icon, label, enabled}) => {
                const isActive = activeTab === id;
                return (<button
                    key={id}
                    type="button"
                    disabled={!enabled}
                    onClick={enabled ? () => onTabSelect(id) : undefined}
                    title={enabled ? undefined : "Coming soon"}
                    className={`flex items-center gap-3 rounded-xl px-3 py-2 text-left text-sm transition-all ${isActive ? "bg-primary-container/30 font-semibold text-primary" : enabled ? "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface" : "cursor-not-allowed text-on-surface-variant/40"}`}
                >
                            <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
                                {icon}
                            </span>
                    {label}
                </button>);
            })}
        </div>

        <div className="flex flex-col gap-1 border-t border-outline-variant/10 pt-4">
            <button
                type="button"
                className="flex items-center gap-3 rounded-xl px-3 py-2 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-on-surface"
            >
                    <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
                        help_outline
                    </span>
                Help
            </button>

            <button
                type="button"
                onClick={onLogout}
                className="flex items-center gap-3 rounded-xl px-3 py-2 text-sm text-on-surface-variant transition-colors hover:bg-error/10 hover:text-error"
            >
                    <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
                        logout
                    </span>
                Sign Out
            </button>
        </div>
    </nav>);
}

/* -------------------------------------------------------------------- */
/* Stream Preview Modal                                                */

/* -------------------------------------------------------------------- */

function StreamModal({file, streamUrl, onClose}) {
    useEffect(() => {
        const onKeyDown = (e) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [onClose]);

    const ext = file.name.split(".").pop()?.toLowerCase();
    const isVideo = ["mp4", "mkv", "webm", "mov"].includes(ext);
    const isAudio = ["mp3", "wav", "ogg"].includes(ext);
    const isImage = ["jpg", "jpeg", "png", "gif", "webp"].includes(ext);
    const isPdf = ext === "pdf";

    return (<div
        className="fixed inset-0 z-[110] flex items-center justify-center bg-black/80 px-4 backdrop-blur-md"
        onClick={onClose}
    >
        <div
            className="relative flex max-h-[90vh] w-full max-w-4xl flex-col rounded-2xl border border-outline-variant/20 bg-surface p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
        >
            <div className="mb-3 flex items-center justify-between border-b border-outline-variant/10 pb-3">
                <div className="flex items-center gap-2">
                        <span className="material-symbols-outlined text-primary" aria-hidden="true">
                            play_circle
                        </span>
                    <h3 className="truncate font-medium text-on-surface">{file.name}</h3>
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    className="rounded-lg p-1 text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                >
                    <span className="material-symbols-outlined">close</span>
                </button>
            </div>

            <div className="flex flex-1 items-center justify-center overflow-auto rounded-xl bg-black/40 p-2">
                {isVideo && (<video controls autoPlay className="max-h-[70vh] w-full rounded-lg">
                    <source src={streamUrl}/>
                    Your browser does not support playing this video format.
                </video>)}
                {isAudio && (<div className="py-12">
                    <audio controls autoPlay className="w-96">
                        <source src={streamUrl}/>
                        Your browser does not support audio playback.
                    </audio>
                </div>)}
                {isImage && (<img
                    src={streamUrl}
                    alt={file.name}
                    className="max-h-[70vh] max-w-full rounded-lg object-contain"
                />)}
                {isPdf && (<iframe
                    src={streamUrl}
                    title={file.name}
                    className="h-[70vh] w-full rounded-lg border-0"
                />)}
                {!isVideo && !isAudio && !isImage && !isPdf && (<iframe
                    src={streamUrl}
                    title={file.name}
                    className="h-[70vh] w-full rounded-lg bg-surface p-4 font-mono text-xs text-on-surface"
                />)}
            </div>
        </div>
    </div>);
}

/* -------------------------------------------------------------------- */
/* Create Folder Modal                                                 */

/* -------------------------------------------------------------------- */

function CreateFolderModal({targetPath, onClose, onCreate}) {
    const [name, setName] = useState("");
    const [error, setError] = useState("");
    const [creating, setCreating] = useState(false);

    useEffect(() => {
        const onKeyDown = (e) => {
            if (e.key === "Escape" && !creating) onClose();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [creating, onClose]);

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
            className="w-full max-w-md rounded-2xl border border-outline-variant/20 bg-surface p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
        >
            <div className="mb-5 flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-semibold text-on-surface">Create New Folder</h2>
                    <p className="mt-1 text-sm text-on-surface-variant">
                        Will be created inside <span className="font-medium text-on-surface">{targetPath || "Root"}</span>.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    disabled={creating}
                    aria-label="Close"
                    className="text-on-surface-variant hover:text-on-surface disabled:opacity-50"
                >
                    <span className="material-symbols-outlined" aria-hidden="true">close</span>
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
                    className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-low px-4 py-3 text-sm text-on-surface focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-60"
                />
                {error && <p className="mt-2 text-sm text-error">{error}</p>}
                <div className="mt-6 flex justify-end gap-3">
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={creating}
                        className="rounded-xl px-4 py-2 text-sm text-on-surface-variant hover:bg-surface-container disabled:opacity-50"
                    >
                        Cancel
                    </button>
                    <button
                        type="submit"
                        disabled={creating}
                        className="rounded-xl bg-primary px-5 py-2 text-sm font-medium text-on-primary transition-colors hover:bg-primary/90 disabled:opacity-60"
                    >
                        {creating ? "Creating…" : "Create Folder"}
                    </button>
                </div>
            </form>
        </div>
    </div>);
}

/* -------------------------------------------------------------------- */
/* Dashboard Page                                                      */

/* -------------------------------------------------------------------- */

export default function DashboardPage() {
    const navigate = useNavigate();

    const [activeTab, setActiveTab] = useState("files");
    const [files, setFiles] = useState([]);
    const [folders, setFolders] = useState([]);
    const [currentFolder, setCurrentFolder] = useState("");
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const [loadingFiles, setLoadingFiles] = useState(true);
    const [pageError, setPageError] = useState("");
    const [showFolderModal, setShowFolderModal] = useState(false);
    const [deletingId, setDeletingId] = useState(null);
    const [streamData, setStreamData] = useState(null);

    /* ---------------------------- fetch files --------------------------- */


    const fetchFiles = useCallback(async () => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        setLoadingFiles(true);
        setPageError("");

        try {
            const directory = currentFolder || "/home";
            const directoryRes = await fetch(`${API_URL}/auth/directory?directory=${encodeURIComponent(directory)}`, {
                headers: {Authorization: `Bearer ${token}`},
            });

            if (directoryRes.status === 401) {
                localStorage.removeItem("token");
                navigate("/");
                return;
            }

            const directoryData = await directoryRes.json();
            if (!directoryRes.ok) throw new Error(directoryData.detail || "Failed to fetch files");

            const [activeFolders, activeFiles] = directoryData;

            const trashRes = await fetch(`${API_URL}/auth/trash`, {
                headers: {Authorization: `Bearer ${token}`},
            });

            const trashData = trashRes.ok ? await trashRes.json() : [[], []];
            const [trashFolders, trashFiles] = trashData;

            const normalizedActive = (Array.isArray(activeFiles) ? activeFiles : []).map(f => ({
                ...f, directory: currentFolder, is_deleted: false,
            }));

            const normalizedTrash = (Array.isArray(trashFiles) ? trashFiles : []).map(f => ({
                ...f, is_deleted: true,
            }));

            setFiles([...normalizedActive, ...normalizedTrash]);

            const normalizedFolders = [...(Array.isArray(activeFolders) ? activeFolders : []).map(f => ({
                path: f.path, id: f.id, is_deleted: false,
            })), ...(Array.isArray(trashFolders) ? trashFolders : []).map(f => ({
                path: f.path, id: f.id, is_deleted: true,
            })),];

            setFolders(normalizedFolders);
        } catch (error) {
            console.error("Failed to fetch files:", error);
            setPageError(error.message || "Failed to load files.");
        } finally {
            setLoadingFiles(false);
        }
    }, [navigate, currentFolder]);


    useEffect(() => {
        fetchFiles();
    }, [fetchFiles]);

    useEffect(() => {
        if (!sidebarOpen) return;
        const onKeyDown = (event) => {
            if (event.key === "Escape") setSidebarOpen(false);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [sidebarOpen]);

    /* ----------------------------- stream/play ---------------------------- */

    const startStreaming = async (file) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        try {
            const linkRes = await fetch(`${API_URL}/auth/files/${file.id}/public-link`, {
                method: "POST", headers: {Authorization: `Bearer ${token}`},
            });
            if (!linkRes.ok) throw new Error("Failed to initialize stream.");
            const {url} = await linkRes.json();
            const streamToken = url.split("/").filter(Boolean).pop();
            setStreamData({file, url: `${API_URL}/public/stream/${streamToken}`});
        } catch (error) {
            console.error("Stream init error:", error);
            alert(error.message || "Could not stream file.");
        }
    };

    /* ----------------------------- download ------------------------------ */

    const downloadFile = async (fileId) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        try {
            const linkRes = await fetch(`${API_URL}/auth/files/${fileId}/public-link`, {
                method: "POST", headers: {Authorization: `Bearer ${token}`},
            });

            if (!linkRes.ok) {
                const errorData = await linkRes.json().catch(() => ({}));
                throw new Error(errorData.detail || "Failed to generate download link.");
            }

            const {url} = await linkRes.json();
            const downloadToken = url.split("/").filter(Boolean).pop();
            const downloadUrl = `${API_URL}/public/download/${downloadToken}`;

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
            const endpoint = permanent ? `${API_URL}/auth/trash/${fileId}` : `${API_URL}/auth/files/${fileId}`;

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

            await fetchFiles();
        } catch (error) {
            console.error("Delete failed:", error);
            alert(error.message || "Failed to delete file.");
        } finally {
            setDeletingId(null);
        }
    };

    const restoreItem = async (itemId) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        try {
            const response = await fetch(`${API_URL}/auth/trash/${itemId}/restore`, {
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

            await fetchFiles();
        } catch (error) {
            console.error("Restore failed:", error);
            alert(error.message || "Failed to restore item.");
        }
    };


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

            await fetchFiles();
        } catch (error) {
            console.error("Folder restore failed:", error);
            alert(error.message || "Failed to restore folder.");
        }
    };


    /* ------------------------------ folders -------------------------------- */

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

        await fetchFiles();
        setShowFolderModal(false);
    };


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
            const endpoint = permanent ? `${API_URL}/auth/trash/directory/${folderId}` : `${API_URL}/auth/directories/${folderId}`;

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

            await fetchFiles();
        } catch (error) {
            console.error("Folder deletion failed:", error);
            alert(error.message || "Failed to delete folder.");
        } finally {
            setDeletingId(null);
        }
    };


    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/");
    };

    const openFolder = (folderPath) => {
        setCurrentFolder(folderPath);
        setSearchQuery("");
    };

    const goBack = () => {
        if (!currentFolder) return;
        const parts = currentFolder.split("/");
        parts.pop();
        setCurrentFolder(parts.join("/"));
    };

    const handleTabChange = (tabId) => {
        setActiveTab(tabId);
        setCurrentFolder("");
        setSearchQuery("");
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

    const filteredFiles = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) return visibleFiles;
        return visibleFiles.filter((file) => file.name?.toLowerCase().includes(query));
    }, [visibleFiles, searchQuery]);

    const filteredFolders = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) return visibleFolders;
        return visibleFolders.filter(({path}) => path.split("/").pop()?.toLowerCase().includes(query));
    }, [visibleFolders, searchQuery]);

    const breadcrumbSegments = useMemo(() => {
        if (!currentFolder) return [];
        const parts = currentFolder.split("/");
        return parts.map((label, i) => ({label, path: parts.slice(0, i + 1).join("/")}));
    }, [currentFolder]);

    const isEmpty = filteredFiles.length === 0 && filteredFolders.length === 0;

    return (<div className="flex h-screen overflow-hidden bg-background text-on-surface">
        {/* Desktop sidebar */}
        <div className="fixed left-0 top-0 hidden h-screen md:flex">
            <Sidebar
                activeTab={activeTab}
                onTabSelect={handleTabChange}
                onUpload={() => navigate(`/upload?directory=${encodeURIComponent(currentFolder || "/home")}`)}
                onLogout={handleLogout}
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
                    onUpload={() => navigate(`/upload?directory=${encodeURIComponent(currentFolder || "/home")}`)}
                    onLogout={handleLogout}
                />
            </div>
            <div
                className="flex-1 bg-black/50"
                onClick={() => setSidebarOpen(false)}
                aria-hidden="true"
            />
        </div>)}

        <div className="flex h-screen flex-1 flex-col md:ml-72">
            {/* Header */}
            <header
                className="fixed top-0 right-0 z-40 flex h-16 w-full items-center justify-between border-b border-outline-variant/10 bg-surface/70 px-6 backdrop-blur-md md:w-[calc(100%-288px)]">
                <div className="flex w-full max-w-md items-center gap-4">
                    <button
                        type="button"
                        className="text-on-surface-variant transition-colors hover:text-primary md:hidden"
                        onClick={() => setSidebarOpen(true)}
                        aria-label="Open menu"
                    >
                            <span className="material-symbols-outlined" aria-hidden="true">
                                menu
                            </span>
                    </button>

                    <div className="relative hidden w-full sm:block">
                            <span
                                className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
                                style={{fontSize: 20}}
                                aria-hidden="true"
                            >
                                search
                            </span>
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(event) => setSearchQuery(event.target.value)}
                            placeholder={isTrash ? "Search trash…" : "Search files…"}
                            aria-label="Search files"
                            className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-low py-2 pl-10 pr-3 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/50"
                        />
                    </div>
                </div>

                <div className="flex items-center gap-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full border border-primary/30 bg-primary/20">
                            <span className="material-symbols-outlined text-[18px] text-primary" aria-hidden="true">
                                person
                            </span>
                    </div>
                </div>
            </header>

            {/* Main View Area */}
            <main className="flex-1 overflow-y-auto bg-gradient-to-br from-background to-surface-container-lowest px-4 pb-8 pt-24 md:px-8">
                <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
                    <div>
                        <h2 className="text-2xl font-semibold tracking-tight text-on-surface">
                            {isTrash ? "Trash Bin" : "Your Files"}
                        </h2>
                        {!isTrash && currentFolder && (<div className="mb-4 flex items-center gap-1 text-sm text-on-surface-variant">
                            <button onClick={() => setCurrentFolder("")} className="hover:text-primary hover:underline">
                                All Files
                            </button>
                            {breadcrumbSegments.map(({label, path}, i) => (<span key={path} className="flex items-center gap-1">
                                            <span className="text-on-surface-variant/40">/</span>
                                {i === breadcrumbSegments.length - 1 ? (<span className="font-medium text-on-surface">{label}</span>) : (
                                    <button onClick={() => openFolder(path)} className="hover:text-primary hover:underline">
                                        {label}
                                    </button>)}
                                        </span>))}
                        </div>)}
                    </div>

                    {!isTrash && (<div className="flex gap-2">
                        <button
                            type="button"
                            onClick={() => setShowFolderModal(true)}
                            className="flex items-center gap-1 rounded-xl border border-outline-variant/20 bg-surface-container-high px-4 py-2 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-highest"
                        >
                                    <span className="material-symbols-outlined text-[16px]" aria-hidden="true">
                                        create_new_folder
                                    </span>
                            New Folder
                        </button>

                        <button
                            type="button"
                            onClick={() => navigate(`/upload?directory=${encodeURIComponent(currentFolder || "/home")}`)}
                            className="flex items-center gap-1 rounded-xl bg-primary px-4 py-2 text-xs font-medium text-on-primary shadow-[0_0_15px_rgba(192,193,255,0.15)] transition-colors hover:bg-primary/90"
                        >
                                    <span className="material-symbols-outlined text-[16px]" aria-hidden="true">
                                        cloud_upload
                                    </span>
                            Upload
                        </button>
                    </div>)}
                </div>

                {!isTrash && currentFolder && (<button
                    type="button"
                    onClick={goBack}
                    className="mb-4 flex items-center gap-2 text-sm text-primary hover:underline"
                >
                    <span className="material-symbols-outlined" aria-hidden="true">arrow_back</span>
                    Back
                </button>)}

                {pageError && (<div
                    className="mb-6 flex items-center justify-between rounded-xl border border-error/30 bg-error-container/20 px-4 py-3 text-sm text-error">
                    <span>{pageError}</span>
                    <button type="button" onClick={fetchFiles} className="text-xs font-semibold underline">
                        Retry
                    </button>
                </div>)}

                {loadingFiles ? (<div className="flex flex-col items-center justify-center py-24 text-on-surface-variant">
                            <span className="material-symbols-outlined mb-4 animate-spin text-[48px]" aria-hidden="true">
                                progress_activity
                            </span>
                    Loading files…
                </div>) : (<>
                    {filteredFolders.length > 0 && (<div className="mb-8">
                        <h3 className="mb-4 flex items-center gap-2 font-semibold text-on-surface">
                                        <span className="material-symbols-outlined text-primary" aria-hidden="true">
                                            folder
                                        </span>
                            Folders
                        </h3>

                        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                            {filteredFolders.map(({path, id}) => {
                                const displayName = path.split("/").pop();
                                return (<div
                                    key={path}
                                    className="group relative rounded-2xl border border-outline-variant/10 bg-surface-container-lowest p-4 text-left transition-all hover:border-primary/30 hover:bg-surface-container-low"
                                >
                                    <button
                                        type="button"
                                        onClick={() => !isTrash && openFolder(path)}
                                        className={`block w-full text-left ${isTrash ? "cursor-default" : ""}`}
                                    >
                                                        <span className="material-symbols-outlined text-[42px] text-primary" aria-hidden="true">
                                                            folder
                                                        </span>
                                        <p className="mt-3 truncate text-sm font-medium text-on-surface">
                                            {displayName}
                                        </p>
                                    </button>
                                    <div
                                        className="absolute right-2 top-2 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                                        {isTrash ? (<>
                                            <button
                                                type="button"
                                                onClick={() => restoreFolder(id)}
                                                title="Restore Folder"
                                                className="rounded-lg p-1 text-on-surface-variant hover:bg-primary/10 hover:text-primary"
                                            >
                                                <span className="material-symbols-outlined text-[16px]">restore</span>
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => deleteFolder(id, path, true)}
                                                title="Delete Permanently"
                                                className="rounded-lg p-1 text-on-surface-variant hover:bg-error/10 hover:text-error"
                                            >
                                                <span className="material-symbols-outlined text-[16px]">delete_forever</span>
                                            </button>
                                        </>) : (<button
                                            type="button"
                                            onClick={() => deleteFolder(id, path, false)}
                                            disabled={deletingId === `folder-${id}`}
                                            title="Move to Trash"
                                            className="rounded-lg p-1 text-on-surface-variant hover:bg-error/10 hover:text-error"
                                        >
                                                                <span className="material-symbols-outlined text-[16px]">
                                                                    {deletingId === `folder-${id}` ? "progress_activity" : "delete"}
                                                                </span>
                                        </button>)}
                                    </div>
                                </div>);
                            })}
                        </div>
                    </div>)}

                    {isEmpty ? (<div className="flex flex-col items-center justify-center py-24 text-on-surface-variant">
                                    <span className="material-symbols-outlined mb-4 text-[64px] text-outline/40" aria-hidden="true">
                                        {isTrash ? "auto_delete" : "cloud_off"}
                                    </span>
                        <p className="text-lg font-medium text-on-surface">
                            {isTrash ? "Trash is empty" : searchQuery ? "No matching files" : "No files yet"}
                        </p>
                    </div>) : (filteredFiles.length > 0 && (<div>
                        <h3 className="mb-4 flex items-center gap-2 font-semibold text-on-surface">
                                            <span className="material-symbols-outlined text-primary/70" aria-hidden="true">
                                                description
                                            </span>
                            Files
                        </h3>

                        <div
                            className="glass-panel overflow-hidden rounded-2xl border border-outline-variant/10 bg-surface-container-lowest">
                            <div
                                className="grid grid-cols-12 gap-2 border-b border-outline-variant/10 bg-surface-container-low/50 p-3 px-4">
                                <div
                                    className="col-span-5 font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:col-span-4">
                                    Name
                                </div>
                                <div
                                    className="col-span-2 hidden font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:block">
                                    Size
                                </div>
                                <div
                                    className="col-span-2 hidden font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:block">
                                    Location
                                </div>
                                <div
                                    className="col-span-4 font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:col-span-2">
                                    Data Center
                                </div>
                                <div
                                    className="col-span-3 text-right font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:col-span-2">
                                    Actions
                                </div>
                            </div>

                            {filteredFiles.map((file, index) => {
                                const {icon, color} = getFileIcon(file.name);
                                const isLast = index === filteredFiles.length - 1;
                                const isDeleting = deletingId === file.id;

                                return (<div
                                    key={file.id}
                                    className={`group grid grid-cols-12 items-center gap-2 p-3 px-4 transition-colors hover:bg-surface-container-low/30 ${isLast ? "" : "border-b border-outline-variant/5"}`}
                                >
                                    {/* File Name & Mobile Metadata */}
                                    <div className="col-span-5 flex items-center gap-3 sm:col-span-4">
                                                            <span className={`material-symbols-outlined text-[20px] ${color}`} aria-hidden="true">
                                                                {icon}
                                                            </span>
                                        <div className="min-w-0">
                                            <p className="truncate text-sm text-on-surface transition-colors group-hover:text-primary">
                                                {file.name}
                                            </p>
                                            <p className="mt-0.5 text-xs text-on-surface-variant sm:hidden">
                                                {formatFileSize(file.size)} • {formatDate(file.modified_at)}
                                            </p>
                                        </div>
                                    </div>

                                    {/* Desktop File Size */}
                                    <div className="col-span-2 hidden font-mono text-xs text-on-surface-variant sm:block">
                                        {formatFileSize(file.size)}
                                    </div>

                                    {/* Location */}
                                    <div className="col-span-2 hidden truncate text-sm text-on-surface-variant sm:block">
                                        {file.directory || "Root"}
                                    </div>

                                    {/* Data Center */}
                                    <div className="col-span-4 sm:col-span-2">
                                                            <span
                                                                className="inline-block max-w-full truncate rounded-full bg-primary/10 px-2 py-0.5 font-geist text-xs text-primary">
                                                                {file.data_center || "—"}
                                                            </span>
                                    </div>

                                    {/* Actions */}
                                    <div
                                        className="col-span-3 flex justify-end gap-1 opacity-100 transition-opacity sm:col-span-2 sm:opacity-0 sm:group-hover:opacity-100">
                                        {isTrash ? (<>
                                            <button
                                                type="button"
                                                onClick={() => restoreItem(file.id)}
                                                title="Restore File"
                                                className="rounded-lg p-1.5 text-on-surface-variant hover:bg-primary/10 hover:text-primary"
                                            >
                                                <span className="material-symbols-outlined text-[18px]">restore</span>
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => deleteFile(file.id, file.name, true)}
                                                title="Delete Permanently"
                                                className="rounded-lg p-1.5 text-on-surface-variant hover:bg-error/10 hover:text-error"
                                            >
                                                <span className="material-symbols-outlined text-[18px]">delete_forever</span>
                                            </button>
                                        </>) : (<>
                                            <button
                                                type="button"
                                                onClick={() => startStreaming(file)}
                                                title="Stream / View file"
                                                className="rounded-lg p-1.5 text-on-surface-variant transition-colors hover:bg-primary/10 hover:text-primary"
                                            >
                                                                        <span className="material-symbols-outlined text-[18px]">
                                                                            play_circle
                                                                        </span>
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => downloadFile(file.id)}
                                                title="Download"
                                                className="rounded-lg p-1.5 text-on-surface-variant transition-colors hover:bg-primary/10 hover:text-primary"
                                            >
                                                                        <span className="material-symbols-outlined text-[18px]">
                                                                            download
                                                                        </span>
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => deleteFile(file.id, file.name, false)}
                                                disabled={isDeleting}
                                                title="Move to Trash"
                                                className="rounded-lg p-1.5 text-on-surface-variant transition-colors hover:bg-error/10 hover:text-error disabled:opacity-50"
                                            >
                                                                        <span className="material-symbols-outlined text-[18px]">
                                                                            {isDeleting ? "progress_activity" : "delete"}
                                                                        </span>
                                            </button>
                                        </>)}
                                    </div>
                                </div>);
                            })}
                        </div>
                    </div>))}
                </>)}
            </main>
        </div>

        {streamData && (<StreamModal
            file={streamData.file}
            streamUrl={streamData.url}
            onClose={() => setStreamData(null)}
        />)}

        {showFolderModal && (<CreateFolderModal
            targetPath={currentFolder}
            onClose={() => setShowFolderModal(false)}
            onCreate={createFolder}
        />)}
    </div>);
}
