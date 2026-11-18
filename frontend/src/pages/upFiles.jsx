import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSearchParams } from "react-router-dom";

const DATA_CENTERS = ["Discord", "Telegram"];

export default function UploadPage() {
    const navigate = useNavigate();
    const [file, setFile] = useState(null);
    const [dataCenter, setDataCenter] = useState("");
    const [progress, setProgress] = useState(0);
    const [status, setStatus] = useState("idle");
    const [dragOver, setDragOver] = useState(false);
    const [searchParams] = useSearchParams();

    const directory = searchParams.get("directory") || "";

    const handleUpload = async () => {
        if (!file || !dataCenter) return;
        setStatus("uploading");
        setProgress(0);

        const formData = new FormData();
        formData.append("file", file);
        formData.append("data_center", dataCenter);
        formData.append("directory", directory);

        try {
            const res = await fetch("http://127.0.0.1:8000/auth/upload", {
                method: "POST",
                headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
                body: formData,
            });
            if (!res.ok || !res.body) { setStatus("error"); return; }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value);
                for (let line of chunk.split("\n")) {
                    if (!line.trim()) continue;
                    try {
                        const data = JSON.parse(line);
                        setProgress((prev) => Math.max(prev, data.progress));
                    } catch {}
                }
            }
            setProgress(100);
            setStatus("done");
            setTimeout(() => navigate("/dashboard"), 1200);
        } catch {
            setStatus("error");
        }
    };

    const handleDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        const dropped = e.dataTransfer.files[0];
        if (dropped) setFile(dropped);
    };

    return (
        <div className="min-h-screen bg-background text-on-surface flex flex-col">
            {/* Top Bar */}
            <header className="h-16 border-b border-outline-variant/10 bg-surface/70 backdrop-blur-md flex items-center px-6 gap-4">
                <button
                    onClick={() => navigate("/dashboard")}
                    className="flex items-center gap-2 text-on-surface-variant hover:text-primary transition-colors text-sm"
                >
                    <span className="material-symbols-outlined text-[20px]">arrow_back</span>
                    Back to Dashboard
                </button>
                <div className="flex items-center gap-2 ml-4">
                    <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center">
                        <span className="material-symbols-outlined text-on-primary text-[16px]" style={{ fontVariationSettings: "'FILL' 1" }}>cloud</span>
                    </div>
                    <span className="font-bold text-primary">DriveBot</span>
                </div>
            </header>

            <div className="flex-1 flex items-center justify-center px-4 py-12">
                <div className="w-full max-w-lg">
                    <h1 className="text-2xl font-semibold text-on-surface mb-1">Upload File</h1>
                    <p className="text-sm text-on-surface-variant mb-8">Select a data center and file to upload.</p>

                    {/* Data Center */}
                    <div className="mb-6">
                        <p className="text-xs font-geist uppercase tracking-widest text-on-surface-variant mb-3">Select Data Center</p>
                        <div className="flex gap-3">
                            {DATA_CENTERS.map((dc) => (
                                <button
                                    key={dc}
                                    onClick={() => setDataCenter(dc)}
                                    className={`flex items-center gap-2 px-5 py-2.5 rounded-xl border text-sm font-medium transition-all ${
                                        dataCenter === dc
                                            ? "bg-primary/10 border-primary text-primary shadow-[0_0_12px_rgba(192,193,255,0.15)]"
                                            : "border-outline-variant/30 text-on-surface-variant hover:border-primary/50 hover:text-on-surface"
                                    }`}
                                >
                                    <span className="material-symbols-outlined text-[18px]">
                                        {dc === "Discord" ? "forum" : "send"}
                                    </span>
                                    {dc}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Drop Zone */}
                    <div className="mb-6">
                        <p className="text-xs font-geist uppercase tracking-widest text-on-surface-variant mb-3">Select File</p>
                        <label
                            htmlFor="file-input"
                            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                            onDragLeave={() => setDragOver(false)}
                            onDrop={handleDrop}
                            className={`flex flex-col items-center justify-center w-full h-40 rounded-2xl border-2 border-dashed cursor-pointer transition-all ${
                                dragOver
                                    ? "border-primary bg-primary/10"
                                    : file
                                    ? "border-primary/40 bg-primary/5"
                                    : "border-outline-variant/30 bg-surface-container-low hover:border-primary/40 hover:bg-surface-container"
                            }`}
                        >
                            <span className={`material-symbols-outlined text-[40px] mb-2 ${file ? "text-primary" : "text-on-surface-variant/40"}`}>
                                {file ? "check_circle" : "cloud_upload"}
                            </span>
                            {file ? (
                                <div className="text-center">
                                    <p className="text-sm font-medium text-primary">{file.name}</p>
                                    <p className="text-xs text-on-surface-variant mt-1">{(file.size / 1024).toFixed(1)} KB</p>
                                </div>
                            ) : (
                                <div className="text-center">
                                    <p className="text-sm text-on-surface-variant">Drop file here or <span className="text-primary">browse</span></p>
                                    <p className="text-xs text-on-surface-variant/50 mt-1">Any file type supported</p>
                                </div>
                            )}
                            <input
                                id="file-input"
                                type="file"
                                className="hidden"
                                disabled={status === "uploading"}
                                onChange={(e) => setFile(e.target.files[0])}
                            />
                        </label>
                    </div>

                    {/* Upload Button */}
                    <button
                        onClick={handleUpload}
                        disabled={!file || !dataCenter || status === "uploading"}
                        className="w-full bg-primary text-on-primary py-3 rounded-xl font-medium text-sm hover:bg-primary/90 transition-colors shadow-[0_0_20px_rgba(192,193,255,0.2)] disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                    >
                        {status === "uploading" ? (
                            <>
                                <span className="material-symbols-outlined text-[18px] animate-spin">autorenew</span>
                                Uploading...
                            </>
                        ) : (
                            <>
                                <span className="material-symbols-outlined text-[18px]">cloud_upload</span>
                                Upload File
                            </>
                        )}
                    </button>

                    {/* Progress */}
                    {status !== "idle" && (
                        <div className="mt-6 glass-panel rounded-2xl p-4 border border-outline-variant/10">
                            <div className="flex justify-between text-xs text-on-surface-variant mb-2">
                                <span className="font-geist uppercase tracking-widest">
                                    {status === "uploading" && "Uploading"}
                                    {status === "done" && "✓ Completed"}
                                    {status === "error" && "✗ Failed"}
                                </span>
                                <span>{progress}%</span>
                            </div>
                            <div className="w-full bg-surface-container-high h-1 rounded-full overflow-hidden">
                                <div
                                    className={`h-full rounded-full transition-all duration-300 ${status === "error" ? "bg-error" : "bg-primary"}`}
                                    style={{ width: `${progress}%` }}
                                />
                            </div>
                            {status === "done" && (
                                <p className="text-xs text-primary mt-2 text-center">Redirecting to dashboard...</p>
                            )}
                            {status === "error" && (
                                <p className="text-xs text-error mt-2 text-center">Upload failed. Please try again.</p>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}