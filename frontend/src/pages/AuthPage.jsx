import { useState } from "react";
import { useNavigate } from "react-router-dom";

export default function AuthPage() {
    const navigate = useNavigate();

    const [isLogin, setIsLogin] = useState(true);

    const [formData, setFormData] = useState({
        first_name: "",
        last_name: "",
        username: "",
        password: ""
    });

    const [message, setMessage] = useState("");
    const [loading, setLoading] = useState(false);

    const handleChange = (e) => {
        setFormData((prev) => ({
            ...prev,
            [e.target.name]: e.target.value
        }));
    };

    const handleSubmit = async (e) => {
        e.preventDefault();

        setMessage("");
        setLoading(true);

        try {

            const endpoint = isLogin
                ? "http://127.0.0.1:8000/auth/login"
                : "http://127.0.0.1:8000/auth/register";

            const bodyData = isLogin
                ? {
                    username: formData.username,
                    password: formData.password
                }
                : {
                    first_name: formData.first_name,
                    last_name: formData.last_name,
                    username: formData.username,
                    password: formData.password
                };

            const response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(bodyData)
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Something went wrong");
            }

            setMessage(data.message);

            if (isLogin) {
                localStorage.setItem("token", data.access_token);
                navigate("/dashboard");
            }

        } catch (error) {
            setMessage(error.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-500 to-purple-600 px-4">
            <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-8">

                <h2 className="text-3xl font-bold text-center mb-6 text-gray-800">
                    {isLogin ? "Welcome Back" : "Create Account"}
                </h2>

                <form onSubmit={handleSubmit} className="space-y-4">

                    {!isLogin && (
                        <>
                            <input
                                type="text"
                                name="first_name"
                                placeholder="First Name"
                                value={formData.first_name}
                                onChange={handleChange}
                                required
                                className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-400 outline-none"
                            />

                            <input
                                type="text"
                                name="last_name"
                                placeholder="Last Name"
                                value={formData.last_name}
                                onChange={handleChange}
                                required
                                className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-400 outline-none"
                            />
                        </>
                    )}

                    <input
                        type="text"
                        name="username"
                        placeholder="Username"
                        value={formData.username}
                        onChange={handleChange}
                        required
                        className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-400 outline-none"
                    />

                    <input
                        type="password"
                        name="password"
                        placeholder="Password"
                        value={formData.password}
                        onChange={handleChange}
                        required
                        className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-400 outline-none"
                    />

                    <button
                        type="submit"
                        disabled={loading}
                        className="w-full bg-indigo-600 text-white py-2 rounded-lg hover:bg-indigo-700 transition duration-300"
                    >
                        {loading ? "Processing..." : isLogin ? "Sign In" : "Sign Up"}
                    </button>

                </form>

                {message && (
                    <p className="text-center mt-4 text-sm text-red-500">
                        {message}
                    </p>
                )}

                <p className="text-center mt-6 text-sm text-gray-600">
                    {isLogin ? "Don't have an account?" : "Already have an account?"}

                    <button
                        onClick={() => {
                            setIsLogin(!isLogin);
                            setMessage("");
                        }}
                        className="ml-2 text-indigo-600 font-semibold hover:underline"
                    >
                        {isLogin ? "Sign Up" : "Sign In"}
                    </button>
                </p>

            </div>
        </div>
    );
}