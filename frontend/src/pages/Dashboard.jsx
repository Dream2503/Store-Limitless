import {useEffect, useState} from "react";

export default function Dashboard() {

    const [files, setFiles] = useState([]);
    const [showUpload, setShowUpload] = useState(false);
    const [contextMenu, setContextMenu] = useState(null);
    const [file, setFile] = useState(null);
    const [dataCenter, setDataCenter] = useState("Discord");

    const [uploading, setUploading] = useState(false);
    const [progress, setProgress] = useState(0);

    useEffect(() => {
        fetchFiles();
    }, []);

    const fetchFiles = async () => {
        const res = await fetch("http://127.0.0.1:8000/auth/files");
        const data = await res.json();
        setFiles(data);
    };

    const handleUpload = () => {

        if (!file) {
            alert("Please select a file first");
            return;
        }

        const formData = new FormData();
        formData.append("file", file);
        formData.append("data_center", dataCenter);
        formData.append("uid", 1);

        const xhr = new XMLHttpRequest();

        setUploading(true);
        setProgress(0);

        xhr.upload.onprogress = (e) => {

            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                setProgress(percent);
            }

        };

        xhr.onload = () => {

            if (xhr.status === 200) {
                setUploading(false);
                setProgress(100);
                setShowUpload(false);
                setFile(null);
                fetchFiles();
            } else {
                alert("Upload failed");
                setUploading(false);
            }

        };

        xhr.onerror = () => {
            alert("Upload failed");
            setUploading(false);
        };

        xhr.open("POST", "http://127.0.0.1:8000/auth/upload");
        xhr.send(formData);
    };

    const handleRightClick = (e) => {
        e.preventDefault();

        setContextMenu({
            x: e.pageX, y: e.pageY
        });
    };

    const closeContextMenu = () => {
        setContextMenu(null);
    };

    return (<div
        className="min-h-screen bg-gray-100 p-6"
        onClick={closeContextMenu}
        onContextMenu={handleRightClick}
    >

        <div className="flex justify-between mb-6">

            <h1 className="text-3xl font-bold">
                Dashboard
            </h1>

            <button
                onClick={() => setShowUpload(true)}
                className="bg-indigo-600 text-white px-4 py-2 rounded-lg"
            >
                + New
            </button>

        </div>

        <div className="bg-white rounded-xl shadow p-6 grid grid-cols-5 gap-4">

            {files.map((file) => (<div
                key={file.id}
                className="p-4 border rounded-lg hover:bg-gray-100 cursor-pointer"
            >
                <p className="text-lg">📄</p>
                <p className="text-sm truncate">{file.name}</p>
            </div>))}

        </div>

        {contextMenu && (<div
            className="absolute bg-white shadow rounded-lg p-2"
            style={{
                top: contextMenu.y, left: contextMenu.x
            }}
        >
            <button
                className="block px-4 py-2 hover:bg-gray-100 w-full text-left"
                onClick={() => setShowUpload(true)}
            >
                Upload File
            </button>
        </div>)}

        {showUpload && (<div className="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center">

            <div className="bg-white p-6 rounded-xl w-96">

                <h2 className="text-xl font-bold mb-4">
                    Upload File
                </h2>

                <input
                    type="file"
                    onChange={(e) => setFile(e.target.files[0])}
                    className="mb-4"
                />

                <select
                    value={dataCenter}
                    onChange={(e) => setDataCenter(e.target.value)}
                    className="border p-2 rounded mb-4 w-full"
                >
                    <option value="Discord">Discord</option>
                    <option value="Telegram">Telegram</option>
                </select>

                <div className="flex justify-end gap-3">

                    <button
                        onClick={() => setShowUpload(false)}
                        className="px-4 py-2 border rounded"
                    >
                        Cancel
                    </button>

                    <button
                        onClick={handleUpload}
                        disabled={uploading}
                        className="px-4 py-2 bg-indigo-600 text-white rounded disabled:opacity-50"
                    >
                        {uploading ? "Uploading..." : "Upload"}
                    </button>

                </div>

                {uploading && (<div className="mt-4">

                    <div className="w-full bg-gray-200 rounded-full h-3">

                        <div
                            className="bg-indigo-600 h-3 rounded-full transition-all duration-300"
                            style={{width: `${progress}%`}}
                        />

                    </div>

                    <p className="text-sm text-gray-500 mt-1">
                        {progress.toFixed(0)}%
                    </p>

                </div>)}

            </div>

        </div>)}

    </div>);
}