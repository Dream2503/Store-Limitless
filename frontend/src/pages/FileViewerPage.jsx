import {useEffect, useState} from "react";
import {useNavigate, useParams, useSearchParams} from "react-router-dom";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

function getToken() {
    return localStorage.getItem("token");
}

export default function FileViewerPage() {
    const {fileId} = useParams();
    const [searchParams] = useSearchParams();
    const kind = searchParams.get("kind") || "video"; // "pdf" | "video" | "audio"
    const filename = searchParams.get("name") || "";
    const navigate = useNavigate();

    const [streamUrl, setStreamUrl] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        const token = getToken();
        if (!token) {
            navigate("/");
            return;
        }

        (async () => {
            try {
                const linkRes = await fetch(`${API_URL}/auth/files/${fileId}/public-link`, {
                    method: "POST",
                    headers: {Authorization: `Bearer ${token}`},
                });
                if (!linkRes.ok) throw new Error("Failed to prepare file.");
                const {url} = await linkRes.json();
                setStreamUrl(`${API_URL}${url}`);
            } catch (err) {
                setError(err.message || "Failed to load file.");
            } finally {
                setLoading(false);
            }
        })();
    }, [fileId, navigate]);

    return (
        <div className="flex h-screen flex-col bg-background text-on-surface">
            <header className="flex h-16 items-center gap-4 border-b border-outline-variant/10 bg-surface/70 px-6 backdrop-blur-md">
                <button onClick={() => navigate(-1)} className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-primary">
                    <span className="material-symbols-outlined text-[20px]" aria-hidden="true">arrow_back</span>
                    Back
                </button>
                <span className="truncate text-sm font-medium">{filename}</span>
            </header>

            <div className="flex flex-1 items-center justify-center overflow-auto p-4">
                {loading && (
                    <span className="material-symbols-outlined animate-spin text-[48px] text-on-surface-variant" aria-hidden="true">
                        progress_activity
                    </span>
                )}

                {!loading && error && <p className="text-error">{error}</p>}

                {!loading && !error && kind === "pdf" && (
                    <iframe src={streamUrl} title={filename} className="h-full w-full rounded-lg border-0 bg-white" />
                )}

                {!loading && !error && kind === "video" && (
                    <video src={streamUrl} controls autoPlay className="max-h-full max-w-full rounded-lg bg-black" />
                )}

                {!loading && !error && kind === "audio" && (
                    <audio src={streamUrl} controls autoPlay className="w-full max-w-lg" />
                )}
            </div>
        </div>
    );
}