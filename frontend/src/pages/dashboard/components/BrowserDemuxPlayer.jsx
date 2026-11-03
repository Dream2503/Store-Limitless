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
