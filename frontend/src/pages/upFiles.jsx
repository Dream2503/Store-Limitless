import { useEffect, useState } from "react";

export default function FilesPage() {

    const [files, setFiles] = useState([]);

    const fetchFiles = async () => {

        try {
            const res = await fetch("http://127.0.0.1:8000/files");

            if (!res.ok) throw new Error("Failed to fetch");

            const data = await res.json();
            setFiles(data);

        } catch (err) {
            console.error(err);
        }
    };

    useEffect(() => {
        fetchFiles();
    }, []);

    const downloadFile = async (fid, fname) => {

        const res = await fetch(`http://127.0.0.1:8000/auth/download/${fid}`);

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = fname;
        a.click();
    };

    return (

        <div className="min-h-screen bg-gray-100 p-8">

            <h1 className="text-3xl font-bold mb-8">
                Your Uploaded Files
            </h1>

            {files.length === 0 ? (

                <p className="text-lg">
                    No files uploaded yet
                </p>

            ) : (

                <div className="bg-white shadow rounded-lg p-6">

                    <table className="w-full">

                        <thead>
                            <tr className="border-b">
                                <th className="p-2 text-left">File Name</th>
                                <th className="p-2 text-left">Data Center</th>
                                <th className="p-2 text-left">Action</th>
                            </tr>
                        </thead>

                        <tbody>

                            {files.map(file => (

                                <tr key={file.fid} className="border-b">

                                    <td className="p-2">{file.fname}</td>

                                    <td className="p-2">{file.data_center}</td>

                                    <td className="p-2">

                                        <button
                                            onClick={() => downloadFile(file.fid, file.fname)}
                                            className="bg-indigo-600 text-white px-3 py-1 rounded"
                                        >
                                            Download
                                        </button>

                                    </td>

                                </tr>

                            ))}

                        </tbody>

                    </table>

                </div>

            )}

        </div>
    );
}