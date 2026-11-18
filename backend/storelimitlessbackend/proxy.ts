import {Redis} from "@upstash/redis";
import {NextRequest, NextResponse} from "next/server";

const UPSTREAM_KEY = "storelimitless:current-upstream";
const FRONTEND_ORIGIN = "https://storelimitless.vercel.app";

function getRedis() {
    return Redis.fromEnv();
}

/**
 * Attach CORS headers to responses from the stable browser-facing API host.
 * The frontend and API proxy are separate Vercel origins, so even proxy-generated
 * errors must include these headers or the browser hides the useful response.
 */
function applyCorsHeaders(response: NextResponse, request: NextRequest): NextResponse {
    const origin = request.headers.get("origin");

    if (origin === FRONTEND_ORIGIN) {
        response.headers.set("Access-Control-Allow-Origin", FRONTEND_ORIGIN);
        response.headers.set("Access-Control-Allow-Credentials", "true");
        response.headers.set(
            "Access-Control-Allow-Methods",
            "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS",
        );
        response.headers.set(
            "Access-Control-Allow-Headers",
            request.headers.get("access-control-request-headers")
            || "Authorization, Content-Type, Range, Accept, Origin, X-Requested-With, X-Data-Center, X-Directory, X-File-Name, X-File-Size, X-Upload-Batch-Id",
        );
        response.headers.set(
            "Access-Control-Expose-Headers",
            "Content-Range, Accept-Ranges, Content-Length, Content-Disposition, ETag",
        );
        response.headers.append("Vary", "Origin");
    }

    return response;
}

export async function proxy(request: NextRequest) {
    const pathname = request.nextUrl.pathname;

    // Handle browser preflight directly. It must not depend on the cloud tunnel
    // being available, and it must not require the user's bearer token.
    if (request.method === "OPTIONS") {
        return applyCorsHeaders(
            new NextResponse(null, {
                status: 204,
                headers: {"Cache-Control": "no-store"},
            }),
            request,
        );
    }

    if (pathname === "/api/register" || pathname === "/health") {
        return NextResponse.next();
    }

    let upstream: string | null;

    try {
        upstream = await getRedis().get<string>(UPSTREAM_KEY);
    } catch {
        return applyCorsHeaders(
            NextResponse.json(
                {error: "The backend tunnel registry is unavailable."},
                {
                    status: 503,
                    headers: {"Cache-Control": "no-store"},
                },
            ),
            request,
        );
    }

    if (!upstream) {
        return applyCorsHeaders(
            NextResponse.json(
                {
                    error: "No backend tunnel is registered. Start StoreLimitless on Colab first.",
                },
                {
                    status: 503,
                    headers: {"Cache-Control": "no-store"},
                },
            ),
            request,
        );
    }

    const destination = new URL(
        `${request.nextUrl.pathname}${request.nextUrl.search}`,
        `${upstream}/`,
    );

    // Do not proxy long-lived media bodies through Vercel. Return a short, uncached
    // redirect so the browser/player streams byte ranges directly from cloudflared.
    const isMediaStream =
        pathname.startsWith("/public/stream/")
        || pathname.startsWith("/public/download/")
        || pathname.startsWith("/public/download-zip/");

    if (isMediaStream) {
        const redirect = NextResponse.redirect(destination, 307);
        redirect.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
        redirect.headers.set("Referrer-Policy", "no-referrer");
        return applyCorsHeaders(redirect, request);
    }

    // Preserve the stable URL while forwarding to the registered tunnel. Add CORS
    // headers to the rewrite response so the browser can read API responses/errors.
    return applyCorsHeaders(NextResponse.rewrite(destination), request);
}

export const config = {
    matcher: ["/((?!_next/|favicon.ico).*)"],
};
