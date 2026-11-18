import {useCallback, useEffect, useRef, useState} from "react";
import * as pdfjsLib from "pdfjs-dist/build/pdf.mjs";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import {API_URL} from "../../../api.js";
import {endAuthSession} from "../utils.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

/** @param {{message: string}} props */
function PreviewMessage({message}) {
    return <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-sm text-[#898989]">
        <span className="material-symbols-outlined text-[30px]"
              aria-hidden="true">{message.startsWith("Loading") || message.startsWith("Preparing") ? "progress_activity" : "error"}</span>
        {message}
    </div>;
}

/**
 * Fetch a signed preview URL for an owned file.
 * @param {string} fileId
 * @param {string | null} [publicToken]
 * @returns {Promise<string>}
 */
async function getPreviewUrl(fileId, publicToken) {
    if (publicToken) return `${API_URL}/public/stream/${encodeURIComponent(publicToken)}`;

    const token = localStorage.getItem("token");
    if (!token) throw new Error("Your session has expired. Please sign in again.");
    const response = await fetch(`${API_URL}/auth/file/${fileId}/private-stream-link`, {
        method: "POST", headers: {Authorization: `Bearer ${token}`},
    });
    if (response.status === 401) {
        endAuthSession();
        throw new Error("Your session has expired. Please sign in again.");
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not open this file.");
    if (!data.public_token) throw new Error("The server did not return a preview token.");
    return `${API_URL}/public/stream/${data.public_token}`;
}

/**
 * Select only the visible file contents for Ctrl+A / Cmd+A.
 * Stop propagation so the dashboard's global select-all shortcut does not
 * replace the browser text selection with a selection of every file.
 * @param {import("react").KeyboardEvent<HTMLElement>} event
 */
function selectAllPreviewContent(event) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== "a") return;

    event.preventDefault();
    event.stopPropagation();

    const selection = window.getSelection();
    if (!selection) return;

    const range = document.createRange();
    range.selectNodeContents(event.currentTarget);
    selection.removeAllRanges();
    selection.addRange(range);
}

/**
 * Render a plain text file or a small safe Markdown subset.
 * @param {{fileId: string, kind: "text" | "markdown", navigate: Function}} props
 */
