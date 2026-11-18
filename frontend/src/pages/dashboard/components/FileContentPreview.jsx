import {useEffect, useState} from "react";
import {API_URL} from "../../../api.js";

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
    const response = await fetch(`${API_URL}/auth/file/${fileId}/public-link`, {
        method: "POST", headers: {Authorization: `Bearer ${token}`},
    });
    if (response.status === 401) {
        localStorage.removeItem("token");
        throw new Error("Your session has expired. Please sign in again.");
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not open this file.");
    if (!data.public_token) throw new Error("The server did not return a preview token.");
    return `${API_URL}/public/stream/${data.public_token}`;
}

/**
 * Render a plain text file or a small safe Markdown subset.
 * @param {{fileId: string, kind: "text" | "markdown", navigate: Function}} props
 */
export default function FileContentPreview({fileId, kind, navigate, publicToken}) {
    const [content, setContent] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;
        setContent("");
        setError("");
        setLoading(true);
        (async () => {
            try {
                const streamUrl = await getPreviewUrl(fileId, publicToken);
                const response = await fetch(streamUrl);
                if (!response.ok) throw new Error(`Could not read this file (${response.status}).`);
                const value = await response.text();
                if (value.length > 5_000_000) throw new Error("This text file is larger than the 5 MB preview limit.");
                if (!cancelled) setContent(value);
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

    if (loading) return <PreviewMessage message="Loading file…"/>;
    if (error) return <PreviewMessage message={error}/>;
    if (kind === "text") return <div className="h-full min-h-0 w-full overflow-y-auto overflow-x-auto overscroll-contain bg-[#111111] p-4 sm:p-6"
                                     style={{WebkitOverflowScrolling: "touch", scrollbarGutter: "stable"}}>
        <div className="mx-auto min-h-full w-max min-w-full max-w-6xl rounded-xl border border-[#2e2e2e] bg-[#171717]">
            <div className="flex items-center justify-between border-b border-[#2e2e2e] px-4 py-2 text-xs text-[#898989]">
                <span>Plain text preview</span><span>{content.length.toLocaleString()} characters</span>
            </div>
            <pre className="overflow-visible whitespace-pre p-4 text-sm leading-6 text-[#d4d4d4]"><code>{content}</code></pre>
        </div>
    </div>;

    return <MarkdownPreview content={content}/>;
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

    return <article className="h-full min-h-0 w-full overflow-y-auto overflow-x-auto overscroll-contain bg-[#151515] px-5 py-8 sm:px-10"
                    style={{WebkitOverflowScrolling: "touch", scrollbarGutter: "stable"}}>
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
