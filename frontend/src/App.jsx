import {BrowserRouter, Route, Routes} from "react-router-dom";
import AuthPage from "./pages/AuthPage";
import DashboardPage from "./pages/Dashboard";
import UploadPage from "./pages/upFiles";

function App() {
    return (
        <BrowserRouter>
            <Routes>
                <Route path="/" element={<AuthPage/>}/>
                <Route path="/dashboard" element={<DashboardPage/>}/>
                <Route path="/upload" element={<UploadPage/>}/>
            </Routes>
        </BrowserRouter>
    );
}

export default App;