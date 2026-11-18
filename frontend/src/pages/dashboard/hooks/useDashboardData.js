import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {API_URL} from "../constants.js";
import {getToken} from "../utils.js";

const HOME_PATH = "/home";
const DIRECTORY_BATCH_SIZE = 8;

// Keep the complete tree in memory across SPA route changes.
// A browser reload clears this cache.
let inMemoryTreeCache = null;

function readTreeCache(token) {
    return token && inMemoryTreeCache?.token === token ? inMemoryTreeCache : null;
}

export function clearDashboardTreeCache(token) {
    if (!token || inMemoryTreeCache?.token === token) {
        inMemoryTreeCache = null;
    }
}

function normalizeDirectoryPath(value) {
    let path = String(value ?? "")
        .trim()
        .replace(/\\/g, "/")
        .replace(/\/{2,}/g, "/");

    path = path.replace(/\/$/, "");

    if (!path || path === "/" || path === "home" || path === HOME_PATH) {
        return HOME_PATH;
    }

    if (path.startsWith("home/")) {
        return `/${path}`;
    }

    return path.startsWith("/") ? path : `${HOME_PATH}/${path}`;
}

function parentDirectoryPath(value) {
    const path = normalizeDirectoryPath(value);

    if (path === HOME_PATH) {
        return HOME_PATH;
    }

    const separator = path.lastIndexOf("/");

    return separator <= 0 ? HOME_PATH : path.slice(0, separator) || HOME_PATH;
}

function displayDirectoryPath(value) {
    const path = normalizeDirectoryPath(value);
    return path === HOME_PATH ? "" : path;
}

function directoryResponseParts(data) {
    const directoryRecords = Array.isArray(data) ? data[0] : data?.directories ?? data?.folders ?? data?.children;

    const fileRecords = Array.isArray(data) ? data[1] : data?.files ?? data?.items;

    if (!Array.isArray(directoryRecords) || !Array.isArray(fileRecords)) {
        throw new Error("The server returned an unexpected directory listing.",);
    }

    return {directoryRecords, fileRecords};
}

async function getJSON(url, token) {
    const response = await fetch(url, {
        headers: {
            Authorization: `Bearer ${token}`,
        }, cache: "no-store",
    });

    let data;

    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (response.status === 401) {
        const error = new Error("Your session has expired. Please sign in again.",);

        error.status = 401;
        throw error;
    }

    if (!response.ok) {
        throw new Error(data?.detail || data?.message || `Request failed (${response.status}).`,);
    }

    return data;
}

/**
 * Owns the in-memory directory tree.
 *
 * Initial load and explicit refresh:
 *   Fetch every directory, plus trash and account metadata.
 *
 * Folder navigation:
 *   Uses the existing in-memory tree without fetching again.
 *
 * Local mutations:
 *   Refresh only the affected directory when appropriate.
 */
