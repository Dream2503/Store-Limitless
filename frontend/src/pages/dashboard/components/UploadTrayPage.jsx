import {useCallback} from "react";
import UploadJobsTray from "./UploadJobsTray.jsx";
import useUploadJobs from "../hooks/useUploadJobs.js";

/**
 * Full-screen view of the same account-scoped upload batches used by Dashboard.
 * The server is the source of truth, so navigating here never creates a second task.
 */
export default function UploadTrayPage() {
    const refreshDirectory = useCallback(async () => {}, []);
    const {uploadJobs, cancelUploadJob, dismissUploadJob, clearAllUploadJobs} = useUploadJobs(refreshDirectory);

    return <main className="min-h-[100dvh] bg-[#0b100d] text-[#e8f0eb]">
        <UploadJobsTray
            fullscreen
            uploadJobs={uploadJobs}
            cancelUploadJob={cancelUploadJob}
            dismissUploadJob={dismissUploadJob}
            clearAllUploadJobs={clearAllUploadJobs}
        />
    </main>;
}
