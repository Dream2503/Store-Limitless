import {useCallback} from "react";
import UploadJobsTray from "./UploadJobsTray.jsx";
import useUploadJobs from "../hooks/useUploadJobs.js";
import {ThemeToggle} from "../../../ThemeContext.jsx";

/**
 * Full-screen view of the same account-scoped upload batches used by Dashboard.
 * The server is the source of truth, so navigating here never creates a second task.
 */
export default function UploadTrayPage() {
    const refreshDirectory = useCallback(async () => {}, []);
    const {uploadJobs, cancelUploadJob, dismissUploadJob, clearAllUploadJobs} = useUploadJobs(refreshDirectory);

    return <main className="relative min-h-[100dvh] bg-[#0b100d] text-[#e8f0eb]">
        <ThemeToggle className="fixed right-4 top-3 z-[1000]"/>
        <UploadJobsTray
            fullscreen
            uploadJobs={uploadJobs}
            cancelUploadJob={cancelUploadJob}
            dismissUploadJob={dismissUploadJob}
            clearAllUploadJobs={clearAllUploadJobs}
        />
    </main>;
}