export default function useDashboardData(currentFolder, navigate) {
    const initialToken = getToken();
    const initialCache = readTreeCache(initialToken);

    const [files, setFiles] = useState(() => initialCache?.files ?? [],);

    const [folderRecords, setFolderRecords] = useState(() => initialCache?.folders ?? [],);

    const [storageUsed, setStorageUsed] = useState(() => initialCache?.storageUsed ?? null,);

    const [treeLoaded, setTreeLoaded] = useState(Boolean(initialCache),);

    const [loadingFiles, setLoadingFiles] = useState(!initialCache,);

    const [loadedFolder, setLoadedFolder] = useState(() => initialCache ? (currentFolder || "") : null,);

    const [pageError, setPageError] = useState("");

    const currentFolderRef = useRef(currentFolder);
    const navigateRef = useRef(navigate);
    const fullFetchPromiseRef = useRef(null);
    const treeVersionRef = useRef(0);
    const hadCacheAtMountRef = useRef(Boolean(initialCache));

    currentFolderRef.current = currentFolder;
    navigateRef.current = navigate;

    const fetchMetadata = useCallback(async () => {
        const token = getToken();

        if (!token || !API_URL) {
            return;
        }

        try {
            const metadata = await getJSON(`${API_URL}/auth/metadata`, token,);

            const total = Number(metadata?.storage_used_bytes);

            if (Number.isFinite(total) && total >= 0) {
                setStorageUsed(total);
            }
        } catch (error) {
            if (error.status === 401) {
                clearDashboardTreeCache(token);
                localStorage.removeItem("token");
                navigateRef.current("/");
                return;
            }

            console.error("Failed to fetch account metadata:", error,);
        }
    }, []);

    /**
     * Fetch one directory.
     * This helper is used by the complete-tree scan and targeted refreshes.
     */
    const fetchDirectory = useCallback(async (directoryPath, token) => {
        const path = normalizeDirectoryPath(directoryPath);

        const data = await getJSON(`${API_URL}/auth/directory?directory=${encodeURIComponent(path)}`, token,);

        const {
            directoryRecords, fileRecords,
        } = directoryResponseParts(data);

        return {
            path, directoryRecords, fileRecords,
        };
    }, [],);

    /**
     * Rebuild the complete in-memory tree.
     *
     * The pending queue is drained in bounded batches. Newly discovered
     * directories are appended to the remaining queue, never substituted
     * for it. This prevents folders from being skipped when a parent
     * contains more than DIRECTORY_BATCH_SIZE children.
     */
    const fetchFiles = useCallback(() => {
        // Reuse an active scan, including during React StrictMode.
        if (fullFetchPromiseRef.current) {
            return fullFetchPromiseRef.current;
        }

        const requestVersion = ++treeVersionRef.current;

        const request = (async () => {
            const token = getToken();

            if (!token) {
                navigateRef.current("/");
                setLoadingFiles(false);
                return;
            }

            if (!API_URL) {
                setPageError("The API URL is not configured.");
                setLoadingFiles(false);
                return;
            }

            setLoadingFiles(true);
            setPageError("");

            void fetchMetadata();

            try {
                const activeFolderMap = new Map();
                const activeFileMap = new Map();

                const visitedPaths = new Set([HOME_PATH]);
                const pendingPaths = [HOME_PATH];

                // Process every discovered directory exactly once.
                while (pendingPaths.length > 0) {
                    const batch = pendingPaths.splice(0, DIRECTORY_BATCH_SIZE,);

                    const results = await Promise.all(batch.map((path) => fetchDirectory(path, token)),);

                    const nextPaths = [];

                    for (const result of results) {
                        const displayPath = displayDirectoryPath(result.path,);

                        // Record the files directly inside this directory.
                        for (const file of result.fileRecords) {
                            if (!file || file.name === ".__folder__") {
                                continue;
                            }

                            const key = String(file.id ?? `${result.path}:${file.name}`,);

                            activeFileMap.set(key, {
                                ...file, directory: displayPath, is_deleted: false,
                            });
                        }

                        // Record child folders and queue every unvisited path.
                        for (const folder of result.directoryRecords) {
                            if (!folder || typeof folder.path !== "string" || folder.is_deleted) {
                                continue;
                            }

                            const path = normalizeDirectoryPath(folder.path,);

                            if (path === HOME_PATH || !path.startsWith(`${HOME_PATH}/`)) {
                                continue;
                            }

                            // Only accept direct children of the directory
                            // currently being processed.
                            if (parentDirectoryPath(path) !== result.path) {
                                continue;
                            }

                            const key = String(folder.id ?? path);

                            activeFolderMap.set(key, {
                                ...folder, path, is_deleted: false,
                            });

                            if (!visitedPaths.has(path)) {
                                visitedPaths.add(path);
                                nextPaths.push(path);
                            }
                        }
                    }

                    // Critical: preserve directories that did not fit in
                    // the current batch, then append newly discovered ones.
                    pendingPaths.push(...nextPaths);
                }

                // Fetch trash separately because it is not part of the
                // active directory tree.
                const trashData = await getJSON(`${API_URL}/auth/trash`, token,);

                const trashFoldersData = Array.isArray(trashData) ? trashData[0] : trashData?.directories ?? trashData?.folders;

                const trashFilesData = Array.isArray(trashData) ? trashData[1] : trashData?.files;

                if (!Array.isArray(trashFoldersData) || !Array.isArray(trashFilesData)) {
                    throw new Error("The server returned an unexpected trash listing.",);
                }

                const trashFolders = trashFoldersData
                    .filter((folder) => folder && typeof folder.path === "string",)
                    .map((folder) => ({
                        ...folder, path: normalizeDirectoryPath(folder.path), is_deleted: true,
                    }));

                // Map directory IDs to paths so deleted files can be
                // associated with their original folders.
                const directoryPathById = new Map();

                for (const folder of [...activeFolderMap.values(), ...trashFolders,]) {
                    if (folder.id !== undefined && folder.id !== null) {
                        directoryPathById.set(String(folder.id), folder.path,);
                    }
                }

                const trashFiles = trashFilesData
                    .filter((file) => file && file.name !== ".__folder__",)
                    .map((file) => {
                        const storedPath = directoryPathById.get(String(file.directory_id),);

                        return {
                            ...file, directory: storedPath ? displayDirectoryPath(storedPath) : (file.directory || ""), is_deleted: true,
                        };
                    });

                // Discard stale responses if a newer full refresh started.
                if (requestVersion !== treeVersionRef.current) {
                    return;
                }

                setFiles([...activeFileMap.values(), ...trashFiles,]);

                setFolderRecords([...activeFolderMap.values(), ...trashFolders,]);

                setTreeLoaded(true);
                setLoadedFolder(currentFolderRef.current || "",);
            } catch (error) {
                if (requestVersion === treeVersionRef.current) {
                    if (error.status === 401) {
                        clearDashboardTreeCache(token);
                        localStorage.removeItem("token");
                        navigateRef.current("/");
                        return;
                    }

                    console.error("Failed to fetch the complete file tree:", error,);

                    setPageError(error.message || "Failed to load your files and folders.",);
                }
            } finally {
                if (requestVersion === treeVersionRef.current) {
                    setLoadingFiles(false);
                }
            }
        })();

        fullFetchPromiseRef.current = request;

        const clearRequest = () => {
            if (fullFetchPromiseRef.current === request) {
                fullFetchPromiseRef.current = null;
            }
        };

        request.then(clearRequest, clearRequest);

        return request;
    }, [fetchDirectory, fetchMetadata]);

    /**
     * Refresh a single directory after a successful local mutation.
     * This does not reload the entire tree.
     */
    const refreshDirectory = useCallback(async (requestedPath) => {
        const path = normalizeDirectoryPath(requestedPath);

        const pendingFullFetch = fullFetchPromiseRef.current;

        if (pendingFullFetch) {
            try {
                await pendingFullFetch;
            } catch {
                // Continue with the targeted request.
            }
        }

        const token = getToken();

        if (!token) {
            navigateRef.current("/");
            return;
        }

        if (!API_URL) {
            throw new Error("The API URL is not configured.");
        }

        const requestVersion = treeVersionRef.current;
        const result = await fetchDirectory(path, token);

        if (requestVersion !== treeVersionRef.current) {
            return;
        }

        const displayPath = displayDirectoryPath(path);

        const freshFolders = result.directoryRecords
            .filter((folder) => folder && typeof folder.path === "string" && !folder.is_deleted,)
            .map((folder) => ({
                ...folder, path: normalizeDirectoryPath(folder.path), is_deleted: false,
            }))
            .filter((folder) => folder.path !== HOME_PATH && parentDirectoryPath(folder.path) === path,);

        const freshFiles = result.fileRecords
            .filter((file) => file && file.name !== ".__folder__",)
            .map((file) => ({
                ...file, directory: displayPath, is_deleted: false,
            }));

        setFolderRecords((current) => [...current.filter((folder) => folder.is_deleted || parentDirectoryPath(folder.path) !== path,), ...freshFolders,]);

        setFiles((current) => [...current.filter((file) => file.is_deleted || (file.directory || "") !== displayPath,), ...freshFiles,]);

        void fetchMetadata();
    }, [fetchDirectory, fetchMetadata],);

    // Navigation only changes the visible slice of the cached tree.
    useEffect(() => {
        setLoadedFolder(currentFolder || "");
    }, [currentFolder]);

    // Load the tree only when no account-specific in-memory cache exists.
    useEffect(() => {
        if (!hadCacheAtMountRef.current) {
            void fetchFiles();
        }
    }, [fetchFiles]);

    // Preserve the latest tree across SPA route changes. Do not persist it
    // to localStorage: a hard browser reload must fetch fresh server data.
    useEffect(() => {
        const token = getToken();

        if (treeLoaded && token) {
            inMemoryTreeCache = {
                token, files, folders: folderRecords, storageUsed,
            };
        }
    }, [files, folderRecords, storageUsed, treeLoaded]);

    /**
     * Calculate recursive folder sizes.
     *
     * Each file belongs to one directory. A folder includes files directly
     * inside itself and files in every descendant directory.
     *
     * Accept common API size fields, including numeric strings. Invalid or
     * missing sizes contribute zero rather than producing NaN.
     */
    const folders = useMemo(() => {
        // Normalize each file's directory once.
        const filesByDirectory = new Map();

        for (const file of files) {
            const directory = normalizeDirectoryPath(file.directory || HOME_PATH,);

            if (!filesByDirectory.has(directory)) {
                filesByDirectory.set(directory, []);
            }

            filesByDirectory.get(directory).push(file);
        }

        // Build a direct-child index so each folder can be traversed
        // without scanning the entire folder list repeatedly.
        const childrenByParent = new Map();

        for (const folder of folderRecords) {
            const path = normalizeDirectoryPath(folder.path);
            const parent = parentDirectoryPath(path);

            if (!childrenByParent.has(parent)) {
                childrenByParent.set(parent, []);
            }

            childrenByParent.get(parent).push({
                ...folder, path,
            });
        }

        // Memoize recursive totals by normalized path and deleted status.
        const sizeCache = new Map();

        const getFolderSize = (folderPath, includeDeleted) => {
            const path = normalizeDirectoryPath(folderPath);
            const cacheKey = `${includeDeleted ? "trash" : "active"}:${path}`;

            if (sizeCache.has(cacheKey)) {
                return sizeCache.get(cacheKey);
            }

            let total = 0;

            const directFiles = filesByDirectory.get(path) ?? [];

            for (const file of directFiles) {
                if (!includeDeleted && file.is_deleted) {
                    continue;
                }

                const rawSize = file.size ?? file.file_size ?? file.size_bytes ?? file.file_size_bytes ?? 0;

                const size = Number(rawSize);

                if (Number.isFinite(size) && size > 0) {
                    total += size;
                }
            }

            const childFolders = childrenByParent.get(path) ?? [];

            for (const child of childFolders) {
                if (!includeDeleted && child.is_deleted) {
                    continue;
                }

                total += getFolderSize(child.path, includeDeleted || child.is_deleted,);
            }

            sizeCache.set(cacheKey, total);

            return total;
        };

        return folderRecords.map((folder) => {
            const path = normalizeDirectoryPath(folder.path);

            return {
                ...folder, total_size_bytes: getFolderSize(path, Boolean(folder.is_deleted),),
            };
        });
    }, [folderRecords, files]);

    return {
        files,
        setFiles,
        folders,
        setFolders: setFolderRecords,
        storageUsed,
        loadingFiles,
        loadedFolder,
        pageError,
        setPageError,
        fetchFiles,
        refreshDirectory,
    };
}
