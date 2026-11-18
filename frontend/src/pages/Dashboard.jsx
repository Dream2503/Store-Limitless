import { useState } from "react";
import { useNavigate } from "react-router-dom";

export default function Dashboard() {

    const navigate = useNavigate();

    const [file, setFile] = useState(null);
    const [uploadedSize, setUploadedSize] = useState(0);
    const [dataCenter, setDataCenter] = useState("discord");
    const [successMsg, setSuccessMsg] = useState("");
    const [progress, setProgress] = useState(0);
    const [uploading, setUploading] = useState(false);

    const handleFileChange = (e) => {
        setFile(e.target.files[0]);
    };

    const handleUpload = () => {
        if (!file) return;

        const formData = new FormData();
        formData.append("file", file);
        formData.append("data_center", dataCenter);
        formData.append("uid", 1);

        const xhr = new XMLHttpRequest();

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                setProgress(percent);
            }
        };

        xhr.onload = () => {
            if (xhr.status === 200) {
                const sizeMB = file.size / (1024 * 1024);
                setUploadedSize(prev => prev + sizeMB);
                setSuccessMsg(`"${file.name}" uploaded successfully!`);
                setTimeout(() => setSuccessMsg(""), 3000);
                setFile(null);
                setProgress(0);
            } else {
                alert("Upload failed");
            }
            setUploading(false);
        };

        xhr.onerror = () => {
            alert("Upload failed");
            setUploading(false);
        };

        xhr.open("POST", "http://127.0.0.1:8000/auth/upload");
        setUploading(true);
        xhr.send(formData);
    };

    return (
        <div className="min-h-screen bg-gray-100 p-8">

            <h1 className="text-3xl font-bold mb-8">
                Dashboard
            </h1>

            <div className="bg-white shadow-md rounded-xl p-6 mb-6">

                <h2 className="text-xl font-semibold mb-4">
                    Upload File
                </h2>

                <input
                    type="file"
                    onChange={handleFileChange}
                    className="mb-4"
                />

                <select
                    value={dataCenter}
                    onChange={(e) => setDataCenter(e.target.value)}
                    className="mb-4 border p-2 rounded"
                >
                    <option value="discord">Discord</option>
                    <option value="telegram">Telegram</option>
                </select>

                <button
                    onClick={handleUpload}
                    disabled={uploading}
                    className="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    {uploading ? "Uploading..." : "Upload"}
                </button>

                {uploading && (
                    <div className="mt-4">
                        <div className="w-full bg-gray-200 rounded-full h-3">
                            <div
                                className="bg-indigo-600 h-3 rounded-full transition-all duration-300"
                                style={{ width: `${progress}%` }}
                            />
                        </div>
                        <p className="text-sm text-gray-500 mt-1">{progress}%</p>
                    </div>
                )}

                {successMsg && (
                    <p className="mt-3 text-green-600 font-medium">
                        ✓ {successMsg}
                    </p>
                )}

            </div>

            <div className="bg-white shadow-md rounded-xl p-6">

                <h2 className="text-xl font-semibold mb-4">
                    Monthly Usage
                </h2>

                <p className="text-lg">
                    Total Uploaded This Month:
                </p>

                <p className="text-3xl font-bold text-indigo-600 mt-2">
                    {uploadedSize.toFixed(2)} MB
                </p>

            </div>

        </div>
    );
}