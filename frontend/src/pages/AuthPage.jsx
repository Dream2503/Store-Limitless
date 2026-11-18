import {useState} from "react";
import {useNavigate} from "react-router-dom";

export default function AuthPage() {
    const navigate = useNavigate();
    const [isLogin, setIsLogin] = useState(true);
    const [formData, setFormData] = useState({
        first_name: "", last_name: "", username: "", password: ""
    });
    const [message, setMessage] = useState("");
    const [loading, setLoading] = useState(false);

    const handleChange = (e) => {
        setFormData((prev) => ({...prev, [e.target.name]: e.target.value}));
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setMessage("");
        setLoading(true);
        try {
            const endpoint = isLogin ? "http://127.0.0.1:8000/auth/login" : "http://127.0.0.1:8000/auth/register";
            const bodyData = isLogin ? {username: formData.username, password: formData.password} : {...formData};
            const response = await fetch(endpoint, {
                method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(bodyData),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || "Something went wrong");
            if (isLogin) {
                localStorage.setItem("token", data.access_token);
                navigate("/dashboard");
            } else {
                setIsLogin(true);
                setMessage("Registration successful. Please login.");
            }
        } catch (error) {
            setMessage(error.message);
        } finally {
            setLoading(false);
        }
    };

    return (<div className="min-h-screen flex items-center justify-center bg-background px-4">
        {/* Background glow */}
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
            <div className="absolute top-[-20%] left-1/2 -translate-x-1/2 w-[600px] h-[600px] bg-primary/10 rounded-full blur-[120px]"/>
        </div>

        <div className="relative w-full max-w-md">
            {/* Logo */}
            <div className="flex items-center justify-center gap-3 mb-8">
                <div className="w-10 h-10 rounded-xl bg-primary flex items-center justify-center">
                    <span className="material-symbols-outlined text-on-primary" style={{fontVariationSettings: "'FILL' 1"}}>cloud</span>
                </div>
                <h1 className="text-2xl font-bold text-primary">DriveBot</h1>
            </div>

            {/* Card */}
            <div className="glass-panel rounded-2xl p-8 card-shadow border border-outline-variant/20">
                <h2 className="text-xl font-semibold text-on-surface mb-1">
                    {isLogin ? "Welcome back" : "Create your account"}
                </h2>
                <p className="text-sm text-on-surface-variant mb-6">
                    {isLogin ? "Sign in to access your files." : "Join DriveBot to manage your files."}
                </p>

                <form onSubmit={handleSubmit} className="space-y-4">
                    {!isLogin && (<div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-xs font-medium text-on-surface-variant mb-1 uppercase tracking-widest">First
                                Name</label>
                            <input
                                type="text" name="first_name"
                                value={formData.first_name} onChange={handleChange} required
                                className="w-full bg-surface-container-low border border-outline-variant/30 rounded-xl py-2 px-3 text-sm text-on-surface focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all placeholder:text-on-surface-variant/40"
                                placeholder="John"
                            />
                        </div>
                        <div>
                            <label className="block text-xs font-medium text-on-surface-variant mb-1 uppercase tracking-widest">Last
                                Name</label>
                            <input
                                type="text" name="last_name"
                                value={formData.last_name} onChange={handleChange} required
                                className="w-full bg-surface-container-low border border-outline-variant/30 rounded-xl py-2 px-3 text-sm text-on-surface focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all placeholder:text-on-surface-variant/40"
                                placeholder="Doe"
                            />
                        </div>
                    </div>)}
                    <div>
                        <label className="block text-xs font-medium text-on-surface-variant mb-1 uppercase tracking-widest">Username</label>
                        <input
                            type="text" name="username"
                            value={formData.username} onChange={handleChange} required
                            className="w-full bg-surface-container-low border border-outline-variant/30 rounded-xl py-2 px-3 text-sm text-on-surface focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all placeholder:text-on-surface-variant/40"
                            placeholder="your_username"
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-medium text-on-surface-variant mb-1 uppercase tracking-widest">Password</label>
                        <input
                            type="password" name="password"
                            value={formData.password} onChange={handleChange} required
                            className="w-full bg-surface-container-low border border-outline-variant/30 rounded-xl py-2 px-3 text-sm text-on-surface focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all placeholder:text-on-surface-variant/40"
                            placeholder="••••••••"
                        />
                    </div>

                    {message && (<p className="text-xs text-error bg-error-container/20 border border-error/20 rounded-lg px-3 py-2">
                        {message}
                    </p>)}

                    <button
                        type="submit" disabled={loading}
                        className="w-full bg-primary text-on-primary py-2.5 rounded-xl font-medium text-sm hover:bg-primary/90 transition-colors shadow-[0_0_20px_rgba(192,193,255,0.2)] disabled:opacity-60 mt-2"
                    >
                        {loading ? "Processing..." : isLogin ? "Sign In" : "Create Account"}
                    </button>
                </form>

                <p className="text-center mt-5 text-sm text-on-surface-variant">
                    {isLogin ? "Don't have an account?" : "Already have an account?"}
                    <button
                        onClick={() => {
                            setIsLogin(!isLogin);
                            setMessage("");
                        }}
                        className="ml-2 text-primary font-semibold hover:underline underline-offset-2"
                    >
                        {isLogin ? "Sign Up" : "Sign In"}
                    </button>
                </p>
            </div>
        </div>
    </div>);
}