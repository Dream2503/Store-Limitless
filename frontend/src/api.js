/**
 * Resolve the StoreLimitless API URL for packaged, deployed, and standalone Vite builds.
 *
 * LOCAL runs serve the frontend on port 5173 and the API on port 8000. Derive
 * the API host from the browser URL so LAN clients use their reachable server
 * address (for example 192.168.10.6:8000), not a tunnel URL baked into a build.
 */
const runtimeApiUrl = window.STORELIMITLESS_CONFIG?.API_URL;
const localApiUrl = window.location.port === "5173"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "";

const configuredApiUrl = runtimeApiUrl || localApiUrl || import.meta.env.VITE_API_URL;

export const API_URL = String(
    configuredApiUrl || "https://storelimitlessbackend.vercel.app"
).replace(/\/+$/, "");
