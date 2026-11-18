const DB_NAME = "storelimitless-resumable-downloads";
const DB_VERSION = 1;
const STORE_NAME = "downloads";

function openDatabase() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(STORE_NAME)) request.result.createObjectStore(STORE_NAME, {keyPath: "id"});
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("Could not open download storage."));
    });
}

async function withStore(mode, action) {
    const db = await openDatabase();
    try {
        return await new Promise((resolve, reject) => {
            const transaction = db.transaction(STORE_NAME, mode);
            const store = transaction.objectStore(STORE_NAME);
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

export const listResumableDownloads = () => withStore("readonly", (store) => store.getAll());
export const saveResumableDownload = (job) => withStore("readwrite", (store) => store.put(job));
export const removeResumableDownload = (id) => withStore("readwrite", (store) => store.delete(id));
