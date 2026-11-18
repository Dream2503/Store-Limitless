/**
 * Resolve the StoreLimitless API URL for packaged, deployed, and standalone Vite builds.
 *
 * In local Vite development the Python backend listens on port 8000 on the same host.
 * This fallback keeps a missing VITE_API_URL from producing requests like
 * http://localhost:5173/undefined/auth/login.
 */
const configuredApiUrl = window.STORELIMITLESS_CONFIG?.API_URL || import.meta.env.VITE_API_URL;
const localDevelopmentApiUrl = import.meta.env.DEV && window.location.port === "5173"
    ? window.location.protocol + "//" + window.location.hostname + ":8000"
    : "";

export const API_URL = String(
    configuredApiUrl || localDevelopmentApiUrl || "https://storelimitlessbackend.vercel.app"
).replace(/\/+$/, "");
