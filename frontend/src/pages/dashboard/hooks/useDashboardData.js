import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {API_URL} from "../constants.js";
import {endAuthSession, getToken} from "../utils.js";

const HOME_PATH = "/home";

// Keep one account's tree in JavaScript memory so leaving and returning to the
// dashboard does not trigger another full scan. A browser reload clears it.
let inMemoryTreeCache = null;

function readTreeCache(token) {
    return token && inMemoryTreeCache?.token === token ? inMemoryTreeCache : null;
}

export function clearDashboardTreeCache(token) {
    if (!token || inMemoryTreeCache?.token === token) inMemoryTreeCache = null;
}

function normalizeDirectoryPath(value) {
    let path = String(value ?? "").trim().replace(/\\/g, "/").replace(/\/{2,}/g, "/");
    path = path.replace(/\/$/, "");
    if (!path || path === "/" || path === "home" || path === HOME_PATH) return HOME_PATH;
    if (path.startsWith("home/")) return `/${path}`;
    return path.startsWith("/") ? path : `${HOME_PATH}/${path}`;
}

function parentDirectoryPath(value) {
    const path = normalizeDirectoryPath(value);
    if (path === HOME_PATH) return HOME_PATH;
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
        throw new Error("The server returned an unexpected directory listing.");
    }
    return {directoryRecords, fileRecords};
}

async function getJSON(url, token) {
    const response = await fetch(url, {
        headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
    });

    let data;
    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (response.status === 401) {
        const error = new Error("Your session has expired. Please sign in again.");
        error.status = 401;
        throw error;
    }
    if (!response.ok) {
        throw new Error(data?.detail || data?.message || `Request failed (${response.status}).`);
    }
    return data;
}

/**
 * Owns the complete in-memory file tree. A full tree fetch happens on mount,
 * browser reload, or an explicit refresh; changing folders only filters cache.
 */
