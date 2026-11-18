import {useEffect, useState} from "react";
import {useNavigate} from "react-router-dom";

export default function DashboardPage() {
    const navigate = useNavigate();
    const [files, setFiles] = useState([]);

    const fetchFiles = async () => {
        try {
            const res = await fetch("http://127.0.0.1:8000/auth/files", {
                headers: {
                    Authorization: `Bearer ${localStorage.getItem("token")}`
                }
            });

            const data = await res.json();
            setFiles(data);

        } catch (err) {
            console.error("Failed to fetch files", err);
        }
    };

    useEffect(() => {
        fetchFiles();
    }, []);

    const downloadFile = async (fid, fname) => {
        const res = await fetch(`http://127.0.0.1:8000/auth/download/${fid}`, {
            headers: {
                Authorization: `Bearer ${localStorage.getItem("token")}`
            }
        });

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = fname;
        a.click();
        window.URL.revokeObjectURL(url);
    };

    return (<div className="h-screen flex bg-gradient-to-br from-[#0f172a] to-[#020617] text-white">

        <aside className="w-64 bg-[#020617]/80 backdrop-blur border-r border-gray-800 p-6">
            <h2 className="text-xl font-semibold mb-8 tracking-wide">
                🚀 DriveBot
            </h2>

            <div className="space-y-3 text-gray-400 text-sm">
                <p className="text-white">📁 Files</p>

                <p
                    onClick={() => navigate("/upload")}
                    className="hover:text-white cursor-pointer"
                >
                    ⬆ Upload
                </p>

                <p className="hover:text-white cursor-pointer">Settings</p>
            </div>
        </aside>

        <main className="flex-1 p-10 overflow-y-auto">

            <div className="flex justify-between items-center mb-8">
                <h1 className="text-3xl font-semibold tracking-tight">
                    Your Files
                </h1>

                <button
                    onClick={() => navigate("/upload")}
                    className="bg-indigo-600 px-5 py-2 rounded-lg hover:bg-indigo-700"
                >
                    Upload File
                </button>
            </div>


            {files.length === 0 ? (<p className="text-gray-400">No files uploaded</p>) : (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">

                    {files.map(file => (<div
                        key={file.fid}
                        className="bg-[#020617] border border-gray-800 p-5 rounded-xl hover:border-indigo-500 transition"
                    >
                        <div className="text-3xl mb-3">📄</div>

                        <p className="truncate font-medium">
                            {file.fname}
                        </p>

                        <p className="text-xs text-gray-500 mb-3">
                            {file.data_center}
                        </p>

                        <button
                            onClick={() => downloadFile(file.fid, file.fname)}
                            className="text-indigo-400 text-sm hover:underline"
                        >
                            Download
                        </button>
                    </div>))}

                </div>)}
        </main>
    </div>);
}