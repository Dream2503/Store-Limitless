/**
 * Read files from an external drop, including recursively nested directories.
 * @param {DataTransfer} dataTransfer
 * @returns {Promise<File[]>}
 */
export const getFilesFromDrop = async (dataTransfer) => {
    const items = Array.from(dataTransfer?.items || []);
    const entries = items.map((item) => item.webkitGetAsEntry?.()).filter(Boolean);

    if (!entries.length) return Array.from(dataTransfer?.files || []);

    /**
     * Convert a FileSystemEntry into files while retaining its relative path.
     * @param {FileSystemEntry} entry
     * @param {string} parentPath
     * @returns {Promise<File[]>}
     */
    const readEntry = async (entry, parentPath = "") => {
        if (entry.isFile) {
            const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
            const relativePath = [parentPath, file.name].filter(Boolean).join("/");
            Object.defineProperty(file, "storeLimitlessRelativePath", {value: relativePath, configurable: true});
            return [file];
        }

        if (!entry.isDirectory) return [];

        const directoryPath = [parentPath, entry.name].filter(Boolean).join("/");
        const reader = entry.createReader();
        const children = [];

        while (true) {
            const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
            if (!batch.length) break;
            children.push(...batch);
        }

        const nestedFiles = await Promise.all(children.map((child) => readEntry(child, directoryPath)));
        return nestedFiles.flat();
    };

    const files = await Promise.all(entries.map((entry) => readEntry(entry)));
    return files.flat();
};

/**
 * Return a file's path relative to the selected/dropped folder, if any.
 * @param {File} file
 * @returns {string}
 */
export const getUploadRelativePath = (file) => {
    const path = file.storeLimitlessRelativePath || file.webkitRelativePath || file.name;
    return String(path).replace(/\\/g, "/").split("/").filter((part) => part && part !== "." && part !== "..").join("/");
};