export default function useDashboardData(currentFolder, navigate) {
    const initialToken = getToken();
    const initialCache = readTreeCache(initialToken);
    const [files, setFiles] = useState(() => initialCache?.files ?? []);
    const [folderRecords, setFolderRecords] = useState(() => initialCache?.folders ?? []);
    const [storageUsed, setStorageUsed] = useState(() => initialCache?.storageUsed ?? null);
    const [treeLoaded, setTreeLoaded] = useState(Boolean(initialCache));
    const [loadingFiles, setLoadingFiles] = useState(!initialCache);
    const [loadedFolder, setLoadedFolder] = useState(() => initialCache ? (currentFolder || "") : null);
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
        if (!token || !API_URL) return;

        try {
            const metadata = await getJSON(`${API_URL}/auth/metadata`, token);
            if (getToken() !== token) return;
            const total = Number(metadata?.storage_used_bytes);
            if (Number.isFinite(total) && total >= 0) setStorageUsed(total);
        } catch (error) {
            if (error.status === 401) {
                clearDashboardTreeCache(token);
                if (getToken() === token && endAuthSession(token)) navigateRef.current("/");
                return;
            }
            // Metadata must not prevent the cached file tree from being browsed.
            console.error("Failed to fetch account metadata:", error);
        }
    }, []);

    /** Load one directory's direct children; used during a full tree scan. */
    const fetchDirectory = useCallback(async (directoryPath, token) => {
        const path = normalizeDirectoryPath(directoryPath);
        const data = await getJSON(`${API_URL}/auth/directory?directory=${encodeURIComponent(path)}`, token,);
        const {directoryRecords, fileRecords} = directoryResponseParts(data);
        return {path, directoryRecords, fileRecords};
    }, []);

    /** Fetch the complete tree and account storage total in one API request. */
    const fetchFiles = useCallback(() => {
        // Share an in-flight request when React StrictMode re-runs the mount effect.
        if (fullFetchPromiseRef.current) return fullFetchPromiseRef.current;

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

            try {
                // The backend returns active and trashed files/folders together,
                // along with storage usage. This replaces a request per directory.
                const treeData = await getJSON(`${API_URL}/auth/tree`, token);
                if (getToken() !== token) return;
                const directoryData = treeData?.directories ?? treeData?.folders;
                const fileData = treeData?.files;

                if (!Array.isArray(directoryData) || !Array.isArray(fileData)) {
                    throw new Error("The server returned an unexpected complete file tree.");
                }

                const allDirectories = directoryData
                    .filter((folder) => folder && typeof folder.path === "string")
                    .map((folder) => ({
                        ...folder, path: normalizeDirectoryPath(folder.path), is_deleted: Boolean(folder.is_deleted ?? folder.deleted_at),
                    }))
                    .filter((folder) => folder.path !== HOME_PATH && folder.path.startsWith(`${HOME_PATH}/`));

                const directoryPathById = new Map();
                for (const folder of directoryData) {
                    if (folder?.id !== undefined && folder?.id !== null && typeof folder.path === "string") {
                        directoryPathById.set(String(folder.id), normalizeDirectoryPath(folder.path));
                    }
                }

                const allFiles = fileData
                    .filter((file) => file && file.name !== ".__folder__")
                    .map((file) => {
                        const storedPath = directoryPathById.get(String(file.directory_id));
                        const path = storedPath ?? file.directory ?? "";
                        return {
                            ...file, directory: displayDirectoryPath(path), is_deleted: Boolean(file.is_deleted ?? file.deleted_at),
                        };
                    });

                const storageTotal = Number(treeData?.storage_used_bytes);
                if (requestVersion !== treeVersionRef.current) return;

                setFiles(allFiles);
                setFolderRecords(allDirectories);
                if (Number.isFinite(storageTotal) && storageTotal >= 0) {
                    setStorageUsed(storageTotal);
                }
                setTreeLoaded(true);
                setLoadedFolder(currentFolderRef.current || "");
            } catch (error) {
                if (requestVersion === treeVersionRef.current) {
                    if (error.status === 401) {
                        clearDashboardTreeCache(token);
                        if (getToken() === token && endAuthSession(token)) navigateRef.current("/");
                        return;
                    }
                    console.error("Failed to fetch the complete file tree:", error);
                    setPageError(error.message || "Failed to load your files and folders.");
                }
            } finally {
                if (requestVersion === treeVersionRef.current) setLoadingFiles(false);
            }
        })();

        fullFetchPromiseRef.current = request;
        const clearRequest = () => {
            if (fullFetchPromiseRef.current === request) fullFetchPromiseRef.current = null;
        };
        request.then(clearRequest, clearRequest);
        return request;
    }, []);

    /**
     * Refresh only one directory after a local write (for example, creating a
     * folder or completing an upload). This never walks the rest of the tree.
     */
    const refreshDirectory = useCallback(async (requestedPath) => {
        const path = normalizeDirectoryPath(requestedPath);
        const pendingFullFetch = fullFetchPromiseRef.current;
        if (pendingFullFetch) {
            try {
                await pendingFullFetch;
            } catch {
                // Keep the original error visible; the targeted request below may still work.
            }
        }

        const token = getToken();
        if (!token) {
            navigateRef.current("/");
            return;
        }
        if (!API_URL) throw new Error("The API URL is not configured.");

        const requestVersion = treeVersionRef.current;
        const result = await fetchDirectory(path, token);
        if (requestVersion !== treeVersionRef.current) return;

        const displayPath = displayDirectoryPath(path);
        const freshFolders = result.directoryRecords
            .filter((folder) => folder && typeof folder.path === "string" && !folder.is_deleted)
            .map((folder) => ({...folder, path: normalizeDirectoryPath(folder.path), is_deleted: false}))
            .filter((folder) => folder.path !== HOME_PATH && parentDirectoryPath(folder.path) === path);
        const freshFiles = result.fileRecords
            .filter((file) => file && file.name !== ".__folder__")
            .map((file) => ({...file, directory: displayPath, is_deleted: false}));

        setFolderRecords((current) => [...current.filter((folder) => folder.is_deleted || parentDirectoryPath(folder.path) !== path), ...freshFolders,]);
        setFiles((current) => [...current.filter((file) => file.is_deleted || (file.directory || "") !== displayPath), ...freshFiles,]);
        void fetchMetadata();
    }, [fetchDirectory, fetchMetadata]);

    // Folder navigation only changes which slice of the already loaded tree is
    // displayed. It does not trigger directory or file requests.
    useEffect(() => {
        setLoadedFolder(currentFolder || "");
    }, [currentFolder]);

    useEffect(() => {
        if (!hadCacheAtMountRef.current) void fetchFiles();
    }, [fetchFiles]);

    // Keep the latest successful tree in memory across SPA route unmounts. This
    // is intentionally not localStorage/sessionStorage, so F5 starts a fresh scan.
    useEffect(() => {
        const token = getToken();
        if (treeLoaded && token) {
            inMemoryTreeCache = {token, files, folders: folderRecords, storageUsed};
        }
    }, [files, folderRecords, storageUsed, treeLoaded]);

    // Derive each folder's apparent size from unique file records in that
    // subtree. The same file can be present in overlapping cached directory
    // responses, so de-duplicate by its stable database ID before summing.
    // Size fields are byte counts; never add a converted/display-formatted size.
    const folders = useMemo(() => folderRecords.map((folder) => {
        const path = normalizeDirectoryPath(folder.path);
        const prefix = `${path}/`;
        const seenFiles = new Set();
        const totalSize = files.reduce((total, file, index) => {
            if (!file || file.name === ".__folder__") return total;

            const directory = normalizeDirectoryPath(file.directory || HOME_PATH);
            if (!(directory === path || directory.startsWith(prefix))) return total;
            if (!folder.is_deleted && file.is_deleted) return total;

            const identity = file.id ?? file.fid ?? file.file_id;
            const key = identity !== undefined && identity !== null ? `id:${identity}` : `path:${directory}/${file.name ?? ""}:${file.modified_at ?? ""}:${index}`;
            if (seenFiles.has(key)) return total;
            seenFiles.add(key);

            const rawSize = file.size_bytes ?? file.file_size_bytes ?? file.size ?? file.file_size ?? 0;
            const size = Number(rawSize);
            return Number.isFinite(size) && size > 0 ? total + size : total;
        }, 0);

        return {...folder, total_size_bytes: totalSize};
    }), [folderRecords, files]);

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
