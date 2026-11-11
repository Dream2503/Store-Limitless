import {useCallback, useEffect, useState} from "react";
import {API_URL} from "../../../api.js";
import {endAuthSession} from "../utils.js";

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
            canEdit,
            editing,
            saving,
            saveError,
            onEdit: beginEditing,
            onCancel: cancelEditing,
            onSave: saveContent,
        });
    }, [onRegisterActions, canEdit, editing, saving, saveError, beginEditing, cancelEditing, saveContent]);

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
 * Render the browser's native PDF or audio preview.
 * @param {{fileId: string, navigate: Function, type: "pdf" | "audio"}} props
 */
export function MediaPreview({fileId, navigate, type, publicToken}) {
    const [streamUrl, setStreamUrl] = useState("");
    const [error, setError] = useState("");
    useEffect(() => {
        let cancelled = false;
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
    }, [fileId, navigate, publicToken]);
    if (error) return <PreviewMessage message={error}/>;
    if (!streamUrl) return <PreviewMessage message="Preparing preview…"/>;
    if (type === "pdf") return <iframe title="PDF preview" src={streamUrl} className="h-full w-full bg-[#222]"/>;
    return <div className="flex h-full items-center justify-center px-6">
        <audio controls src={streamUrl} className="w-full max-w-2xl"/>
    </div>;
}
