import {useLocation, useNavigate} from "react-router-dom";
import UploadJobsTray from "./dashboard/components/UploadJobsTray.jsx";
import useUploadJobs from "./dashboard/hooks/useUploadJobs.js";

/** Stable no-op refresh callback for the standalone tray route. */
const noRefreshDirectory = async () => {
};

/** Full-screen view of the same server-backed upload task queue used by the dashboard tray. */
export default function UploadTrayPage() {
    const navigate = useNavigate();
    const location = useLocation();
    const {uploadJobs, dismissUploadJob, cancelUploadJob, clearAllUploadJobs} = useUploadJobs(noRefreshDirectory);
    const returnTo = typeof location.state?.returnTo === "string" && location.state.returnTo.startsWith("/home") ? location.state.returnTo : "/home";

    return <UploadJobsTray
        fullscreen
        uploadJobs={uploadJobs}
        cancelUploadJob={cancelUploadJob}
        dismissUploadJob={dismissUploadJob}
        clearAllUploadJobs={clearAllUploadJobs}
        onMinimize={() => navigate(returnTo, {replace: true})}
    />;
}
