import {useCallback, useEffect, useMemo, useRef, useState} from "react";

const API_URL = window.STORELIMITLESS_CONFIG?.API_URL ?? import.meta.env.VITE_API_URL;
const HOME_PATH = "/home";

const panelButton = "inline-flex h-9 items-center justify-center gap-2 rounded-lg border px-3 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const buttonSecondary = `${panelButton} border-[#363636] bg-[#242424] text-[#e5e5e5] hover:border-[#4d4d4d] hover:bg-[#2b2b2b]`;
const buttonPrimary = `${panelButton} border-[#3ecf8e]/40 bg-[#006239] text-white hover:border-[#3ecf8e]/70 hover:bg-[#007a46]`;

/**
 * Normalize API and route paths into the application's canonical /home path form.
 * @param {string | null | undefined} value
 * @returns {string}
 */
function normalizeDirectoryPath(value) {
    let path = String(value ?? "").trim().replace(/\\/g, "/").replace(/\/{2,}/g, "/");
    path = path.replace(/\/$/, "");

    if (!path || path === "/" || path === "home" || path === HOME_PATH) {
        return HOME_PATH;
    }

    if (path.startsWith("home/")) {
        return `/${path}`;
    }

    if (!path.startsWith("/")) {
        return `${HOME_PATH}/${path}`;
    }

    return path;
}

/**
 * Get the parent path. The top-level directory is its own parent.
 * @param {string} path
 * @returns {string}
 */
function parentDirectoryPath(path) {
    if (path === HOME_PATH) {
        return HOME_PATH;
    }

    const separator = path.lastIndexOf("/");
    return separator <= 0 ? HOME_PATH : path.slice(0, separator) || HOME_PATH;
}

/**
 * Get the last directory name in a path.
 * @param {string} path
 * @returns {string}
 */
function directoryName(path) {
    return path === HOME_PATH ? "home" : path.split("/").filter(Boolean).pop() || "home";
}

/**
 * @typedef {{
 *   key: string,
 *   type: "file" | "folder",
 *   id: string | number,
 *   name: string,
 *   path?: string,
 *   directory?: string
 * }} MoveItem
 */

/**
 * A folder picker that fetches each directory's direct children from the API as it is browsed.
 * @param {{
 *   items: MoveItem[],
 *   currentFolder: string,
 *   canMoveTo: (items: MoveItem[], destination: string) => boolean,
 *   onClose: () => void,
 *   onMove: (items: MoveItem[], destination: string) => Promise<boolean | {
 *     ok?: boolean,
 *     partial?: boolean,
 *     failed?: Array<{type: string, id: string | number, error?: string}>,
 *     message?: string
 *   } | void>
 * }} props
 */
