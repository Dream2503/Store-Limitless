import {useCallback, useEffect, useRef, useState} from "react";
import {useNavigate} from "react-router-dom";
import {API_URL} from "../../../api.js";
import {ThemeToggle} from "../../../ThemeContext.jsx";
import {endAuthSession, getToken} from "../utils.js";

const MAX_CLIPBOARD_LENGTH = 1_000_000;

function formatCreatedAt(value) {
    if (!value) return "Saved";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Saved";
    return date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

async function copyTextToClipboard(value) {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();

    if (!copied) throw new Error("Your browser did not allow clipboard access.");
}

/**
 * Account-synchronised plain-text clipboard. Entries are stored by the API,
 * not in browser storage, so the same signed-in account can access them
 * from another device.
 */
export default function ClipboardPage() {
    const navigate = useNavigate();
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [composerOpen, setComposerOpen] = useState(false);
    const [draft, setDraft] = useState("");
    const [saving, setSaving] = useState(false);
    const [deletingId, setDeletingId] = useState(null);
    const [copiedId, setCopiedId] = useState(null);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const textareaRef = useRef(null);
    const feedbackTimerRef = useRef(null);

    const loadClipboard = useCallback(async ({quiet = false} = {}) => {
        const token = getToken();
        if (!token) {
            navigate("/login", {replace: true});
            return;
        }

        if (quiet) setRefreshing(true);
        else setLoading(true);
        setError("");

        try {
            const response = await fetch(API_URL + "/auth/clipboard", {
                method: "GET",
                headers: {Authorization: "Bearer " + token, Accept: "application/json"},
                cache: "no-store",
            });
            const payload = await response.json().catch(() => null);

            if (response.status === 401) {
                endAuthSession(token);
                navigate("/login", {replace: true});
                return;
            }
            if (!response.ok) {
                throw new Error(payload?.detail || payload?.message || "Could not load clipboard (HTTP " + response.status + ").");
            }

            setItems(Array.isArray(payload?.items) ? payload.items : []);
        } catch (loadError) {
            setError(loadError instanceof Error ? loadError.message : "Could not load your clipboard.");
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [navigate]);

    useEffect(() => {
        void loadClipboard();
    }, [loadClipboard]);

    useEffect(() => () => {
        if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
    }, []);

    const openComposer = useCallback(() => {
        setDraft("");
        setError("");
        setNotice("");
        setComposerOpen(true);
    }, []);

    const handlePagePaste = useCallback((event) => {
        if (composerOpen) return;
        const target = event.target;
        if (target instanceof Element && target.closest("input, textarea, select, [contenteditable='true']")) return;

        const pastedText = event.clipboardData?.getData("text/plain");
        if (!pastedText) return;

        event.preventDefault();
        setDraft(pastedText.slice(0, MAX_CLIPBOARD_LENGTH));
        setError("");
        setNotice("");
        setComposerOpen(true);
    }, [composerOpen]);

    const saveClip = useCallback(async (event) => {
        event.preventDefault();
        const token = getToken();
        if (!token) {
            navigate("/login", {replace: true});
            return;
        }
        if (!draft.trim()) {
            setError("Paste or type some text before saving.");
            textareaRef.current?.focus();
            return;
        }
        if (draft.length > MAX_CLIPBOARD_LENGTH) {
            setError("Clipboard text cannot exceed 1 MB.");
            return;
        }

        setSaving(true);
        setError("");
        setNotice("");

        try {
            const response = await fetch(API_URL + "/auth/clipboard", {
                method: "POST",
                headers: {
                    Authorization: "Bearer " + token,
                    "Content-Type": "application/json",
                    Accept: "application/json",
                },
                body: JSON.stringify({content: draft}),
            });
            const payload = await response.json().catch(() => null);

            if (response.status === 401) {
                endAuthSession(token);
                navigate("/login", {replace: true});
                return;
            }
            if (!response.ok) {
                throw new Error(payload?.detail || payload?.message || "Could not save clipboard text (HTTP " + response.status + ").");
            }

            setItems((current) => [payload, ...current]);
            setComposerOpen(false);
            setDraft("");
            setNotice("Saved. This text is now available anywhere you sign in.");
        } catch (saveError) {
            setError(saveError instanceof Error ? saveError.message : "Could not save this clipboard entry.");
        } finally {
            setSaving(false);
        }
    }, [draft, navigate]);

    const copyClip = useCallback(async (item) => {
        try {
            await copyTextToClipboard(item.content);
            setCopiedId(item.id);
            setNotice("Text copied to this device's clipboard.");
            if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
            feedbackTimerRef.current = window.setTimeout(() => setCopiedId(null), 1800);
        } catch (copyError) {
            setError(copyError instanceof Error ? copyError.message : "Could not copy this text.");
        }
    }, []);

    const deleteClip = useCallback(async (item) => {
        const token = getToken();
        if (!token) {
            navigate("/login", {replace: true});
            return;
        }

        setDeletingId(item.id);
        setError("");
        setNotice("");
        try {
            const response = await fetch(API_URL + "/auth/clipboard/" + encodeURIComponent(item.id), {
                method: "DELETE",
                headers: {Authorization: "Bearer " + token, Accept: "application/json"},
            });
            const payload = await response.json().catch(() => null);

            if (response.status === 401) {
                endAuthSession(token);
                navigate("/login", {replace: true});
                return;
            }
            if (!response.ok) {
                throw new Error(payload?.detail || payload?.message || "Could not delete clipboard entry (HTTP " + response.status + ").");
            }

            setItems((current) => current.filter((entry) => entry.id !== item.id));
            setNotice("Clipboard entry deleted.");
        } catch (deleteError) {
            setError(deleteError instanceof Error ? deleteError.message : "Could not delete this clipboard entry.");
        } finally {
            setDeletingId(null);
        }
    }, [navigate]);

    return <div className="clipboard-page min-h-screen" onPaste={handlePagePaste}>
        <header className="clipboard-topbar sticky top-0 z-40 flex min-h-14 items-center justify-between gap-3 border-b px-4 py-2.5 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
                <button type="button" onClick={() => navigate("/home")}
                        className="clipboard-icon-button" aria-label="Back to files" title="Back to files">
                    <span className="material-symbols-outlined" aria-hidden="true">arrow_back</span>
                </button>
                <div className="flex min-w-0 items-center gap-2.5">
                    <div className="clipboard-brand-icon flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                        <span className="material-symbols-outlined text-[20px]" aria-hidden="true">content_paste</span>
                    </div>
                    <div className="min-w-0">
                        <h1 className="truncate text-sm font-semibold">Cross-device clipboard</h1>
                        <p className="clipboard-muted hidden text-[11px] sm:block">Your saved text, wherever you sign in</p>
                    </div>
                </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
                <button type="button" onClick={() => void loadClipboard({quiet: true})}
                        className="clipboard-icon-button" aria-label="Refresh clipboard" title="Refresh clipboard"
                        disabled={refreshing || loading}>
                    <span className={"material-symbols-outlined text-[19px]" + (refreshing ? " animate-spin" : "")} aria-hidden="true">refresh</span>
                </button>
                <ThemeToggle/>
            </div>
        </header>

        <main className="clipboard-main mx-auto w-full max-w-5xl px-4 py-7 sm:px-6 sm:py-10">
            <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
                <div>
                    <p className="clipboard-eyebrow mb-2">PERSONAL WORKSPACE</p>
                    <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Your clipboard</h2>
                    <p className="clipboard-muted mt-2 max-w-xl text-sm leading-6">
                        Save text here and pick it up from another device using the same account.
                    </p>
                </div>
                <button type="button" onClick={openComposer} className="clipboard-primary-button">
                    <span className="material-symbols-outlined text-[19px]" aria-hidden="true">add</span>
                    New text
                </button>
            </div>

            <div className="clipboard-hint mb-6 flex items-start gap-3 rounded-xl border px-4 py-3.5">
                <span className="material-symbols-outlined mt-0.5 text-[20px]" aria-hidden="true">sync</span>
                <div>
                    <p className="text-xs font-semibold">Synced to your account</p>
                    <p className="clipboard-muted mt-1 text-xs leading-5">
                        Press Ctrl+V anywhere on this page to paste text into a new entry, or choose New text. Refresh this page on your phone to see the latest clips.
                    </p>
                </div>
            </div>

            {notice && <div role="status" className="clipboard-notice mb-4 rounded-lg px-3 py-2.5 text-xs">{notice}</div>}
            {error && <div role="alert" className="clipboard-error mb-4 rounded-lg px-3 py-2.5 text-xs">{error}</div>}

            {composerOpen && <form onSubmit={saveClip} className="clipboard-composer mb-6 overflow-hidden rounded-xl border">
                <div className="clipboard-card-heading flex items-center justify-between gap-3 border-b px-4 py-3">
                    <div className="flex min-w-0 items-center gap-2">
                        <span className="material-symbols-outlined clipboard-accent" aria-hidden="true">edit_note</span>
                        <h3 className="text-sm font-semibold">New clipboard text</h3>
                    </div>
                    <span className="clipboard-muted text-[10px] tabular-nums">{draft.length.toLocaleString()} / 1,000,000</span>
                </div>
                <div className="p-4">
                    <textarea
                        ref={textareaRef}
                        autoFocus
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onKeyDown={(event) => {
                            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                                event.preventDefault();
                                event.currentTarget.form?.requestSubmit();
                            }
                        }}
                        maxLength={MAX_CLIPBOARD_LENGTH}
                        rows={7}
                        placeholder="Paste or type your text here…"
                        aria-label="Clipboard text"
                        className="clipboard-textarea w-full resize-y rounded-lg border px-3 py-3 text-sm leading-6 outline-none"
                    />
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                        <p className="clipboard-muted text-[11px]">Ctrl+Enter to save · Text up to 1 MB</p>
                        <div className="flex items-center gap-2">
                            <button type="button" onClick={() => {setComposerOpen(false); setDraft(""); setError("");}}
                                    disabled={saving} className="clipboard-secondary-button">Cancel</button>
                            <button type="submit" disabled={saving || !draft.trim()} className="clipboard-primary-button">
                                <span className="material-symbols-outlined text-[17px]" aria-hidden="true">{saving ? "progress_activity" : "save"}</span>
                                {saving ? "Saving…" : "Save text"}
                            </button>
                        </div>
                    </div>
                </div>
            </form>}

            <div className="mb-3 flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold">Saved entries</h3>
                <span className="clipboard-muted text-[11px]">{items.length.toLocaleString()} {items.length === 1 ? "entry" : "entries"}</span>
            </div>

            {loading ? (
                <div className="clipboard-empty-state rounded-xl border px-5 py-12 text-center">
                    <span className="material-symbols-outlined mb-3 animate-spin text-[28px]" aria-hidden="true">progress_activity</span>
                    <p className="text-sm font-medium">Loading your clipboard…</p>
                </div>
            ) : items.length === 0 ? (
                <div className="clipboard-empty-state rounded-xl border px-5 py-14 text-center">
                    <div className="clipboard-empty-icon mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl">
                        <span className="material-symbols-outlined text-[28px]" aria-hidden="true">content_paste</span>
                    </div>
                    <p className="text-sm font-semibold">Your clipboard is empty</p>
                    <p className="clipboard-muted mx-auto mt-2 max-w-md text-xs leading-5">
                        Create your first text entry, paste something with Ctrl+V, and it will be waiting for you on your other devices.
                    </p>
                    <button type="button" onClick={openComposer} className="clipboard-secondary-button mt-5">
                        <span className="material-symbols-outlined text-[17px]" aria-hidden="true">add</span>
                        Add your first text
                    </button>
                </div>
            ) : (
                <div className="space-y-3">
                    {items.map((item) => <article key={item.id} className="clipboard-entry overflow-hidden rounded-xl border">
                        <div className="clipboard-card-heading flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
                            <div className="flex min-w-0 items-center gap-2">
                                <span className="material-symbols-outlined clipboard-accent text-[18px]" aria-hidden="true">description</span>
                                <span className="clipboard-muted text-[11px]">{formatCreatedAt(item.created_at)}</span>
                            </div>
                            <div className="flex items-center gap-2">
                                <button type="button" onClick={() => void copyClip(item)}
                                        className="clipboard-entry-copy" aria-label="Copy entire text" title="Copy entire text">
                                    <span className="material-symbols-outlined text-[16px]" aria-hidden="true">{copiedId === item.id ? "check" : "content_copy"}</span>
                                    <span>{copiedId === item.id ? "Copied" : "Copy"}</span>
                                </button>
                                <button type="button" onClick={() => void deleteClip(item)}
                                        disabled={deletingId === item.id}
                                        className="clipboard-entry-delete" aria-label="Delete clipboard entry" title="Delete entry">
                                    <span className="material-symbols-outlined text-[17px]" aria-hidden="true">{deletingId === item.id ? "progress_activity" : "delete"}</span>
                                    <span className="hidden sm:inline">{deletingId === item.id ? "Deleting…" : "Delete"}</span>
                                </button>
                            </div>
                        </div>
                        <pre className="clipboard-entry-content whitespace-pre-wrap break-words px-4 py-4 text-sm leading-6">{item.content}</pre>
                    </article>)}
                </div>
            )}
        </main>
    </div>;
}
