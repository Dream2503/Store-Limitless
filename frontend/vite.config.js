import {defineConfig} from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
    plugins: [react()],
    worker: {
        // Media workers use dynamic imports for optional codec implementations.
        format: "es",
    },
    server: {
        host: "0.0.0.0",
        port: 5173,
        strictPort: true,
        allowedHosts: [".trycloudflare.com"],
        headers: {
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
        },
    },
    preview: {
        headers: {
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
        },
    },
});