export default function MoveModal({
                                      items, currentFolder, canMoveTo, onClose, onMove,
                                  }) {
    const initialPath = normalizeDirectoryPath(currentFolder || HOME_PATH);

    const [browsePath, setBrowsePath] = useState(initialPath);
    const [selectedPath, setSelectedPath] = useState(initialPath);
    const [pendingItems, setPendingItems] = useState(items);
    const [childrenByPath, setChildrenByPath] = useState({});
    const [loadingPaths, setLoadingPaths] = useState(() => new Set());
    const [loadErrors, setLoadErrors] = useState({});
    const [moving, setMoving] = useState(false);
    const [error, setError] = useState("");

    const inFlightRef = useRef(new Map());
    const childrenByPathRef = useRef({});

    // Keep the ref in sync so concurrent folder requests cannot overwrite each other's cache updates.
    childrenByPathRef.current = childrenByPath;

    /**
     * Fetch immediate child folders for a path.
     * @param {string} requestedPath
     * @param {{force?: boolean}} [options]
     * @returns {Promise<Array<{id?: string | number, path: string, is_deleted?: boolean}>>}
     */
    const loadChildren = useCallback(async (requestedPath, {force = false} = {}) => {
        const path = normalizeDirectoryPath(requestedPath);

        if (!force && Object.prototype.hasOwnProperty.call(childrenByPathRef.current, path,)) {
            return childrenByPathRef.current[path];
        }

        if (!force && inFlightRef.current.has(path)) {
            return inFlightRef.current.get(path);
        }

        const request = (async () => {
            setLoadingPaths((previous) => new Set(previous).add(path));
            setLoadErrors((previous) => ({...previous, [path]: ""}));

            try {
                const token = localStorage.getItem("token");

                if (!token) {
                    throw new Error("Your session has expired. Sign in again before moving files.",);
                }

                if (!API_URL) {
                    throw new Error("The API URL is not configured.");
                }

                const response = await fetch(`${API_URL}/auth/directory?directory=${encodeURIComponent(path)}`, {
                    headers: {
                        Authorization: `Bearer ${token}`,
                    }, cache: "no-store",
                },);

                let data;

                try {
                    data = await response.json();
                } catch {
                    data = null;
                }

                if (!response.ok) {
                    throw new Error(data?.detail || data?.message || `Could not load folders (${response.status}).`,);
                }

                // /auth/directory returns [directories, files].
                // Also tolerate object-shaped responses.
                const records = Array.isArray(data) ? data[0] : data?.directories ?? data?.folders ?? data?.children;

                if (!Array.isArray(records)) {
                    throw new Error("The server returned an unexpected folder listing.",);
                }

                const seen = new Set();

                const directChildren = records
                    .flatMap((folder) => {
                        if (!folder || typeof folder.path !== "string" || folder.is_deleted) {
                            return [];
                        }

                        const childPath = normalizeDirectoryPath(folder.path,);

                        if (childPath === HOME_PATH || !childPath.startsWith(`${HOME_PATH}/`)) {
                            return [];
                        }

                        if (parentDirectoryPath(childPath) !== path || seen.has(childPath)) {
                            return [];
                        }

                        seen.add(childPath);

                        return [{
                            ...folder, path: childPath, is_deleted: false,
                        },];
                    })
                    .sort((left, right) => directoryName(left.path).localeCompare(directoryName(right.path), undefined, {sensitivity: "base"},),);

                const nextCache = {
                    ...childrenByPathRef.current, [path]: directChildren,
                };

                childrenByPathRef.current = nextCache;
                setChildrenByPath(nextCache);

                return directChildren;
            } catch (loadError) {
                const message = loadError instanceof Error ? loadError.message : "Failed to load folders.";

                setLoadErrors((previous) => ({
                    ...previous, [path]: message,
                }));

                throw loadError;
            } finally {
                setLoadingPaths((previous) => {
                    const next = new Set(previous);
                    next.delete(path);
                    return next;
                });

                inFlightRef.current.delete(path);
            }
        })();

        inFlightRef.current.set(path, request);

        return request;
    }, [],);

    // Load home and the currently displayed folder.
    useEffect(() => {
        void loadChildren(HOME_PATH).catch(() => {
        });

        if (initialPath !== HOME_PATH) {
            void loadChildren(initialPath).catch(() => {
            });
        }
    }, [initialPath, loadChildren]);

    const childFolders = useMemo(() => childrenByPath[browsePath] || [], [childrenByPath, browsePath],);

    const pathSegments = useMemo(() => {
        const parts = browsePath.split("/").filter(Boolean).slice(1);
        const segments = [{label: "home", path: HOME_PATH}];

        let path = HOME_PATH;

        for (const part of parts) {
            path += `/${part}`;
            segments.push({label: part, path});
        }

        return segments;
    }, [browsePath]);

    const selectedIsValid = pendingItems.length > 0 && canMoveTo(pendingItems, selectedPath);

    const currentFolderIsLoading = loadingPaths.has(browsePath);
    const currentFolderError = loadErrors[browsePath];

    useEffect(() => {
        const onKeyDown = (event) => {
            if (event.key === "Escape" && !moving) {
                event.preventDefault();
                event.stopPropagation();
                onClose();
            }
        };

        document.addEventListener("keydown", onKeyDown);

        return () => {
            document.removeEventListener("keydown", onKeyDown);
        };
    }, [moving, onClose]);

    /**
     * Open a folder for browsing and load its children if they are not already cached.
     * @param {string} path
     * @returns {Promise<void>}
     */
    const openFolder = async (path) => {
        const normalized = normalizeDirectoryPath(path);

        setBrowsePath(normalized);
        setError("");

        try {
            await loadChildren(normalized);
        } catch {
            // The error is shown in the current folder list, with a Retry action.
        }
    };

    /**
     * Attempt the move; when the server reports partial success,
     * keep only failed items in the dialog.
     * @returns {Promise<void>}
     */
    const move = async () => {
        if (moving || !selectedIsValid || !pendingItems.length) {
            return;
        }

        setMoving(true);
        setError("");

        try {
            const result = await onMove(pendingItems, selectedPath);

            if (result === false || (result && typeof result === "object" && result.ok === false)) {
                if (result && typeof result === "object" && result.partial && Array.isArray(result.failed)) {
                    const failedKeys = new Set(result.failed.map((failure) => `${failure.type}:${String(failure.id)}`,),);

                    const failedItems = pendingItems.filter((item) => failedKeys.has(`${item.type}:${String(item.id)}`),);

                    if (failedItems.length) {
                        setPendingItems(failedItems);
                    }
                }

                setError(result && typeof result === "object" && result.message ? result.message : "The move did not complete. Choose a valid destination and try again.",);

                setMoving(false);
                return;
            }

            onClose();
        } catch (moveError) {
            setError(moveError instanceof Error ? moveError.message : "Failed to move items.",);

            setMoving(false);
        }
    };

    const parentPath = parentDirectoryPath(browsePath);

    const destinationLabel = selectedPath === HOME_PATH ? "home" : selectedPath.split("/").filter(Boolean).join(" / ");

    return (<div
        className="fixed inset-0 z-[240] flex items-center justify-center bg-black/65 px-4 py-6 backdrop-blur-sm"
        onMouseDown={(event) => {
            if (event.target === event.currentTarget && !moving) {
                onClose();
            }
        }}
    >
        <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="move-items-title"
            className="w-full max-w-[520px] overflow-hidden rounded-xl border border-[#343434] bg-[#1a1a1a] text-[#fafafa] shadow-2xl"
        >
            <header className="flex items-start justify-between gap-4 border-b border-[#2e2e2e] px-5 py-4">
                <div className="flex min-w-0 items-start gap-3">
                    <div
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#3ecf8e]/20 bg-[#3ecf8e]/10 text-[#3ecf8e]">
                            <span
                                className="material-symbols-outlined text-[20px]"
                                aria-hidden="true"
                            >
                                drive_file_move
                            </span>
                    </div>

                    <div className="min-w-0">
                        <h2
                            id="move-items-title"
                            className="text-sm font-medium"
                        >
                            Move to folder
                        </h2>

                        <p className="mt-1 text-xs leading-5 text-[#898989]">
                            Browse through folders and choose the
                            destination for {pendingItems.length} item
                            {pendingItems.length === 1 ? "" : "s"}.
                        </p>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={onClose}
                    disabled={moving}
                    aria-label="Close move dialog"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[#898989] hover:bg-[#2a2a2a] hover:text-white disabled:opacity-40"
                >
                        <span
                            className="material-symbols-outlined text-[19px]"
                            aria-hidden="true"
                        >
                            close
                        </span>
                </button>
            </header>

            <div className="px-5 pt-4">
                <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
                    {pathSegments.map((segment, index) => (<span
                        key={segment.path}
                        className="inline-flex items-center gap-1.5"
                    >
                                {index > 0 && (<span
                                    className="material-symbols-outlined text-[14px] text-[#5d5d5d]"
                                    aria-hidden="true"
                                >
                                        chevron_right
                                    </span>)}

                        <button
                            type="button"
                            disabled={moving}
                            onClick={() => void openFolder(segment.path)}
                            className={`rounded px-1.5 py-1 transition-colors ${index === pathSegments.length - 1 ? "bg-[#2a2a2a] text-[#fafafa]" : "text-[#898989] hover:bg-[#242424] hover:text-white"}`}
                        >
                                    {segment.label}
                                </button>
                            </span>))}
                </div>

                <div className="overflow-hidden rounded-lg border border-[#303030] bg-[#151515]">
                    {browsePath !== HOME_PATH && (<button
                        type="button"
                        disabled={moving}
                        onClick={() => void openFolder(parentPath)}
                        className="flex h-10 w-full items-center gap-2 border-b border-[#2a2a2a] px-3 text-left text-xs text-[#b4b4b4] hover:bg-[#222222] disabled:opacity-40"
                    >
                                <span
                                    className="material-symbols-outlined text-[17px]"
                                    aria-hidden="true"
                                >
                                    drive_folder_upload
                                </span>
                        <span>Parent folder</span>
                    </button>)}

                    <div className="max-h-60 overflow-y-auto p-1.5">
                        {currentFolderIsLoading ? (<div className="flex items-center justify-center gap-2 px-3 py-8 text-xs text-[#898989]">
                                    <span
                                        className="material-symbols-outlined animate-spin text-[16px]"
                                        aria-hidden="true"
                                    >
                                        progress_activity
                                    </span>
                            Loading folders…
                        </div>) : currentFolderError ? (<div className="px-3 py-6 text-center">
                            <p
                                role="alert"
                                className="text-xs leading-5 text-[#ff8589]"
                            >
                                {currentFolderError}
                            </p>

                            <button
                                type="button"
                                disabled={moving}
                                onClick={() => void loadChildren(browsePath, {
                                    force: true,
                                }).catch(() => {
                                })}
                                className="mt-3 rounded-md border border-[#363636] px-3 py-1.5 text-xs text-[#e5e5e5] hover:bg-[#242424] disabled:opacity-40"
                            >
                                Retry
                            </button>
                        </div>) : childFolders.length === 0 ? (<p className="px-3 py-8 text-center text-xs text-[#707070]">
                            No subfolders in this location.
                        </p>) : (childFolders.map((folder) => {
                            const name = directoryName(folder.path);
                            const isSelected = selectedPath === folder.path;

                            return (<div
                                key={folder.path}
                                className={`flex items-center gap-2 rounded-md px-2 py-1.5 ${isSelected ? "bg-[#3ecf8e]/[0.08]" : "hover:bg-[#222222]"}`}
                            >
                                <button
                                    type="button"
                                    disabled={moving}
                                    onClick={() => void openFolder(folder.path)}
                                    className="flex min-w-0 flex-1 items-center gap-2.5 rounded px-1 py-1 text-left disabled:opacity-40"
                                    title={`Browse inside ${name}`}
                                    aria-label={`Open folder ${name}`}
                                >
                                                <span
                                                    className="material-symbols-outlined text-[19px] text-[#3ecf8e]"
                                                    aria-hidden="true"
                                                >
                                                    folder
                                                </span>

                                    <span className="truncate text-sm text-[#e5e5e5]">
                                                    {name}
                                                </span>

                                    <span
                                        className="material-symbols-outlined ml-auto text-[16px] text-[#606060]"
                                        aria-hidden="true"
                                    >
                                                    chevron_right
                                                </span>
                                </button>

                                <button
                                    type="button"
                                    disabled={moving}
                                    onClick={() => setSelectedPath((previousPath) => previousPath === folder.path ? browsePath : folder.path,)}
                                    aria-pressed={isSelected}
                                    className={`shrink-0 rounded-md border px-2 py-1 text-[11px] transition-colors disabled:opacity-40 ${isSelected ? "border-[#3ecf8e]/40 bg-[#3ecf8e]/10 text-[#3ecf8e]" : "border-[#363636] text-[#898989] hover:border-[#4d4d4d] hover:text-white"}`}
                                >
                                    {isSelected ? "Deselect" : "Choose"}
                                </button>
                            </div>);
                        }))}
                    </div>
                </div>

                <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-[#303030] bg-[#202020] px-3 py-2.5">
                    <div className="min-w-0">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#707070]">
                            Destination
                        </p>

                        <p
                            className="mt-1 truncate text-xs text-[#fafafa]"
                            title={selectedPath}
                        >
                            {destinationLabel}
                        </p>
                    </div>

                    <button
                        type="button"
                        disabled={moving || !canMoveTo(pendingItems, browsePath)}
                        onClick={() => setSelectedPath(browsePath)}
                        className="shrink-0 rounded-md border border-[#363636] bg-[#242424] px-2.5 py-1.5 text-[11px] text-[#b4b4b4] hover:bg-[#2c2c2c] disabled:opacity-40"
                    >
                        Use this folder
                    </button>
                </div>

                {!selectedIsValid && (<p className="mt-2 text-xs leading-5 text-[#d9a441]">
                    This destination is the current location or is
                    inside a folder being moved. Choose a different
                    folder.
                </p>)}

                {error && (<p
                    role="alert"
                    className="mt-2 text-xs leading-5 text-[#ff6369]"
                >
                    {error}
                </p>)}
            </div>

            <footer className="mt-4 flex justify-end gap-2 border-t border-[#2e2e2e] px-5 py-4">
                <button
                    type="button"
                    onClick={onClose}
                    disabled={moving}
                    className={buttonSecondary}
                >
                    Cancel
                </button>

                <button
                    type="button"
                    onClick={() => void move()}
                    disabled={moving || !selectedIsValid || pendingItems.length === 0}
                    className={buttonPrimary}
                >
                        <span
                            className="material-symbols-outlined text-[16px]"
                            aria-hidden="true"
                        >
                            drive_file_move
                        </span>

                    {moving ? "Moving…" : `Move ${pendingItems.length} item${pendingItems.length === 1 ? "" : "s"}`}
                </button>
            </footer>
        </section>
    </div>);
}
