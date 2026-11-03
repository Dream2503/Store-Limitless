import {useCallback, useEffect, useRef, useState} from "react";

/**
 * Optional browser-side container demuxing for files with multiple audio or
 * subtitle tracks. If the player bundle cannot load, the caller keeps using
 * the regular HTMLVideoElement player.
 *
 * @param {{
 *   source: string,
 *   title?: string,
 *   active?: boolean,
 *   onUseStandardPlayer?: () => void,
 *   onUnavailable?: (error?: unknown) => void,
 * }} props
 */
export default function BrowserDemuxPlayer({
    source,
    title,
    active = true,
    onUseStandardPlayer,
    onUnavailable,
}) {
    const playerRef = useRef(null);
    const fallbackTriggeredRef = useRef(false);
    const [libraryReady, setLibraryReady] = useState(false);
    const [zeroCopyAvailable, setZeroCopyAvailable] = useState(
        () => Boolean(window.crossOriginIsolated && typeof SharedArrayBuffer !== "undefined"),
    );

    const fallback = useCallback((error) => {
        if (fallbackTriggeredRef.current) return;
        fallbackTriggeredRef.current = true;
        if (error) console.warn("Browser demux playback unavailable; using the standard player.", error);
        onUnavailable?.(error);
    }, [onUnavailable]);

    useEffect(() => {
        let cancelled = false;

        import("movi-player/element")
            .then(() => customElements.whenDefined("movi-player"))
            .then(() => {
                if (!cancelled) setLibraryReady(true);
            })
            .catch((error) => {
                if (!cancelled) fallback(error);
            });

        return () => {
            cancelled = true;
        };
    }, [fallback]);

    useEffect(() => {
        if (!libraryReady) return undefined;

        const player = playerRef.current;
        if (!player) return undefined;

        const handleError = (event) => fallback(event?.detail || new Error("The browser media player could not open this file."));

        player.addEventListener("error", handleError);
        player.setAttribute("engine", "wasm native");
        player.setAttribute("fallback", "native");
        player.setAttribute("sw", "auto");
        player.setAttribute("crossorigin", "anonymous");
        player.src = source;

        return () => {
            player.removeEventListener("error", handleError);
            try {
                player.pause();
            } catch {
                // The custom element may already be disconnecting.
            }
        };
    }, [fallback, libraryReady, source]);

    useEffect(() => {
        if (!active) {
            playerRef.current?.pause();
        }
    }, [active]);

    useEffect(() => {
        const updateIsolationStatus = () => {
            setZeroCopyAvailable(Boolean(
                window.crossOriginIsolated && typeof SharedArrayBuffer !== "undefined",
            ));
        };
        updateIsolationStatus();
        window.addEventListener("pageshow", updateIsolationStatus);
        return () => window.removeEventListener("pageshow", updateIsolationStatus);
    }, []);

    return (
        <div className="relative h-full min-h-0 w-full bg-black">
            {libraryReady ? (
                <movi-player
                    ref={playerRef}
                    src={source}
                    title={title || "Video"}
                    controls
                    playsinline
                    preload="metadata"
                    theme="dark"
                    themecolor="#3ecf8e"
                    engine="wasm native"
                    fallback="native"
                    sw="auto"
                    crossorigin="anonymous"
                    style={{
                        display: "block",
                        width: "100%",
                        height: "100%",
                        minHeight: "240px",
                        borderRadius: "inherit",
                        "--movi-primary-color": "#3ecf8e",
                        "--movi-bg-color": "rgba(0, 0, 0, 0.88)",
                        "--movi-text-color": "#ffffff",
                        "--movi-progress-color": "#3ecf8e",
                        "--movi-buffer-color": "rgba(255, 255, 255, 0.3)",
                    }}
                />
            ) : (
                <div className="flex h-full min-h-[240px] w-full items-center justify-center gap-3 text-sm text-[#898989]">
                    <span className="material-symbols-outlined animate-spin text-[24px]" aria-hidden="true">progress_activity</span>
                    Preparing browser media player…
                </div>
            )}

            <div
                className={`absolute left-3 top-3 z-[60] rounded-md border px-2.5 py-1.5 text-[11px] font-medium shadow-lg backdrop-blur ${zeroCopyAvailable ? "border-[#3ecf8e]/40 bg-[#062a1d]/85 text-[#8af0bd]" : "border-white/15 bg-black/70 text-white/75"}`}
                title={zeroCopyAvailable
                    ? "Cross-origin isolation is active. Movi can use its SharedArrayBuffer zero-copy HTTP streaming path."
                    : "Zero-copy streaming is unavailable. Open StoreLimitless from localhost or HTTPS and enable COOP/COEP headers."}
            >
                {zeroCopyAvailable ? "Zero-copy stream" : "Standard stream"}
            </div>

            <button
                type="button"
                onClick={onUseStandardPlayer}
                title="Return to the standard StoreLimitless player"
                className="absolute right-3 top-3 z-[60] rounded-md border border-white/20 bg-black/70 px-2.5 py-1.5 text-[11px] font-medium text-white/90 shadow-lg backdrop-blur transition-colors hover:bg-black/90 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]"
            >
                Standard player
            </button>
        </div>
    );
}
