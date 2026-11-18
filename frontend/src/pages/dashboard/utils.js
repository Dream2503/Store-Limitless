import {API_URL, FILE_ICONS} from "./constants.js";

/** Generate a unique identifier with compatibility for browsers lacking crypto.randomUUID(). */
export function createUniqueId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }

    if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("").replace(
            /^(........)(....)(....)(....)(............)$/,
            "$1-$2-$3-$4-$5",
        );
    }

    // Older or restricted browsers may not expose Web Crypto at all.
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

/** Stable key for storing per-view selection snapshots. */
export function makeSelectionViewKey(pathname, activeTab, searchQuery) {
    return JSON.stringify([pathname, activeTab, searchQuery]);
}

/** Select the icon/color pair for a file extension. */
export function getFileIcon(filename) {
    const extension = filename?.split(".").pop()?.toLowerCase();
    return FILE_ICONS[extension] || FILE_ICONS.default;
}

/** Format a date for display, returning a placeholder for invalid dates. */
export function formatDate(dateStr) {
    if (!dateStr) return "—";
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"});
}

/** Return the supported preview category for a filename. */
export function getFileKind(filename) {
    const ext = filename?.split(".").pop()?.toLowerCase();
    if (["mp4", "mkv", "webm", "mov", "m4v"].includes(ext)) return "video";
    if (["mp3", "wav", "ogg", "m4a", "flac"].includes(ext)) return "audio";
    if (ext === "pdf") return "pdf";
    if (["md", "markdown", "mdown", "mkd"].includes(ext)) return "markdown";
    if (["txt", "text", "log", "csv", "tsv", "json", "jsonl", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "html", "htm", "css", "scss", "sass", "js", "jsx", "mjs", "cjs", "ts", "tsx", "py", "pyw", "ipynb", "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "hxx", "rs", "go", "java", "kt", "kts", "swift", "rb", "php", "sh", "bash", "zsh", "fish", "sql", "r", "lua", "pl", "pm", "ex", "exs", "erl", "hrl", "hs", "lhs", "scala", "sc", "dart", "vue", "svelte", "graphql", "gql", "proto", "make", "cmake", "dockerfile", "gitignore", "editorconfig", "diff", "patch", "properties", "gradle",].includes(ext)) return "text";
    return null;
}

/** Format byte counts using decimal units to match Linux Files (base 1000). */
export function formatFileSize(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    const index = Math.min(Math.floor(Math.log(value) / Math.log(1000)), units.length - 1);
    return `${(value / (1000 ** index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

/** Browser events emitted when an authenticated session starts or ends. */
export const AUTH_SESSION_START_EVENT = "storelimitless:session-started";
export const AUTH_SESSION_END_EVENT = "storelimitless:session-ended";

/** Revoke server-issued private links, clear the browser session, and cancel private work. */
export function endAuthSession(expectedToken) {
    const currentToken = localStorage.getItem("token");

    // An old request can finish after a new login. It must never revoke or clear
    // the newer session just because the previous token received a 401 response.
    if (expectedToken && currentToken !== expectedToken) return false;

    if (currentToken && typeof fetch === "function") {
        // Start revocation before clearing the bearer token. keepalive lets the small
        // request finish even if the browser navigates immediately after sign-out.
        void fetch(`${API_URL}/auth/logout`, {
            method: "POST",
            headers: {Authorization: `Bearer ${currentToken}`},
            cache: "no-store",
            keepalive: true,
        }).then((response) => {
            if (!response.ok) {
                throw new Error(`Session revocation failed (HTTP ${response.status}).`);
            }
        }).catch((error) => console.warn("Could not revoke the signed-out session on the server:", error));
    }

    localStorage.removeItem("token");
    if (typeof window !== "undefined") {
        window.dispatchEvent(new Event(AUTH_SESSION_END_EVENT));
    }
    return true;
}

/** Read the current bearer token from browser storage. */
export function getToken() {
    return localStorage.getItem("token");
}

/** Convert a stored folder path into its dashboard route. */
export function folderPathToRoute(folderPath) {
    const segments = (folderPath || "").split("/").filter(Boolean);
    const relativeSegments = segments[0] === "home" ? segments.slice(1) : segments;
    return `/home${relativeSegments.length ? `/${relativeSegments.map(encodeURIComponent).join("/")}` : ""}`;
}

/** Decode a URL path segment without failing on malformed percent escapes. */
export function decodePathSegment(segment) {
    try {
        return decodeURIComponent(segment);
    } catch {
        return segment;
    }
}
