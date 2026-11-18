import {useEffect} from "react";
import {BrowserRouter, Navigate, Route, Routes, useSearchParams} from "react-router-dom";
import Auth from "./pages/Auth.jsx";
import Landing from "./pages/Landing.jsx";
import DashboardPage from "./pages/Dashboard.jsx";
import UploadPage from "./pages/dashboard/components/UploadModal.jsx";
import ViewFile from "./pages/dashboard/components/ViewFile.jsx";
import UploadTrayPage from "./pages/dashboard/components/UploadTrayPage.jsx";


/**
 * Reload the current page after an intentional upward wheel gesture or downward
 * touch pull at the top of the entire scroll chain, including nested scroll areas.
 */
function installTopEdgeRefresh() {
    let wheelDistance = 0;
    let lastWheelAt = 0;
    let touchStartY = 0;
    let touchDistance = 0;
    let touchEligible = false;
    let touchTriggered = false;

    const isAtTopOfScrollChain = (target) => {
        let element = target instanceof Element ? target : null;

        while (element) {
            const style = window.getComputedStyle(element);
            const canScrollY = /(auto|scroll|overlay)/.test(style.overflowY)
                && element.scrollHeight > element.clientHeight + 1;

            if (canScrollY && element.scrollTop > 1) return false;
            element = element.parentElement;
        }

        return window.scrollY <= 1
            && document.documentElement.scrollTop <= 1
            && document.body.scrollTop <= 1;
    };

    const refresh = () => {
        window.location.reload();
    };

    const handleWheel = (event) => {
        if (event.deltaY >= 0 || !isAtTopOfScrollChain(event.target)) {
            wheelDistance = 0;
            lastWheelAt = 0;
            return;
        }

        const now = performance.now();
        if (now - lastWheelAt > 350) wheelDistance = 0;
        lastWheelAt = now;
        wheelDistance += Math.abs(event.deltaY);

        if (wheelDistance >= 90) {
            wheelDistance = 0;
            refresh();
        }
    };

    const handleTouchStart = (event) => {
        if (event.touches.length !== 1) {
            touchEligible = false;
            return;
        }

        touchStartY = event.touches[0].clientY;
        touchDistance = 0;
        touchTriggered = false;
        touchEligible = isAtTopOfScrollChain(event.target);
    };

    const handleTouchMove = (event) => {
        if (!touchEligible || event.touches.length !== 1 || touchTriggered) return;

        touchDistance = event.touches[0].clientY - touchStartY;

        if (touchDistance < 0 || !isAtTopOfScrollChain(event.target)) {
            touchEligible = false;
            return;
        }

        if (touchDistance >= 110) touchTriggered = true;
    };

    const handleTouchEnd = () => {
        if (touchEligible && touchTriggered) refresh();
        touchEligible = false;
        touchTriggered = false;
        touchDistance = 0;
    };

    window.addEventListener("wheel", handleWheel, {passive: true});
    window.addEventListener("touchstart", handleTouchStart, {passive: true});
    window.addEventListener("touchmove", handleTouchMove, {passive: true});
    window.addEventListener("touchend", handleTouchEnd, {passive: true});
    window.addEventListener("touchcancel", handleTouchEnd, {passive: true});

    return () => {
        window.removeEventListener("wheel", handleWheel);
        window.removeEventListener("touchstart", handleTouchStart);
        window.removeEventListener("touchmove", handleTouchMove);
        window.removeEventListener("touchend", handleTouchEnd);
        window.removeEventListener("touchcancel", handleTouchEnd);
    };
}

/**
 * Convert a stored folder path into the dashboard route format.
 * @param {string} directory
 * @returns {string}
 */
function directoryToRoute(directory) {
    const segments = (directory || "/home").split("/").filter(Boolean);
    const relativeSegments = segments[0] === "home" ? segments.slice(1) : segments;
    return `/home${relativeSegments.length ? `/${relativeSegments.map(encodeURIComponent).join("/")}` : ""}`;
}

/**
 * Redirect legacy dashboard URLs to the corresponding folder route.
 * @returns {import("react").ReactElement}
 */
function LegacyDashboardRedirect() {
    const [searchParams] = useSearchParams();
    return <Navigate to={directoryToRoute(searchParams.get("directory") || "/home")} replace/>;
}

/**
 * Declare the client-side routes for authentication, browsing, uploading, and previews.
 * @returns {import("react").ReactElement}
 */
function App() {
    useEffect(() => installTopEdgeRefresh(), []);

    return (<BrowserRouter>
        <Routes>
            <Route path="/" element={<Landing/>}/>
            <Route path="/login" element={<Auth initialMode="login"/>}/>
            <Route path="/signup" element={<Auth initialMode="signup"/>}/>
            <Route path="/home" element={<DashboardPage/>}/>
            <Route path="/home/*" element={<DashboardPage/>}/>
            <Route path="/upload-tray" element={<UploadTrayPage/>}/>
            <Route path="/dashboard" element={<LegacyDashboardRedirect/>}/>
            <Route path="/upload" element={<UploadPage/>}/>
            <Route path="/view/:fileId" element={<ViewFile/>}/>
        </Routes>
    </BrowserRouter>);
}

export default App;