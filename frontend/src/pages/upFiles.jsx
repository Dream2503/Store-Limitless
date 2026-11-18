import {useState} from "react";
import {useNavigate} from "react-router-dom";

export default function UploadPage() {
    const navigate = useNavigate();

    const [file, setFile] = useState(null);
    const [dataCenter, setDataCenter] = useState("");
    const [progress, setProgress] = useState(0);
    const [status, setStatus] = useState("idle");

    const handleUpload = async () => {
        if (!file || !dataCenter) {
            alert("Please select file and data center");
            return;
        }

        setStatus("uploading");
        setProgress(0);

        const formData = new FormData();
        formData.append("file", file);
        formData.append("data_center", dataCenter);

        try {
            const res = await fetch("http://127.0.0.1:8000/auth/upload", {
                method: "POST", headers: {
                    Authorization: `Bearer ${localStorage.getItem("token")}`
                }, body: formData
            });

            // ❗ Important checks
            if (!res.ok || !res.body) {
                setStatus("error");
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const {done, value} = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split("\n");

                for (let line of lines) {
                    if (!line.trim()) continue;

                    const data = JSON.parse(line);
                    setProgress(prev => Math.max(prev, data.progress));
                }
            }

            setProgress(100);
            setStatus("done");

            // 🔥 Redirect back to dashboard
            setTimeout(() => {
                navigate("/dashboard");
            }, 1000);

        } catch (err) {
            console.error(err);
            setStatus("error");
        }
    };

    return (<div className="min-h-screen bg-[#020617] text-white flex items-center justify-center px-6">

        <div className="w-full max-w-xl bg-[#020617] border border-gray-800 rounded-xl p-8 shadow-lg">

            <h1 className="text-2xl font-semibold mb-6">
                Upload File
            </h1>

            {/* Data Center */}
            <div className="mb-6">
                <p className="text-sm text-gray-400 mb-2">
                    Select Data Center
                </p>

                <div className="flex gap-3">
                    {["Discord", "Telegram"].map(dc => (<button
                        key={dc}
                        onClick={() => setDataCenter(dc)}
                        className={`px-4 py-2 rounded-lg border transition ${dataCenter === dc ? "bg-indigo-600 border-indigo-600" : "border-gray-700 hover:border-indigo-500"}`}
                    >
                        {dc.toUpperCase()}
                    </button>))}
                </div>
            </div>

            {/* File Input */}
            <div className="mb-6">
                <p className="text-sm text-gray-400 mb-2">
                    Select File
                </p>

                <input
                    type="file"
                    onChange={(e) => setFile(e.target.files[0])}
                    disabled={status === "uploading"}
                    className="block w-full text-sm text-gray-300 file:bg-indigo-600 file:border-0 file:px-4 file:py-2 file:rounded-lg file:text-white"
                />

                {file && (<p className="text-sm mt-2 text-gray-400">
                    {file.name}
                </p>)}
            </div>

            {/* Upload Button */}
            <button
                onClick={handleUpload}
                disabled={!file || !dataCenter || status === "uploading"}
                className="w-full bg-indigo-600 py-2 rounded-lg hover:bg-indigo-700 disabled:bg-gray-600"
            >
                {status === "uploading" ? "Uploading..." : "Upload File"}
            </button>

            {/* Progress */}
            {status !== "idle" && (<div className="mt-6">

                <div className="w-full bg-gray-800 h-2 rounded-full">
                    <div
                        className="bg-indigo-500 h-2 rounded-full transition-all"
                        style={{width: `${progress}%`}}
                    />
                </div>

                <div className="flex justify-between mt-2 text-sm text-gray-400">
                    <span>{progress}%</span>
                    <span>
                                {status === "uploading" && "Uploading"}
                        {status === "done" && "Completed"}
                        {status === "error" && "Failed"}
                            </span>
                </div>

            </div>)}

            <button
                onClick={() => navigate("/dashboard")}
                className="mt-6 text-sm text-gray-400 hover:text-white"
            >
                ← Back to Dashboard
            </button>

        </div>
    </div>);
}