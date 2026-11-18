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
    configuredApiUrl || "https://cdn-storelimitless.vercel.app"
).replace(/\/+$/, "");

const STABLE_API_PROXY_ORIGIN = "https://cdn-storelimitless.vercel.app";
const TUNNEL_DISCOVERY_TIMEOUT_MS = 10000;

/**
 * Resolve the current Cloudflare Quick Tunnel only for browser file-byte uploads.
 * The stable Vercel API origin remains responsible for job creation, progress,
 * authentication and all other metadata operations.
 *
 * Local and custom/self-hosted API configurations continue using API_URL directly.
 *
 * @returns {Promise<string>}
 */
export async function getDirectUploadApiUrl() {
    const discoveryError = (message) => {
        const error = new Error(message);
        error.queueFatal = true;
        return error;
    };
    let apiOrigin;
    try {
        apiOrigin = new URL(API_URL).origin;
    } catch {
        throw discoveryError("The configured StoreLimitless API URL is invalid.");
    }

    if (apiOrigin !== STABLE_API_PROXY_ORIGIN) {
        return API_URL;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(
        () => controller.abort(),
        TUNNEL_DISCOVERY_TIMEOUT_MS,
    );

    let response;
    try {
        response = await fetch(`${STABLE_API_PROXY_ORIGIN}/api/current-upstream`, {
            method: "GET",
            headers: {Accept: "application/json"},
            cache: "no-store",
            credentials: "omit",
            signal: controller.signal,
        });
    } catch (error) {
        if (error?.name === "AbortError") {
            throw discoveryError("Timed out while discovering the current backend tunnel. Please try again.");
        }
        throw discoveryError("Could not contact the StoreLimitless tunnel registry. Check your connection and try again.");
    } finally {
        window.clearTimeout(timeoutId);
    }

    const payload = await response.json().catch(() => null);
    if (!response.ok) {
        throw discoveryError(payload?.error || `Could not discover the backend tunnel (HTTP ${response.status}).`);
    }

    const candidate = typeof payload?.api_url === "string" ? payload.api_url.trim() : "";
    let tunnel;
    try {
        tunnel = new URL(candidate);
    } catch {
        throw discoveryError("The tunnel registry returned an invalid backend URL. Restart the backend and try again.");
    }

    if (
        tunnel.protocol !== "https:"
        || !tunnel.hostname.toLowerCase().endsWith(".trycloudflare.com")
        || tunnel.username
        || tunnel.password
        || tunnel.port
        || tunnel.pathname !== "/"
        || tunnel.search
        || tunnel.hash
    ) {
        throw discoveryError("The tunnel registry returned an unsupported backend URL.");
    }

    return tunnel.origin;
}

