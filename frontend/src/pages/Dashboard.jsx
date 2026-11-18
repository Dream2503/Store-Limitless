import {useCallback, useEffect, useMemo, useState} from "react";
import {useNavigate} from "react-router-dom";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const NAV_ITEMS = [{icon: "folder_open", label: "All Files", enabled: true}, {icon: "schedule", label: "Recent", enabled: false}, {
    icon: "group", label: "Shared", enabled: false
}, {icon: "delete", label: "Trash", enabled: false},];

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

function getToken() {
    return localStorage.getItem("token");
}

/* -------------------------------------------------------------------- */
/*  Sidebar                                                              */

/* -------------------------------------------------------------------- */

function Sidebar({onUpload, onGoHome, onLogout}) {
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
            {NAV_ITEMS.map(({icon, label, enabled}) => (<button
                key={label}
                type="button"
                disabled={!enabled}
                onClick={enabled ? onGoHome : undefined}
                title={enabled ? undefined : "Coming soon"}
                className={`flex items-center gap-3 rounded-xl px-3 py-2 text-left text-sm transition-all ${label === "All Files" ? "bg-primary-container/30 font-semibold text-primary" : enabled ? "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface" : "cursor-not-allowed text-on-surface-variant/40"}`}
            >
            <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
              {icon}
            </span>
                {label}
            </button>))}
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
/*  Create-folder modal                                                  */

/* -------------------------------------------------------------------- */

function CreateFolderModal({nestedDisabled, onClose, onCreate}) {
    const [name, setName] = useState("");
    const [error, setError] = useState("");
    const [creating, setCreating] = useState(false);

    useEffect(() => {
        const onKeyDown = (event) => {
            if (event.key === "Escape" && !creating) onClose();
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
            onClick={(event) => event.stopPropagation()}
        >
            <div className="mb-5 flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-semibold text-on-surface">Create New Folder</h2>
                    <p className="mt-1 text-sm text-on-surface-variant">Enter a name for your folder.</p>
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    disabled={creating}
                    aria-label="Close"
                    className="text-on-surface-variant hover:text-on-surface disabled:opacity-50"
                >
            <span className="material-symbols-outlined" aria-hidden="true">
              close
            </span>
                </button>
            </div>

            {nestedDisabled && (<p className="mb-4 rounded-lg bg-surface-container-high px-3 py-2 text-xs text-on-surface-variant">
                Nested folders aren't supported yet — this folder will be created at the top level.
            </p>)}

            <form onSubmit={handleSubmit}>
                <input
                    type="text"
                    value={name}
                    onChange={(event) => {
                        setName(event.target.value);
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
/*  Dashboard page                                                       */
/* -------------------------------------------------------------------- */

export default function DashboardPage() {
    const navigate = useNavigate();

    const [files, setFiles] = useState([]);
    const [folders, setFolders] = useState([]);
    const [currentFolder, setCurrentFolder] = useState("");
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const [loadingFiles, setLoadingFiles] = useState(true);
    const [pageError, setPageError] = useState("");
    const [showFolderModal, setShowFolderModal] = useState(false);
    const [deletingId, setDeletingId] = useState(null);

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
            const response = await fetch(`${API_URL}/auth/files`, {
                headers: {Authorization: `Bearer ${token}`},
            });

            if (response.status === 401) {
                localStorage.removeItem("token");
                navigate("/");
                return;
            }

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Failed to fetch files");
            }

            const allFiles = Array.isArray(data) ? data : [];
            setFiles(allFiles);

            // Folders are represented as placeholder records: name === ".__folder__"
            const uniqueFolders = [...new Set(allFiles
                .filter((file) => file.name === ".__folder__")
                .map((file) => file.directory)
                .filter(Boolean)),];
            setFolders(uniqueFolders);
        } catch (error) {
            console.error("Failed to fetch files:", error);
            setPageError(error.message || "Failed to load files.");
        } finally {
            setLoadingFiles(false);
        }
    }, [navigate]);

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

    /* ----------------------------- download ------------------------------ */

    const downloadFile = async (fileId, filename) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        try {
            const response = await fetch(`${API_URL}/auth/download/${fileId}`, {
                headers: {Authorization: `Bearer ${token}`},
            });

            if (!response.ok) {
                let message = "Failed to download file.";
                try {
                    const data = await response.json();
                    message = data.detail || message;
                } catch {
                    // response wasn't JSON — keep the default message
                }
                throw new Error(message);
            }

            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = filename;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            window.URL.revokeObjectURL(url);
        } catch (error) {
            console.error("Download failed:", error);
            alert(error.message || "Failed to download file.");
        }
    };

    /* ------------------------------ delete -------------------------------- */
    // NOTE: assumes a DELETE /auth/files/{fileId} endpoint, mirroring the
    // /auth/download/{fileId} pattern. Update the URL below if your backend
    // uses a different route.

    const deleteFile = async (fileId, filename) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        if (!window.confirm(`Delete "${filename}"? This can't be undone.`)) {
            return;
        }

        setDeletingId(fileId);

        try {
            const response = await fetch(`${API_URL}/auth/files/${fileId}`, {
                method: "DELETE", headers: {Authorization: `Bearer ${token}`},
            });

            if (!response.ok) {
                let message = "Failed to delete file.";
                try {
                    const data = await response.json();
                    message = data.detail || message;
                } catch {
                    // response wasn't JSON — keep the default message
                }
                throw new Error(message);
            }

            setFiles((prev) => prev.filter((file) => file.id !== fileId));
        } catch (error) {
            console.error("Delete failed:", error);
            alert(error.message || "Failed to delete file.");
        } finally {
            setDeletingId(null);
        }
    };

    /* ------------------------------ folders -------------------------------- */

    const createFolder = async (cleanName) => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        // The backend rejects "/" in folder names, so nested folders aren't
        // possible yet — new folders always land at the top level.
        const folderPath = cleanName;

        const response = await fetch(`${API_URL}/auth/create-folder`, {
            method: "POST", headers: {
                "Content-Type": "application/json", Authorization: `Bearer ${token}`,
            }, body: JSON.stringify({directory: folderPath}),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Failed to create folder.");
        }

        await fetchFiles();
        setShowFolderModal(false);
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

    /* ------------------------------ derived data ---------------------------- */

    // Direct-child folders of the current directory.
    const visibleFolders = useMemo(() => {
        return folders.filter((folderPath) => {
            if (!currentFolder) return !folderPath.includes("/");
            if (!folderPath.startsWith(`${currentFolder}/`)) return false;
            const remainder = folderPath.slice(currentFolder.length + 1);
            return remainder && !remainder.includes("/");
        });
    }, [folders, currentFolder]);

    const visibleFiles = useMemo(() => {
        return files.filter((file) => file.name !== ".__folder__" && (file.directory || "") === currentFolder);
    }, [files, currentFolder]);

    const filteredFiles = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) return visibleFiles;
        return visibleFiles.filter((file) => file.name?.toLowerCase().includes(query));
    }, [visibleFiles, searchQuery]);

    const filteredFolders = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) return visibleFolders;
        return visibleFolders.filter((folderPath) => folderPath.split("/").pop()?.toLowerCase().includes(query));
    }, [visibleFolders, searchQuery]);

    const isEmpty = filteredFiles.length === 0 && filteredFolders.length === 0;

    /* --------------------------------- render --------------------------------- */

    return (<div className="flex h-screen overflow-hidden bg-background text-on-surface">
        {/* Desktop sidebar */}
        <div className="fixed left-0 top-0 hidden h-screen md:flex">
            <Sidebar
                onUpload={() => navigate("/upload")}
                onGoHome={() => setCurrentFolder("")}
                onLogout={handleLogout}
            />
        </div>

        {/* Mobile sidebar */}
        {sidebarOpen && (<div className="fixed inset-0 z-50 flex md:hidden">
            <div className="flex-shrink-0">
                <Sidebar
                    onUpload={() => navigate("/upload")}
                    onGoHome={() => {
                        setCurrentFolder("");
                        setSidebarOpen(false);
                    }}
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
            {/* Top bar */}
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
                            placeholder="Search files…"
                            aria-label="Search files"
                            className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-low py-2 pl-10 pr-3 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/50"
                        />
                    </div>
                </div>

                <div className="flex items-center gap-3">
                    <div className="mr-3 hidden items-center gap-2 border-r border-outline-variant/20 pr-4 lg:flex">
              <span className="material-symbols-outlined text-primary" aria-hidden="true">
                cloud_done
              </span>
                        <span className="font-geist text-xs text-on-surface-variant">
                Unlimited storage
              </span>
                    </div>

                    <button
                        type="button"
                        className="rounded-full p-2 text-on-surface-variant transition-colors hover:text-primary"
                        aria-label="Notifications"
                    >
              <span className="material-symbols-outlined" aria-hidden="true">
                notifications
              </span>
                    </button>

                    <div className="flex h-8 w-8 items-center justify-center rounded-full border border-primary/30 bg-primary/20">
              <span className="material-symbols-outlined text-[18px] text-primary" aria-hidden="true">
                person
              </span>
                    </div>
                </div>
            </header>

            {/* Main content */}
            <main className="flex-1 overflow-y-auto bg-gradient-to-br from-background to-surface-container-lowest px-4 pb-8 pt-24 md:px-8">
                <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
                    <div>
                        <h2 className="text-2xl font-semibold tracking-tight text-on-surface">
                            Your Files
                        </h2>
                        {currentFolder && (<p className="mt-1 text-sm text-on-surface-variant">{currentFolder}</p>)}
                    </div>

                    <div className="flex gap-2">
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
                            onClick={() => navigate("/upload")}
                            className="flex items-center gap-1 rounded-xl bg-primary px-4 py-2 text-xs font-medium text-on-primary shadow-[0_0_15px_rgba(192,193,255,0.15)] transition-colors hover:bg-primary/90"
                        >
                <span className="material-symbols-outlined text-[16px]" aria-hidden="true">
                  cloud_upload
                </span>
                            Upload
                        </button>
                    </div>
                </div>

                {currentFolder && (<button
                    type="button"
                    onClick={goBack}
                    className="mb-4 flex items-center gap-2 text-sm text-primary hover:underline"
                >
              <span className="material-symbols-outlined" aria-hidden="true">
                arrow_back
              </span>
                    Back
                </button>)}

                <div
                    className="glass-panel relative mb-8 flex flex-col items-center justify-between gap-4 overflow-hidden rounded-2xl border border-outline-variant/10 p-4 sm:flex-row">
                    <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-primary/5 to-transparent"/>
                    <div className="relative z-10 flex items-center gap-4">
                        <div
                            className="flex h-12 w-12 items-center justify-center rounded-full border border-outline-variant/20 bg-surface-container">
                <span className="material-symbols-outlined text-[24px] text-primary" aria-hidden="true">
                  cloud
                </span>
                        </div>
                        <div>
                            <h3 className="font-semibold text-on-surface">Storage Status</h3>
                            <p className="text-sm text-on-surface-variant">Unlimited</p>
                        </div>
                    </div>
                    <span className="relative z-10 text-sm font-medium text-primary">
              {files.length} item{files.length !== 1 ? "s" : ""}
            </span>
                </div>

                {pageError && (<div
                    className="mb-6 flex items-center justify-between rounded-xl border border-error/30 bg-error-container/20 px-4 py-3 text-sm text-error">
                    <span>{pageError}</span>
                    <button type="button" onClick={fetchFiles} className="text-xs font-semibold underline">
                        Retry
                    </button>
                </div>)}

                {loadingFiles ? (<div className="flex flex-col items-center justify-center py-24 text-on-surface-variant">
              <span
                  className="material-symbols-outlined mb-4 animate-spin text-[48px]"
                  aria-hidden="true"
              >
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
                            {filteredFolders.map((folderPath) => {
                                const displayName = folderPath.split("/").pop();
                                return (<button
                                    key={folderPath}
                                    type="button"
                                    onClick={() => openFolder(folderPath)}
                                    className="rounded-2xl border border-outline-variant/10 bg-surface-container-lowest p-4 text-left transition-all hover:border-primary/30 hover:bg-surface-container-low"
                                >
                          <span className="material-symbols-outlined text-[42px] text-primary" aria-hidden="true">
                            folder
                          </span>
                                    <p className="mt-3 truncate text-sm font-medium text-on-surface">
                                        {displayName}
                                    </p>
                                </button>);
                            })}
                        </div>
                    </div>)}

                    {isEmpty ? (<div className="flex flex-col items-center justify-center py-24 text-on-surface-variant">
                  <span
                      className="material-symbols-outlined mb-4 text-[64px] text-outline/40"
                      aria-hidden="true"
                  >
                    cloud_off
                  </span>
                        <p className="text-lg font-medium text-on-surface">
                            {searchQuery ? "No matching files found" : "No files yet"}
                        </p>
                        <p className="mt-1 text-sm">
                            {searchQuery ? "Try a different search." : "Create a folder or upload your first file."}
                        </p>

                        {!searchQuery && (<div className="mt-6 flex gap-3">
                            <button
                                type="button"
                                onClick={() => setShowFolderModal(true)}
                                className="rounded-xl bg-surface-container-high px-5 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-highest"
                            >
                                New Folder
                            </button>
                            <button
                                type="button"
                                onClick={() => navigate(`/upload?directory=${encodeURIComponent(currentFolder)}`)}
                                className="rounded-xl bg-primary px-5 py-2 text-sm font-medium text-on-primary transition-colors hover:bg-primary/90"
                            >
                                Upload File
                            </button>
                        </div>)}
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
                                    className="col-span-6 font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:col-span-5">
                                    Name
                                </div>
                                <div
                                    className="col-span-3 hidden font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:block">
                                    Location
                                </div>
                                <div
                                    className="col-span-3 font-geist text-xs uppercase tracking-widest text-on-surface-variant sm:col-span-2">
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
                                    <div className="col-span-6 flex items-center gap-3 sm:col-span-5">
                              <span className={`material-symbols-outlined text-[20px] ${color}`} aria-hidden="true">
                                {icon}
                              </span>
                                        <div className="min-w-0">
                                            <p className="truncate text-sm text-on-surface transition-colors group-hover:text-primary">
                                                {file.name}
                                            </p>
                                            <p className="mt-0.5 text-xs text-on-surface-variant sm:hidden">
                                                {formatDate(file.modified_at)}
                                            </p>
                                        </div>
                                    </div>

                                    <div className="col-span-3 hidden truncate text-sm text-on-surface-variant sm:block">
                                        {file.directory || "Root"}
                                    </div>

                                    <div className="col-span-3 sm:col-span-2">
                              <span
                                  className="inline-block max-w-full truncate rounded-full bg-primary/10 px-2 py-0.5 font-geist text-xs text-primary">
                                {file.data_center || "—"}
                              </span>
                                    </div>

                                    <div
                                        className="col-span-3 flex justify-end gap-1 opacity-100 transition-opacity sm:col-span-2 sm:opacity-0 sm:group-hover:opacity-100">
                                        <button
                                            type="button"
                                            onClick={() => downloadFile(file.id, file.name)}
                                            title="Download"
                                            aria-label={`Download ${file.name}`}
                                            className="rounded-lg p-1.5 text-on-surface-variant transition-colors hover:bg-primary/10 hover:text-primary"
                                        >
                                <span className="material-symbols-outlined text-[18px]" aria-hidden="true">
                                  download
                                </span>
                                        </button>

                                        <button
                                            type="button"
                                            onClick={() => deleteFile(file.id, file.name)}
                                            disabled={isDeleting}
                                            title="Delete"
                                            aria-label={`Delete ${file.name}`}
                                            className="rounded-lg p-1.5 text-on-surface-variant transition-colors hover:bg-error/10 hover:text-error disabled:opacity-50"
                                        >
                                <span className="material-symbols-outlined text-[18px]" aria-hidden="true">
                                  {isDeleting ? "progress_activity" : "delete"}
                                </span>
                                        </button>
                                    </div>
                                </div>);
                            })}
                        </div>
                    </div>))}
                </>)}
            </main>
        </div>

        {showFolderModal && (<CreateFolderModal
            nestedDisabled={Boolean(currentFolder)}
            onClose={() => setShowFolderModal(false)}
            onCreate={createFolder}
        />)}
    </div>);
}