export default function FileContentPreview({fileId, kind, navigate, publicToken, onRegisterActions}) {
    const [content, setContent] = useState("");
    const [draft, setDraft] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState("");
    const [saveMessage, setSaveMessage] = useState("");

    useEffect(() => {
        let cancelled = false;
        setContent("");
        setDraft("");
        setError("");
        setSaveError("");
        setSaveMessage("");
        setEditing(false);
        setLoading(true);

        (async () => {
            try {
                const streamUrl = await getPreviewUrl(fileId, publicToken);
                const response = await fetch(streamUrl);
                if (!response.ok) throw new Error(`Could not read this file (${response.status}).`);
                const value = await response.text();
                if (value.length > 5_000_000) throw new Error("This text file is larger than the 5 MB preview limit.");
                if (!cancelled) {
                    setContent(value);
                    setDraft(value);
                }
            } catch (err) {
                if (!cancelled) {
                    const message = err instanceof Error ? err.message : "Could not read this file.";
                    setError(message);
                    if (message.includes("session has expired")) navigate("/");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [fileId, kind, navigate, publicToken]);

    const canEdit = Boolean(fileId && !publicToken);

    const beginEditing = useCallback(() => {
        setDraft(content);
        setSaveError("");
        setSaveMessage("");
        setEditing(true);
    }, [content]);

    const cancelEditing = useCallback(() => {
        setDraft(content);
        setSaveError("");
        setEditing(false);
    }, [content]);

    const saveContent = useCallback(async () => {
        if (!canEdit || saving) return;
        const sessionToken = localStorage.getItem("token");
        if (!sessionToken) {
            setSaveError("Sign in again to save this file.");
            navigate("/");
            return;
        }

        if (new TextEncoder().encode(draft).byteLength > 5_000_000) {
            setSaveError("The edited file exceeds the 5 MB limit.");
            return;
        }

        setSaving(true);
        setSaveError("");
        setSaveMessage("");

        try {
            const response = await fetch(`${API_URL}/auth/file/${encodeURIComponent(fileId)}/content`, {
                method: "PUT",
                headers: {
                    Authorization: `Bearer ${sessionToken}`,
                    "Content-Type": "text/plain; charset=utf-8",
                },
                body: draft,
            });
            const data = await response.json().catch(() => ({}));

            if (response.status === 401) {
                if (localStorage.getItem("token") === sessionToken) {
                    endAuthSession(sessionToken);
                    navigate("/");
                }
                throw new Error("Your session has expired. Sign in again to save this file.");
            }
            if (!response.ok) throw new Error(data.detail || "Could not save this file.");

            setContent(draft);
            setEditing(false);
            setSaveMessage("Saved · storage links updated");
        } catch (err) {
            setSaveError(err instanceof Error ? err.message : "Could not save this file.");
        } finally {
            setSaving(false);
        }
    }, [canEdit, draft, fileId, navigate, saving]);

    useEffect(() => {
        onRegisterActions?.({
            canEdit: Boolean(canEdit && !loading && !error),
            editing,
            saving,
            saveError,
            onEdit: beginEditing,
            onCancel: cancelEditing,
            onSave: saveContent,
        });
    }, [onRegisterActions, canEdit, editing, saving, saveError, loading, error, beginEditing, cancelEditing, saveContent]);

    useEffect(() => () => onRegisterActions?.(null), [onRegisterActions]);

    if (loading) return <PreviewMessage message="Loading file…"/>;
    if (error) return <PreviewMessage message={error}/>;

    return <div className="flex h-full min-h-0 w-full flex-col bg-[#111111]">
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-[#2e2e2e] bg-[#171717] px-4 py-2.5">
            <div className="min-w-0 text-xs text-[#898989]">
                <span>{editing ? "Editing source" : kind === "markdown" ? "Markdown preview" : "Plain text preview"}</span>
                <span className="ml-3">{(editing ? draft : content).length.toLocaleString()} characters</span>
            </div>
            {saveMessage && !editing && <span role="status" className="text-xs text-[#7ed6a8]">{saveMessage}</span>}
        </header>

        {editing ? <textarea
            aria-label="Edit file content"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            autoCorrect="off"
            className="min-h-0 w-full flex-1 resize-none overflow-auto bg-[#111111] p-4 font-mono text-sm leading-6 text-[#d4d4d4] outline-none focus:ring-1 focus:ring-inset focus:ring-[#3ecf8e]/40 sm:p-6"
        /> : kind === "text" ? <div className="min-h-0 w-full flex-1 overflow-y-auto overflow-x-auto overscroll-contain bg-[#111111] p-4 sm:p-6"
                                  style={{WebkitOverflowScrolling: "touch", scrollbarGutter: "stable"}}>
            <div className="mx-auto min-h-full w-max min-w-full max-w-6xl rounded-xl border border-[#2e2e2e] bg-[#171717]">
                <pre
                    tabIndex={0}
                    aria-label="File contents"
                    onKeyDown={selectAllPreviewContent}
                    className="overflow-visible whitespace-pre p-4 text-sm leading-6 text-[#d4d4d4] outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[#3ecf8e]/40"
                ><code>{content}</code></pre>
            </div>
        </div> : <div className="min-h-0 w-full flex-1 overflow-hidden">
            <MarkdownPreview content={content}/>
        </div>}
    </div>;
}

/**
 * Render common Markdown blocks without injecting HTML from the uploaded file.
 * @param {{content: string}} props
 */
function MarkdownPreview({content}) {
    const lines = content.split(/\r?\n/);
    const blocks = [];
    let index = 0;
    const inline = (value) => value.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|_[^_]+_|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
        if (part.startsWith("`") && part.endsWith("`")) return <code key={i}
                                                                     className="rounded bg-[#252525] px-1.5 py-0.5 font-mono text-[#a7e3c6]">{part.slice(1, -1)}</code>;
        if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) return <strong
            key={i}>{part.slice(2, -2)}</strong>;
        if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) return <em key={i}>{part.slice(1, -1)}</em>;
        const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
        if (link) return <a key={i} href={link[2]} target="_blank" rel="noreferrer"
                            className="text-[#3ecf8e] underline underline-offset-2">{link[1]}</a>;
        return part;
    });

    while (index < lines.length) {
        const line = lines[index];
        if (!line.trim()) {
            index++;
            continue;
        }
        if (line.startsWith("```") || line.startsWith("~~~")) {
            const marker = line.slice(0, 3);
            const code = [];
            index++;
            while (index < lines.length && !lines[index].startsWith(marker)) code.push(lines[index++]);
            if (index < lines.length) index++;
            blocks.push(<pre key={`code-${index}`}
                             className="overflow-x-auto rounded-lg border border-[#2e2e2e] bg-[#111111] p-4 text-sm leading-6"><code>{code.join("\n")}</code></pre>);
            continue;
        }
        const heading = line.match(/^(#{1,6})\s+(.+)$/);
        if (heading) {
            const level = heading[1].length;
            const sizes = ["", "text-3xl", "text-2xl", "text-xl", "text-lg", "text-base", "text-sm"];
            blocks.push(<h2 key={`heading-${index}`} className={`${sizes[level]} font-semibold text-[#f5f5f5]`}>{inline(heading[2])}</h2>);
            index++;
            continue;
        }
        if (/^\s*(---+|___+|\*\*\*+)\s*$/.test(line)) {
            blocks.push(<hr key={`rule-${index}`} className="border-[#3a3a3a]"/>);
            index++;
            continue;
        }
        if (/^\s*>\s?/.test(line)) {
            const quote = [];
            while (index < lines.length && /^\s*>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
            blocks.push(<blockquote key={`quote-${index}`} className="border-l-2 border-[#3ecf8e] pl-4 text-[#bdbdbd]">{quote.map((item, i) => <p
                key={i}>{inline(item)}</p>)}</blockquote>);
            continue;
        }
        const firstList = line.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
        if (firstList) {
            const ordered = /^\d/.test(firstList[1]);
            const items = [];
            while (index < lines.length) {
                const match = lines[index].match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
                if (!match || /^\d/.test(match[1]) !== ordered) break;
                items.push(match[2]);
                index++;
            }
            const List = ordered ? "ol" : "ul";
            blocks.push(<List key={`list-${index}`} className={`${ordered ? "list-decimal" : "list-disc"} pl-6`}>{items.map((item, i) => <li
                key={i}>{inline(item)}</li>)}</List>);
            continue;
        }
        const paragraph = [line];
        index++;
        while (index < lines.length && lines[index].trim() && !/^(#{1,6})\s/.test(lines[index]) && !lines[index].startsWith("```") && !lines[index].startsWith("~~~") && !/^\s*(>|[-*+]|\d+\.)\s/.test(lines[index])) paragraph.push(lines[index++]);
        blocks.push(<p key={`paragraph-${index}`} className="leading-7 text-[#d4d4d4]">{paragraph.map((part, i) => <span key={i}>{i > 0 &&
            <br/>}{inline(part)}</span>)}</p>);
    }

    return <article
        tabIndex={0}
        aria-label="Markdown file contents"
        onKeyDown={selectAllPreviewContent}
        className="h-full min-h-0 w-full overflow-y-auto overflow-x-auto overscroll-contain bg-[#151515] px-5 py-8 outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[#3ecf8e]/40 sm:px-10"
        style={{WebkitOverflowScrolling: "touch", scrollbarGutter: "stable"}}
    >
        <div
            className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-4 rounded-xl border border-[#2e2e2e] bg-[#1b1b1b] p-5 sm:p-8">{blocks}</div>
    </article>;
}

/**
 * Render PDFs with PDF.js so the viewer controls and page rendering stay inside StoreLimitless.
 * The document is loaded through the existing signed URL and supports the backend's byte ranges.
 * @param {{streamUrl: string, fileName?: string}} props
 */
function PDFDocumentViewer({streamUrl, fileName}) {
    const [documentProxy, setDocumentProxy] = useState(null);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState("");
    const [pageError, setPageError] = useState("");
    const [pageCount, setPageCount] = useState(0);
    const [pageNumber, setPageNumber] = useState(1);
    const [pageInput, setPageInput] = useState("1");
    const [zoom, setZoom] = useState(1);
    const [fitToWidth, setFitToWidth] = useState(true);
    const [containerWidth, setContainerWidth] = useState(0);
    const [rendering, setRendering] = useState(false);
    const containerRef = useRef(null);
    const canvasRef = useRef(null);
    const documentRef = useRef(null);
    const renderTaskRef = useRef(null);
    const renderedScaleRef = useRef(1);

    useEffect(() => {
        let cancelled = false;
        let loadingTask = null;
        setDocumentProxy(null);
        setLoading(true);
        setLoadError("");
        setPageError("");
        setPageCount(0);
        setPageNumber(1);
        setPageInput("1");

        try {
            loadingTask = pdfjsLib.getDocument({
                url: streamUrl,
                rangeChunkSize: 256 * 1024,
                disableAutoFetch: false,
                disableStream: false,
            });

            loadingTask.promise.then((loadedDocument) => {
                if (cancelled) {
                    void loadedDocument.destroy();
                    return;
                }
                documentRef.current = loadedDocument;
                setPageCount(loadedDocument.numPages);
                setDocumentProxy(loadedDocument);
                setLoading(false);
            }).catch((error) => {
                if (cancelled) return;
                const message = error && error.name === "PasswordException"
                    ? "This PDF is password-protected and cannot be opened without its password."
                    : (error && error.message) || "The PDF may be damaged or use an unsupported feature.";
                setLoadError(message);
                setLoading(false);
            });
        } catch (error) {
            setLoadError(error instanceof Error ? error.message : "Could not start the PDF viewer.");
            setLoading(false);
        }

        return () => {
            cancelled = true;
            if (renderTaskRef.current) {
                try {
                    renderTaskRef.current.cancel();
                } catch {
                    // A finished render task does not need further cancellation.
                }
                renderTaskRef.current = null;
            }
            const loadedDocument = documentRef.current;
            documentRef.current = null;
            if (loadedDocument) void loadedDocument.destroy();
            else if (loadingTask) void loadingTask.destroy();
        };
    }, [streamUrl]);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return undefined;

        const updateWidth = () => setContainerWidth(container.clientWidth);
        updateWidth();

        if (typeof ResizeObserver !== "undefined") {
            const observer = new ResizeObserver(updateWidth);
            observer.observe(container);
            return () => observer.disconnect();
        }

        window.addEventListener("resize", updateWidth);
        return () => window.removeEventListener("resize", updateWidth);
    }, []);

    useEffect(() => {
        setPageInput(String(pageNumber));
        if (containerRef.current) containerRef.current.scrollTo({top: 0, behavior: "auto"});
    }, [pageNumber]);

    useEffect(() => {
        if (!documentProxy || !canvasRef.current) return undefined;

        let cancelled = false;
        let activeRenderTask = null;
        setRendering(true);
        setPageError("");

        (async () => {
            try {
                const page = await documentProxy.getPage(pageNumber);
                if (cancelled) return;

                const baseViewport = page.getViewport({scale: 1});
                const fittedScale = containerWidth > 0
                    ? Math.min(2.5, Math.max(0.25, (containerWidth - 48) / baseViewport.width))
                    : 1;
                const scale = fitToWidth ? fittedScale : zoom;
                const viewport = page.getViewport({scale});
                renderedScaleRef.current = scale;
                const canvas = canvasRef.current;
                const context = canvas && canvas.getContext("2d", {alpha: false});
                if (!canvas || !context) throw new Error("Your browser could not create a PDF rendering surface.");

                const outputScale = Math.min(window.devicePixelRatio || 1, 2);
                canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
                canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
                canvas.style.width = Math.floor(viewport.width) + "px";
                canvas.style.height = Math.floor(viewport.height) + "px";

                activeRenderTask = page.render({
                    canvasContext: context,
                    viewport,
                    transform: outputScale !== 1
                        ? [outputScale, 0, 0, outputScale, 0, 0]
                        : null,
                });
                renderTaskRef.current = activeRenderTask;
                await activeRenderTask.promise;
            } catch (error) {
                if (!cancelled && (!error || error.name !== "RenderingCancelledException")) {
                    setPageError((error && error.message) || "Could not render this PDF page.");
                }
            } finally {
                if (!cancelled) setRendering(false);
                if (renderTaskRef.current === activeRenderTask) renderTaskRef.current = null;
            }
        })();

        return () => {
            cancelled = true;
            if (activeRenderTask) {
                try {
                    activeRenderTask.cancel();
                } catch {
                    // The task may already have completed.
                }
            }
        };
    }, [documentProxy, pageNumber, zoom, fitToWidth, containerWidth]);

    const goToPage = useCallback((value) => {
        const parsed = Number.parseInt(String(value), 10);
        if (!Number.isFinite(parsed) || pageCount < 1) {
            setPageInput(String(pageNumber));
            return;
        }
        const nextPage = Math.max(1, Math.min(pageCount, parsed));
        setPageNumber(nextPage);
        setPageInput(String(nextPage));
    }, [pageCount, pageNumber]);

    const changeZoom = useCallback((amount) => {
        setFitToWidth(false);
        setZoom((current) => {
            const base = fitToWidth ? renderedScaleRef.current : current;
            return Math.max(0.5, Math.min(3, Math.round((base + amount) * 100) / 100));
        });
    }, [fitToWidth]);

    const buttonClass = "inline-flex h-8 min-w-8 items-center justify-center gap-1 rounded-md border border-[#3a493f] px-2 text-xs text-[#e7eee9] transition-colors hover:border-[#3ecf8e]/50 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-35 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]";
    const iconButtonClass = "inline-flex h-8 w-8 items-center justify-center rounded-md border border-[#3a493f] text-[#e7eee9] transition-colors hover:border-[#3ecf8e]/50 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-35 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#3ecf8e]";

    return <div className="flex h-full min-h-0 w-full flex-col bg-[#111111]">
        <div className="z-10 flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-[#2e3a32] bg-[#171d19] px-3 py-2">
            <div className="flex min-w-0 items-center gap-1.5">
                <button type="button" className={iconButtonClass} onClick={() => goToPage(pageNumber - 1)}
                        disabled={loading || pageNumber <= 1} aria-label="Previous page" title="Previous page">
                    <span className="material-symbols-outlined text-[19px]" aria-hidden="true">chevron_left</span>
                </button>
                <input
                    type="number"
                    min="1"
                    max={pageCount || 1}
                    value={pageInput}
                    onChange={(event) => setPageInput(event.target.value)}
                    onBlur={() => goToPage(pageInput)}
                    onKeyDown={(event) => {
                        if (event.key === "Enter") {
                            goToPage(pageInput);
                            event.currentTarget.blur();
                        }
                    }}
                    disabled={loading || Boolean(loadError)}
                    aria-label="Current PDF page"
                    className="h-8 w-14 rounded-md border border-[#3a493f] bg-[#101410] px-1 text-center text-xs tabular-nums text-white outline-none focus:border-[#3ecf8e]"
                />
                <span className="whitespace-nowrap text-xs text-[#9aa79e]">of {pageCount || "—"}</span>
                <button type="button" className={iconButtonClass} onClick={() => goToPage(pageNumber + 1)}
                        disabled={loading || pageNumber >= pageCount} aria-label="Next page" title="Next page">
                    <span className="material-symbols-outlined text-[19px]" aria-hidden="true">chevron_right</span>
                </button>
            </div>

            <div className="flex items-center gap-1.5">
                <button type="button" className={iconButtonClass} onClick={() => changeZoom(-0.15)}
                        disabled={loading || Boolean(loadError) || (!fitToWidth && zoom <= 0.5)} aria-label="Zoom out" title="Zoom out">
                    <span className="material-symbols-outlined text-[18px]" aria-hidden="true">remove</span>
                </button>
                <span className="min-w-12 text-center text-xs tabular-nums text-[#c5d0c8]">
                    {fitToWidth ? "Fit width" : Math.round(zoom * 100) + "%"}
                </span>
                <button type="button" className={iconButtonClass} onClick={() => changeZoom(0.15)}
                        disabled={loading || Boolean(loadError) || (!fitToWidth && zoom >= 3)} aria-label="Zoom in" title="Zoom in">
                    <span className="material-symbols-outlined text-[18px]" aria-hidden="true">add</span>
                </button>
                <button type="button" className={buttonClass} onClick={() => setFitToWidth(true)}
                        disabled={loading || Boolean(loadError) || fitToWidth} title="Fit page to viewer width">
                    <span className="material-symbols-outlined text-[16px]" aria-hidden="true">fit_screen</span>
                    <span className="hidden sm:inline">Fit width</span>
                </button>
            </div>
        </div>

        <div ref={containerRef} className="relative min-h-0 flex-1 overflow-auto bg-[#0d100e] px-3 py-4 sm:px-6 sm:py-6">
            {loading && <PreviewMessage message="Loading PDF…"/>}
            {!loading && loadError && <PreviewMessage message={loadError}/>}
            {!loading && !loadError && <>
                <div className="mx-auto flex w-max max-w-full justify-center">
                    <canvas
                        ref={canvasRef}
                        role="img"
                        aria-label={(fileName || "PDF") + " page " + pageNumber + " of " + pageCount}
                        className="block max-w-full bg-white shadow-[0_8px_32px_rgba(0,0,0,0.45)]"
                    />
                </div>
                {rendering && <div className="pointer-events-none absolute right-4 top-4 flex items-center gap-2 rounded-md border border-[#3a493f] bg-[#171d19]/95 px-3 py-2 text-xs text-[#b5c1b9] shadow-lg">
                    <span className="material-symbols-outlined animate-spin text-[15px]" aria-hidden="true">progress_activity</span>
                    Rendering page…
                </div>}
                {pageError && <div role="alert" className="mx-auto mt-4 max-w-xl rounded-lg border border-[#784448] bg-[#301d20] px-4 py-3 text-sm text-[#ffb8bc]">{pageError}</div>}
            </>}
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-[#2e3a32] bg-[#171d19] px-3 py-1.5 text-[11px] text-[#849188]">
            <span className="min-w-0 truncate" title={fileName}>{fileName || "PDF document"}</span>
            <span className="shrink-0">{pageCount ? pageCount.toLocaleString() + " pages" : "PDF viewer"}</span>
        </div>
    </div>;
}

/**
 * Format media playback time without showing invalid or infinite duration values.
 * @param {number} seconds
 */
function formatMediaTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.floor(seconds % 60).toString().padStart(2, "0");
    const hours = Math.floor(minutes / 60);
    return hours > 0
        ? `${hours}:${(minutes % 60).toString().padStart(2, "0")}:${remainder}`
        : `${minutes}:${remainder}`;
}

/**
 * Clamp image zoom to a useful range shared by all image interactions.
 * @param {number} value
 */
function clampPreviewZoom(value) {
    return Math.max(0.25, Math.min(5, value));
}

/**
 * Image preview with wheel zoom, touch pinch zoom, and a small themed zoom toolbar.
 * The image is sized in layout (rather than only transformed) so the viewer can scroll
 * to every edge after zooming in.
 */
function ImageFilePreview({src, fileName, onError}) {
    const viewportRef = useRef(null);
    const pointersRef = useRef(new Map());
    const pinchRef = useRef(null);
    const dragRef = useRef(null);
    const [loaded, setLoaded] = useState(false);
    const [zoom, setZoom] = useState(1);
    const [isPanning, setIsPanning] = useState(false);
    const [naturalSize, setNaturalSize] = useState(null);
    const [viewportSize, setViewportSize] = useState({width: 0, height: 0});

    useEffect(() => {
        setLoaded(false);
        setZoom(1);
        setNaturalSize(null);
        pointersRef.current.clear();
        pinchRef.current = null;
        dragRef.current = null;
        setIsPanning(false);
    }, [src]);

    useEffect(() => {
        const element = viewportRef.current;
        if (!element) return undefined;
        const updateSize = () => setViewportSize({
            width: element.clientWidth,
            height: element.clientHeight,
        });
        updateSize();

        if (typeof ResizeObserver !== "undefined") {
            const observer = new ResizeObserver(updateSize);
            observer.observe(element);
            return () => observer.disconnect();
        }
        window.addEventListener("resize", updateSize);
        return () => window.removeEventListener("resize", updateSize);
    }, []);

    const handleWheel = useCallback((event) => {
        event.preventDefault();
        const factor = Math.exp(-event.deltaY * 0.0015);
        setZoom((current) => clampPreviewZoom(current * factor));
    }, []);

    const beginPan = useCallback((event) => {
        const viewport = viewportRef.current;
        if (!viewport) return;
        dragRef.current = {
            pointerId: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            scrollLeft: viewport.scrollLeft,
            scrollTop: viewport.scrollTop,
        };
        setIsPanning(true);
    }, []);

    const handlePointerDown = useCallback((event) => {
        if (event.target instanceof Element && event.target.closest("button")) return;
        if (event.pointerType === "mouse") {
            if (event.button !== 0 || zoom <= 1) return;
            event.preventDefault();
            beginPan(event);
            try {
                event.currentTarget.setPointerCapture(event.pointerId);
            } catch {
                // Pointer capture is not available in every embedded browser.
            }
            return;
        }
        if (event.pointerType !== "touch") return;

        const pointers = pointersRef.current;
        pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
        try {
            event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
            // Pointer capture is not available in every embedded browser.
        }

        if (pointers.size === 1) {
            // A one-finger touch drags the image viewport; two fingers switch to pinch zoom.
            beginPan(event);
        } else if (pointers.size === 2) {
            dragRef.current = null;
            setIsPanning(false);
            const [{x: x1, y: y1}, {x: x2, y: y2}] = Array.from(pointers.values());
            pinchRef.current = {
                distance: Math.max(1, Math.hypot(x2 - x1, y2 - y1)),
                zoom,
            };
        }
    }, [beginPan, zoom]);

    const handlePointerMove = useCallback((event) => {
        const viewport = viewportRef.current;
        if (!viewport) return;

        if (event.pointerType === "touch") {
            const pointers = pointersRef.current;
            if (!pointers.has(event.pointerId)) return;
            pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});

            if (pointers.size >= 2 && pinchRef.current) {
                const [{x: x1, y: y1}, {x: x2, y: y2}] = Array.from(pointers.values());
                const distance = Math.max(1, Math.hypot(x2 - x1, y2 - y1));
                const nextZoom = pinchRef.current.zoom * (distance / pinchRef.current.distance);
                setZoom(clampPreviewZoom(nextZoom));
                return;
            }
        }

        const drag = dragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        if (event.pointerType === "mouse") event.preventDefault();
        viewport.scrollLeft = drag.scrollLeft - (event.clientX - drag.x);
        viewport.scrollTop = drag.scrollTop - (event.clientY - drag.y);
    }, []);

    const finishPointer = useCallback((event) => {
        if (event.pointerType === "mouse") {
            if (dragRef.current?.pointerId === event.pointerId) {
                dragRef.current = null;
                setIsPanning(false);
            }
            return;
        }
        if (event.pointerType !== "touch") return;

        const pointers = pointersRef.current;
        pointers.delete(event.pointerId);
        pinchRef.current = null;

        if (pointers.size === 1) {
            const [pointerId, point] = Array.from(pointers.entries())[0];
            const viewport = viewportRef.current;
            dragRef.current = viewport ? {
                pointerId,
                x: point.x,
                y: point.y,
                scrollLeft: viewport.scrollLeft,
                scrollTop: viewport.scrollTop,
            } : null;
            setIsPanning(Boolean(dragRef.current));
        } else {
            dragRef.current = null;
            setIsPanning(false);
        }
    }, []);

    const fitScale = naturalSize && viewportSize.width > 0 && viewportSize.height > 0
        ? Math.min(
            1,
            Math.max(1, viewportSize.width - 48) / naturalSize.width,
            Math.max(1, viewportSize.height - 48) / naturalSize.height,
        )
        : 1;
    const imageWidth = naturalSize ? Math.max(1, naturalSize.width * fitScale * zoom) : undefined;
    const imageHeight = naturalSize ? Math.max(1, naturalSize.height * fitScale * zoom) : undefined;

    return <div
        ref={viewportRef}
        className={`relative flex h-full min-h-0 w-full overflow-auto bg-[#0d100e] ${isPanning ? "cursor-grabbing" : zoom > 1 ? "cursor-grab" : "cursor-default"}`}
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointer}
        onPointerCancel={finishPointer}
        style={{touchAction: "none", overscrollBehavior: "contain", WebkitOverflowScrolling: "touch"}}
        aria-label="Image viewer. Scroll to zoom, or pinch on a touch screen."
    >
        <div className="flex h-max min-h-full w-max min-w-full items-center justify-center p-3 sm:p-6">
            {!loaded && <div className="absolute inset-0 z-10 pointer-events-none flex items-center justify-center">
                <PreviewMessage message="Loading image…"/>
            </div>}
            <img
                src={src}
                alt={fileName || "Image preview"}
                draggable={false}
                onLoad={(event) => {
                    setNaturalSize({width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight});
                    setLoaded(true);
                }}
                onError={onError}
                style={{
                    width: imageWidth ? `${imageWidth}px` : "auto",
                    height: imageHeight ? `${imageHeight}px` : "auto",
                    maxWidth: "none",
                    maxHeight: "none",
                    opacity: loaded ? 1 : 0,
                }}
                className="block shrink-0 select-none object-contain"
            />
        </div>

        {loaded && <div className="absolute right-3 top-3 z-20 flex items-center gap-1 rounded-lg border border-[#304236] bg-[#121a15]/95 p-1 text-[#e7eee9] shadow-lg backdrop-blur">
            <button type="button" onClick={() => setZoom((current) => clampPreviewZoom(current / 1.2))}
                    disabled={zoom <= 0.25} aria-label="Zoom out" title="Zoom out"
                    className="flex h-8 w-8 items-center justify-center rounded-md text-[#c5d0c8] transition-colors hover:bg-white/10 hover:text-white disabled:opacity-35">
                <span className="material-symbols-outlined text-[19px]" aria-hidden="true">remove</span>
            </button>
            <button type="button" onClick={() => setZoom(1)} aria-label="Reset image zoom" title="Reset zoom"
                    className="min-w-12 rounded-md px-1 text-xs tabular-nums hover:bg-white/10">
                {Math.round(zoom * 100)}%
            </button>
            <button type="button" onClick={() => setZoom((current) => clampPreviewZoom(current * 1.2))}
                    disabled={zoom >= 5} aria-label="Zoom in" title="Zoom in"
                    className="flex h-8 w-8 items-center justify-center rounded-md text-[#c5d0c8] transition-colors hover:bg-white/10 hover:text-white disabled:opacity-35">
                <span className="material-symbols-outlined text-[19px]" aria-hidden="true">add</span>
            </button>
        </div>}
    </div>;
}

/**
 * A custom StoreLimitless-themed audio player with keyboard-friendly controls.
 * The native audio element is retained for decoding and streaming, but its browser UI
 * is replaced by consistent in-app controls.
 */
function AudioFilePlayer({src, fileName, onError}) {
    const audioRef = useRef(null);
    const [playing, setPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [volume, setVolume] = useState(1);
    const [muted, setMuted] = useState(false);
    const [playbackRate, setPlaybackRate] = useState(1);

    const progress = duration > 0 ? Math.max(0, Math.min(100, (currentTime / duration) * 100)) : 0;

    const togglePlayback = useCallback(() => {
        const audio = audioRef.current;
        if (!audio) return;
        if (audio.paused) {
            void audio.play().catch(() => {});
        } else {
            audio.pause();
        }
    }, []);

    const handleSeek = useCallback((event) => {
        const audio = audioRef.current;
        if (!audio || !Number.isFinite(audio.duration)) return;
        const nextTime = Number(event.target.value);
        audio.currentTime = Math.max(0, Math.min(audio.duration, nextTime));
        setCurrentTime(audio.currentTime);
    }, []);

    const handleVolume = useCallback((event) => {
        const nextVolume = Math.max(0, Math.min(1, Number(event.target.value)));
        setVolume(nextVolume);
        setMuted(nextVolume === 0);
        if (audioRef.current) {
            audioRef.current.volume = nextVolume;
            audioRef.current.muted = nextVolume === 0;
        }
    }, []);

    const toggleMute = useCallback(() => {
        const audio = audioRef.current;
        const nextMuted = audio ? !audio.muted : !muted;
        if (audio) audio.muted = nextMuted;
        setMuted(nextMuted);
    }, [muted]);

    const changePlaybackRate = useCallback((event) => {
        const nextRate = Number(event.target.value);
        setPlaybackRate(nextRate);
        if (audioRef.current) audioRef.current.playbackRate = nextRate;
    }, []);

    return <div className="w-full max-w-3xl overflow-hidden rounded-2xl border border-[#29392f] bg-[#151c17] shadow-[0_16px_48px_rgba(0,0,0,0.3)]">
        <div className="flex items-center gap-4 border-b border-[#29392f] bg-gradient-to-r from-[#17231b] to-[#151c17] px-5 py-5 sm:px-7">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-[#3ecf8e]/25 bg-[#3ecf8e]/10 text-[#3ecf8e]">
                <span className="material-symbols-outlined text-[30px]" aria-hidden="true">graphic_eq</span>
            </div>
            <div className="min-w-0 flex-1">
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-[#70a88b]">Audio preview</p>
                <h2 className="break-all text-sm font-semibold leading-5 text-[#f3f7f4]">{fileName || "Audio file"}</h2>
                <p className="mt-1 text-xs text-[#89988e]">StoreLimitless media player</p>
            </div>
        </div>

        <div className="px-5 py-5 sm:px-7">
            <audio
                ref={audioRef}
                src={src}
                preload="metadata"
                onLoadedMetadata={(event) => {
                    const audio = event.currentTarget;
                    setDuration(Number.isFinite(audio.duration) ? audio.duration : 0);
                    audio.volume = volume;
                    audio.muted = muted;
                    audio.playbackRate = playbackRate;
                }}
                onDurationChange={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
                onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime || 0)}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={() => setPlaying(false)}
                onVolumeChange={(event) => {
                    setVolume(event.currentTarget.volume);
                    setMuted(event.currentTarget.muted);
                }}
                onError={onError}
                className="sr-only"
                aria-label={`Audio playback for ${fileName || "audio file"}`}
            />

            <div className="mb-2 flex items-center justify-between text-[11px] tabular-nums text-[#9ba99f]">
                <span>{formatMediaTime(currentTime)}</span>
                <span>{duration ? formatMediaTime(duration) : "--:--"}</span>
            </div>
            <input
                type="range"
                min="0"
                max={duration || 0}
                step="0.01"
                value={Math.min(currentTime, duration || 0)}
                onChange={handleSeek}
                disabled={!duration}
                aria-label="Audio progress"
                style={{background: `linear-gradient(to right, #3ecf8e ${progress}%, #344238 ${progress}%)`}}
                className="mb-5 h-1.5 w-full cursor-pointer appearance-none rounded-full accent-[#3ecf8e] disabled:cursor-wait"
            />

            <div className="flex flex-wrap items-center gap-3">
                <button type="button" onClick={togglePlayback} aria-label={playing ? "Pause audio" : "Play audio"}
                        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-[#168452] text-white shadow-[0_4px_18px_rgba(62,207,142,0.18)] transition-colors hover:bg-[#1b9a61] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e]">
                    <span className="material-symbols-outlined text-[26px]" aria-hidden="true">{playing ? "pause" : "play_arrow"}</span>
                </button>
                <span className="min-w-0 flex-1 text-xs font-medium text-[#c2cec6]">{playing ? "Now playing" : currentTime > 0 && currentTime < duration ? "Playback paused" : "Ready to play"}</span>

                <div className="flex items-center gap-2">
                    <button type="button" onClick={toggleMute} aria-label={muted || volume === 0 ? "Unmute audio" : "Mute audio"}
                            className="flex h-9 w-9 items-center justify-center rounded-lg text-[#aebbb2] transition-colors hover:bg-white/5 hover:text-white">
                        <span className="material-symbols-outlined text-[20px]" aria-hidden="true">{muted || volume === 0 ? "volume_off" : "volume_up"}</span>
                    </button>
                    <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={muted ? 0 : volume}
                        onChange={handleVolume}
                        aria-label="Audio volume"
                        className="w-20 cursor-pointer accent-[#3ecf8e]"
                    />
                </div>

                <label className="flex items-center gap-2 text-[11px] text-[#9ba99f]">
                    <span>Speed</span>
                    <select value={playbackRate} onChange={changePlaybackRate}
                            className="h-8 rounded-md border border-[#35473b] bg-[#101712] px-2 text-xs text-[#e7eee9] outline-none focus:border-[#3ecf8e]">
                        {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => <option key={rate} value={rate}>{rate}×</option>)}
                    </select>
                </label>
            </div>
        </div>
        <div className="border-t border-[#29392f] bg-[#111813] px-5 py-2.5 text-center text-[11px] text-[#849188] sm:px-7">
            Playback depends on the audio codec supported by your browser.
        </div>
    </div>;
}

/**
 * Render an image, in-app PDF, or audio preview from a signed stream URL.
 * Inactive persistent viewer instances release their preview resources and obtain a
 * fresh URL when reopened. This prevents a hidden PDF canvas from becoming stale.
 * @param {{fileId: string, navigate: Function, type: "image" | "pdf" | "audio", fileName?: string, active?: boolean}} props
 */
/**
 * Render an image, in-app PDF, or audio preview from a signed stream URL.
 * Image formats are displayed as contained media so the full photo remains visible.
 * @param {{fileId: string, navigate: Function, type: "image" | "pdf" | "audio", fileName?: string}} props
 */
export function MediaPreview({fileId, navigate, type, publicToken, fileName, active = true}) {
    const [streamUrl, setStreamUrl] = useState("");
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;
        setStreamUrl("");
        setError("");

        if (!active) return () => { cancelled = true; };

        (async () => {
            try {
                const url = await getPreviewUrl(fileId, publicToken);
                if (!cancelled) setStreamUrl(url);
            } catch (err) {
                if (!cancelled) {
                    const message = err instanceof Error ? err.message : "Could not open this file.";
                    setError(message);
                    if (message.includes("session has expired")) navigate("/");
                }
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [active, fileId, navigate, publicToken]);

    if (!active) return null;
    if (error) return <PreviewMessage message={error}/>;
    if (!streamUrl) return <PreviewMessage message="Preparing preview…"/>;

    if (type === "image") return <ImageFilePreview
        src={streamUrl}
        fileName={fileName}
        onError={() => setError("This image could not be displayed. Your browser may not support this image format.")}
    />;

    if (type === "pdf") return <PDFDocumentViewer streamUrl={streamUrl} fileName={fileName}/>;

    return <div className="flex h-full min-h-0 w-full items-center justify-center bg-[#0d100e] px-4 py-8 sm:px-6">
        <AudioFilePlayer
            src={streamUrl}
            fileName={fileName}
            onError={() => setError("This audio file could not be played. Your browser may not support its audio codec or container format.")}
        />
    </div>;
}
