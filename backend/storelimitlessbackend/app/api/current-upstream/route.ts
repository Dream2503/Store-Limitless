import {Redis} from "@upstash/redis";
import {NextRequest, NextResponse} from "next/server";

const UPSTREAM_KEY = "storelimitless:current-upstream";
const UPDATED_KEY = "storelimitless:upstream-updated-at";
const FRONTEND_ORIGIN = "https://storelimitless.vercel.app";

// The tunnel changes whenever the backend restarts. Route Handlers run at
// request time with Cache Components enabled; response headers below explicitly
// prevent browsers and intermediary caches from retaining an old tunnel URL.

function responseHeaders(request: NextRequest): Headers {
    const headers = new Headers({
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Vary": "Origin",
    });

    // This endpoint is read-only and does not use cookies. Restrict browser
    // access to the production website origin while keeping server-side checks
    // and local development unaffected.
    if (request.headers.get("origin") === FRONTEND_ORIGIN) {
        headers.set("Access-Control-Allow-Origin", FRONTEND_ORIGIN);
    }

    return headers;
}

export async function GET(request: NextRequest) {
    const headers = responseHeaders(request);

    try {
        const redis = Redis.fromEnv();
        const upstream = await redis.get<string>(UPSTREAM_KEY);
        const updatedAt = await redis.get<string>(UPDATED_KEY);

        if (typeof upstream !== "string" || !upstream) {
            return NextResponse.json(
                {error: "No Cloudflare Quick Tunnel is registered. Start StoreLimitless on the backend first."},
                {status: 503, headers},
            );
        }

        let tunnel: URL;
        try {
            tunnel = new URL(upstream);
        } catch {
            return NextResponse.json(
                {error: "The registered Cloudflare tunnel URL is invalid. Restart the backend to register a new tunnel."},
                {status: 503, headers},
            );
        }

        // Validate the value again at the point of use. The registration route
        // already enforces this format, but Redis should not be blindly trusted.
        if (
            tunnel.protocol !== "https:"
            || !tunnel.hostname.toLowerCase().endsWith(".trycloudflare.com")
            || tunnel.username
            || tunnel.password
            || tunnel.port
            || tunnel.pathname !== "/"
            || tunnel.search
            || tunnel.hash
        ) {
            return NextResponse.json(
                {error: "The registered backend URL is not a valid Cloudflare Quick Tunnel."},
                {status: 503, headers},
            );
        }

        return NextResponse.json(
            {api_url: tunnel.origin, updated_at: updatedAt || null},
            {headers},
        );
    } catch {
        return NextResponse.json(
            {error: "The Cloudflare tunnel registry is temporarily unavailable."},
            {status: 503, headers},
        );
    }
}
