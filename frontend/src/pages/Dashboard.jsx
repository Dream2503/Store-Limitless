import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const NAV_ITEMS = [
    { icon: "folder_open", label: "All Files", active: true },
    { icon: "schedule", label: "Recent" },
    { icon: "group", label: "Shared" },
    { icon: "delete", label: "Trash" },
];

const FILE_ICONS = {
    pdf: { icon: "picture_as_pdf", color: "text-error/70" },
    jpg: { icon: "image", color: "text-secondary/70" },
    jpeg: { icon: "image", color: "text-secondary/70" },
    png: { icon: "image", color: "text-secondary/70" },
    txt: { icon: "subject", color: "text-outline/70" },
    zip: { icon: "folder_zip", color: "text-tertiary/70" },
    mp4: { icon: "movie", color: "text-primary/70" },
    default: { icon: "draft", color: "text-on-surface-variant/70" },
};

function getFileIcon(fname) {
    const ext = fname?.split(".").pop()?.toLowerCase();
    return FILE_ICONS[ext] || FILE_ICONS.default;
}

function formatDate(dateStr) {
    if (!dateStr) return "—";
    return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export default function DashboardPage() {
    const navigate = useNavigate();
    const [files, setFiles] = useState([]);
    const [sidebarOpen, setSidebarOpen] = useState(false);

    const fetchFiles = async () => {
        try {
            const res = await fetch("http://127.0.0.1:8000/auth/files", {
                headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
            });
            const data = await res.json();
            setFiles(Array.isArray(data) ? data : []);
        } catch (err) {
            console.error("Failed to fetch files", err);
        }
    };

    useEffect(() => { fetchFiles(); }, []);

    const downloadFile = async (fid, fname) => {
        const res = await fetch(`http://127.0.0.1:8000/auth/download/${fid}`, {
            headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
        });
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fname;
        a.click();
        window.URL.revokeObjectURL(url);
    };

    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/");
    };

    const Sidebar = () => (
        <nav className="flex flex-col p-6 gap-4 h-screen w-72 backdrop-blur-xl bg-surface/70 border-r border-outline-variant/20 z-50">
            {/* Brand */}
            <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl bg-primary flex items-center justify-center">
                    <span className="material-symbols-outlined text-on-primary" style={{ fontVariationSettings: "'FILL' 1" }}>cloud</span>
                </div>
                <div>
                    <h1 className="text-lg font-bold text-primary">DriveBot</h1>
                    <p className="text-xs text-on-surface-variant font-geist tracking-widest uppercase">File Management</p>
                </div>
            </div>

            {/* Upload CTA */}
            <button
                onClick={() => navigate("/upload")}
                className="w-full bg-primary text-on-primary hover:bg-primary/90 rounded-xl py-2 px-4 flex items-center justify-center gap-2 text-sm font-medium transition-colors shadow-[0_0_15px_rgba(192,193,255,0.2)]"
            >
                <span className="material-symbols-outlined text-[18px]">add</span>
                Upload Files
            </button>

            {/* Nav */}
            <div className="flex-1 flex flex-col gap-1 mt-2">
                {NAV_ITEMS.map(({ icon, label, active }) => (
                    <a
                        key={label}
                        href="#"
                        className={`flex items-center gap-3 px-3 py-2 rounded-xl text-sm transition-all ${
                            active
                                ? "bg-primary-container/30 text-primary font-semibold"
                                : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"
                        }`}
                    >
                        <span className="material-symbols-outlined text-[20px]">{icon}</span>
                        {label}
                    </a>
                ))}
            </div>

            {/* Footer */}
            <div className="flex flex-col gap-1 border-t border-outline-variant/10 pt-4">
                <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors">
                    <span className="material-symbols-outlined text-[20px]">help_outline</span>
                    Help
                </a>
                <button
                    onClick={handleLogout}
                    className="flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-on-surface-variant hover:text-error hover:bg-error/10 transition-colors"
                >
                    <span className="material-symbols-outlined text-[20px]">logout</span>
                    Sign Out
                </button>
            </div>
        </nav>
    );

    return (
        <div className="flex h-screen bg-background text-on-surface overflow-hidden">
            {/* Sidebar desktop */}
            <div className="hidden md:flex fixed left-0 top-0 h-screen">
                <Sidebar />
            </div>

            {/* Sidebar mobile overlay */}
            {sidebarOpen && (
                <div className="md:hidden fixed inset-0 z-50 flex">
                    <div className="flex-shrink-0">
                        <Sidebar />
                    </div>
                    <div className="flex-1 bg-black/50" onClick={() => setSidebarOpen(false)} />
                </div>
            )}

            {/* Main */}
            <div className="flex-1 md:ml-72 flex flex-col h-screen">
                {/* Top Bar */}
                <header className="fixed top-0 right-0 w-full md:w-[calc(100%-288px)] h-16 z-40 backdrop-blur-md border-b border-outline-variant/10 bg-surface/70 flex items-center justify-between px-6">
                    <div className="flex items-center gap-4 w-full max-w-md">
                        <button
                            className="md:hidden text-on-surface-variant hover:text-primary transition-colors"
                            onClick={() => setSidebarOpen(true)}
                        >
                            <span className="material-symbols-outlined">menu</span>
                        </button>
                        <div className="relative w-full hidden sm:block">
                            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant" style={{ fontSize: 20 }}>search</span>
                            <input
                                className="w-full bg-surface-container-low border border-outline-variant/30 rounded-xl py-2 pl-10 pr-3 text-sm text-on-surface focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/50 transition-all placeholder:text-on-surface-variant/50"
                                placeholder="Search files..."
                                type="text"
                            />
                        </div>
                    </div>
                    <div className="flex items-center gap-3">
                        <div className="hidden lg:flex items-center gap-2 mr-3 border-r border-outline-variant/20 pr-4">
                            <div className="h-1.5 w-24 bg-surface-container-high rounded-full overflow-hidden">
                                <div className="h-full bg-primary w-3/4 rounded-full" />
                            </div>
                            <span className="text-xs text-on-surface-variant font-geist">Storage: 75%</span>
                        </div>
                        <button className="text-on-surface-variant hover:text-primary transition-colors p-2 rounded-full">
                            <span className="material-symbols-outlined">notifications</span>
                        </button>
                        <div className="w-8 h-8 rounded-full bg-primary/20 border border-primary/30 flex items-center justify-center">
                            <span className="material-symbols-outlined text-primary text-[18px]">person</span>
                        </div>
                    </div>
                </header>

                {/* Canvas */}
                <main className="flex-1 overflow-y-auto pt-24 px-4 md:px-8 pb-8 bg-gradient-to-br from-background to-surface-container-lowest">
                    {/* Header */}
                    <div className="flex flex-col sm:flex-row sm:items-end justify-between mb-8 gap-4">
                        <div>
                            <div className="flex items-center gap-1 text-on-surface-variant text-xs font-geist uppercase tracking-widest mb-1">
                                <span className="hover:text-primary cursor-pointer transition-colors">Home</span>
                                <span className="material-symbols-outlined text-[16px]">chevron_right</span>
                                <span className="text-on-surface">My Files</span>
                            </div>
                            <h2 className="text-2xl font-semibold text-on-surface tracking-tight">Your Files</h2>
                        </div>
                        <div className="flex gap-2">
                            <button className="bg-surface-container-high hover:bg-surface-container-highest text-on-surface border border-outline-variant/20 rounded-xl px-4 py-2 text-xs font-medium transition-colors flex items-center gap-1">
                                <span className="material-symbols-outlined text-[16px]">create_new_folder</span>
                                New Folder
                            </button>
                            <button
                                onClick={() => navigate("/upload")}
                                className="bg-primary hover:bg-primary/90 text-on-primary rounded-xl px-4 py-2 text-xs font-medium transition-colors shadow-[0_0_15px_rgba(192,193,255,0.15)] flex items-center gap-1"
                            >
                                <span className="material-symbols-outlined text-[16px]">cloud_upload</span>
                                Upload
                            </button>
                        </div>
                    </div>

                    {/* Storage Summary */}
                    <div className="glass-panel rounded-2xl p-4 mb-8 flex flex-col sm:flex-row items-center justify-between gap-4 border border-outline-variant/10 relative overflow-hidden">
                        <div className="absolute inset-0 bg-gradient-to-r from-primary/5 to-transparent pointer-events-none" />
                        <div className="relative z-10 flex items-center gap-4">
                            <div className="w-12 h-12 rounded-full bg-surface-container border border-outline-variant/20 flex items-center justify-center">
                                <svg className="w-7 h-7 text-primary" viewBox="0 0 36 36">
                                    <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="currentColor" strokeWidth="3" className="text-surface-container-highest opacity-30" />
                                    <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="currentColor" strokeDasharray="64, 100" strokeWidth="3" />
                                </svg>
                            </div>
                            <div>
                                <h3 className="font-semibold text-on-surface">Storage Status</h3>
                                <p className="text-sm text-on-surface-variant">3.2 GB of 5 GB used</p>
                            </div>
                        </div>
                        <button className="relative z-10 text-primary text-sm font-medium hover:underline underline-offset-4 decoration-primary/50">
                            Upgrade Plan
                        </button>
                    </div>

                    {/* Files Table */}
                    {files.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-24 text-on-surface-variant">
                            <span className="material-symbols-outlined text-[64px] mb-4 text-outline/40">cloud_off</span>
                            <p className="text-lg font-medium text-on-surface">No files yet</p>
                            <p className="text-sm mt-1">Upload your first file to get started.</p>
                            <button
                                onClick={() => navigate("/upload")}
                                className="mt-6 bg-primary text-on-primary px-5 py-2 rounded-xl text-sm font-medium hover:bg-primary/90 transition-colors shadow-[0_0_15px_rgba(192,193,255,0.2)]"
                            >
                                Upload File
                            </button>
                        </div>
                    ) : (
                        <div>
                            <h3 className="font-semibold text-on-surface mb-4 flex items-center gap-2">
                                <span className="material-symbols-outlined text-primary/70">description</span>
                                Recent Files
                            </h3>
                            <div className="bg-surface-container-lowest border border-outline-variant/10 rounded-2xl overflow-hidden glass-panel">
                                {/* Table Header */}
                                <div className="grid grid-cols-12 gap-2 p-3 px-4 border-b border-outline-variant/10 bg-surface-container-low/50">
                                    <div className="col-span-6 sm:col-span-5 text-xs font-geist uppercase tracking-widest text-on-surface-variant">Name</div>
                                    <div className="col-span-3 hidden sm:block text-xs font-geist uppercase tracking-widest text-on-surface-variant">Location</div>
                                    <div className="col-span-3 sm:col-span-2 text-xs font-geist uppercase tracking-widest text-on-surface-variant">Data Center</div>
                                    <div className="col-span-3 sm:col-span-2 text-right text-xs font-geist uppercase tracking-widest text-on-surface-variant">Actions</div>
                                </div>
                                {/* File Rows */}
                                {files.map((file, idx) => {
                                    const { icon, color } = getFileIcon(file.fname);
                                    return (
                                        <div
                                            key={file.fid}
                                            className={`grid grid-cols-12 gap-2 p-3 px-4 items-center group cursor-pointer hover:bg-surface-container-low/30 transition-colors ${idx < files.length - 1 ? "border-b border-outline-variant/5" : ""}`}
                                        >
                                            <div className="col-span-6 sm:col-span-5 flex items-center gap-3">
                                                <span className={`material-symbols-outlined text-[20px] ${color}`}>{icon}</span>
                                                <span className="text-sm text-on-surface truncate group-hover:text-primary transition-colors">{file.fname}</span>
                                            </div>
                                            <div className="col-span-3 hidden sm:block text-sm text-on-surface-variant truncate">
                                                {file.data_center || "—"}
                                            </div>
                                            <div className="col-span-3 sm:col-span-2">
                                                <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full font-geist">
                                                    {file.data_center || "—"}
                                                </span>
                                            </div>
                                            <div className="col-span-3 sm:col-span-2 flex justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                <button
                                                    onClick={() => downloadFile(file.fid, file.fname)}
                                                    className="p-1.5 text-on-surface-variant hover:text-primary hover:bg-primary/10 rounded-lg transition-colors"
                                                >
                                                    <span className="material-symbols-outlined text-[18px]">download</span>
                                                </button>
                                                <button className="p-1.5 text-on-surface-variant hover:text-error hover:bg-error/10 rounded-lg transition-colors">
                                                    <span className="material-symbols-outlined text-[18px]">delete</span>
                                                </button>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </main>
            </div>
        </div>
    );
}