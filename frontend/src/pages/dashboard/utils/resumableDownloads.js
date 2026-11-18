const DB_NAME = "storelimitless-resumable-downloads";
const DB_VERSION = 2;
const STORE_NAME = "downloads";
const CHUNK_STORE_NAME = "downloadChunks";

function openDatabase() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
            const db = request.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, {keyPath: "id"});
            }

            let chunks;
            if (!db.objectStoreNames.contains(CHUNK_STORE_NAME)) {
                chunks = db.createObjectStore(CHUNK_STORE_NAME, {keyPath: "key"});
            } else {
                chunks = request.transaction.objectStore(CHUNK_STORE_NAME);
            }

            if (!chunks.indexNames.contains("downloadId")) {
                chunks.createIndex("downloadId", "downloadId", {unique: false});
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("Could not open download storage."));
    });
}

async function withStore(storeName, mode, action) {
    const db = await openDatabase();
    try {
        return await new Promise((resolve, reject) => {
            const transaction = db.transaction(storeName, mode);
            const store = transaction.objectStore(storeName);
            let request;
            try {
                request = action(store);
            } catch (error) {
                reject(error);
                return;
            }
            transaction.oncomplete = () => resolve(request?.result);
            transaction.onerror = () => reject(transaction.error || request?.error);
            transaction.onabort = () => reject(transaction.error || new Error("Download task storage was interrupted."));
        });
    } finally {
        db.close();
    }
}

export const listResumableDownloads = () => withStore(STORE_NAME, "readonly", (store) => store.getAll());
export const saveResumableDownload = (job) => withStore(STORE_NAME, "readwrite", (store) => store.put(job));
export const removeResumableDownload = (id) => withStore(STORE_NAME, "readwrite", (store) => store.delete(id));

/**
 * Save a fully verified byte range before advancing the persisted download offset.
 * Chunks are immutable and keyed by their starting byte so an interrupted range
 * can safely be requested again without corrupting already saved data.
 */
export const saveResumableDownloadChunk = (downloadId, offset, bytes) => withStore(
    CHUNK_STORE_NAME,
    "readwrite",
    (store) => store.put({
        key: `${downloadId}:${offset}`,
        downloadId,
        offset,
        bytes,
    }),
);

/** Return saved byte ranges for a task in ascending offset order. */
export const listResumableDownloadChunks = async (downloadId) => {
    const chunks = await withStore(
        CHUNK_STORE_NAME,
        "readonly",
        (store) => store.index("downloadId").getAll(downloadId),
    );
    return chunks.sort((left, right) => left.offset - right.offset);
};

/** Delete only the saved byte ranges belonging to one download task. */
export const removeResumableDownloadChunks = (downloadId) => withStore(
    CHUNK_STORE_NAME,
    "readwrite",
    (store) => {
        const request = store.index("downloadId").getAllKeys(downloadId);
        request.onsuccess = () => {
            for (const key of request.result) store.delete(key);
        };
        return request;
    },
);

/** Delete a damaged or non-contiguous suffix while preserving earlier verified ranges. */
export const removeResumableDownloadChunksFrom = (downloadId, offset) => withStore(
    CHUNK_STORE_NAME,
    "readwrite",
    (store) => {
        const request = store.index("downloadId").getAll(downloadId);
        request.onsuccess = () => {
            for (const chunk of request.result) {
                if (Number(chunk.offset) >= offset) store.delete(chunk.key);
            }
        };
        return request;
    },
);


/*
 * Serialize every download started by StoreLimitless. Dashboard and file-view
 * downloads share this queue so only one transfer can write staged data at once.
 */
let activeDownloadTaskId = null;
const queuedDownloadTasks = [];

/** Start one queued transfer and advance the FIFO queue after it settles. */
function startQueuedDownloadTask(task) {
    activeDownloadTaskId = task.id;
    void (async () => {
        try {
            await task.run();
        } catch (error) {
            console.error("Download task failed unexpectedly:", error);
        } finally {
            if (activeDownloadTaskId === task.id) activeDownloadTaskId = null;
            const nextTask = queuedDownloadTasks.shift();
            if (nextTask) startQueuedDownloadTask(nextTask);
        }
    })();
}

/**
 * Add a download to the shared FIFO queue.
 * @param {string} id
 * @param {() => Promise<void>} run
 * @returns {"started" | "queued" | "existing"}
 */
export function enqueueDownloadTask(id, run) {
    const taskId = String(id);
    if (activeDownloadTaskId === taskId || queuedDownloadTasks.some((task) => task.id === taskId)) {
        return "existing";
    }

    const task = {id: taskId, run};
    if (activeDownloadTaskId === null) {
        startQueuedDownloadTask(task);
        return "started";
    }

    queuedDownloadTasks.push(task);
    return "queued";
}

/** Remove a task that has not started; active work must abort and settle normally. */
export function cancelQueuedDownloadTask(id) {
    const taskId = String(id);
    const previousLength = queuedDownloadTasks.length;
    for (let index = queuedDownloadTasks.length - 1; index >= 0; index -= 1) {
        if (queuedDownloadTasks[index].id === taskId) queuedDownloadTasks.splice(index, 1);
    }
    return queuedDownloadTasks.length !== previousLength;
}
