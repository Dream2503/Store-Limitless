import {Redis} from "@upstash/redis";
import {NextRequest, NextResponse} from "next/server";

const UPSTREAM_KEY = "storelimitless:current-upstream";

function getRedis() {
    return Redis.fromEnv();
}

export async function proxy(request: NextRequest) {
    const pathname = request.nextUrl.pathname;

    // These routes belong to this proxy application.
    if (pathname === "/api/register" || pathname === "/health") {
        return NextResponse.next();
    }

    let upstream: string | null;

    try {
        upstream = await getRedis().get<string>(UPSTREAM_KEY);
    } catch {
        return NextResponse.json(
            {error: "The backend tunnel registry is unavailable."},
            {
                status: 503,
                headers: {"Cache-Control": "no-store"},
            },
        );
    }

    if (!upstream) {
        return NextResponse.json(
            {
                error: "No backend tunnel is registered. Start StoreLimitless on Colab first.",
            },
            {
                status: 503,
                headers: {"Cache-Control": "no-store"},
            },
        );
    }

    // Preserve the incoming path, query string, HTTP method, and request body.
    const destination = new URL(
        `${request.nextUrl.pathname}${request.nextUrl.search}`,
        `${upstream}/`,
    );

    return NextResponse.rewrite(destination);
}

export const config = {
    matcher: ["/((?!_next/|favicon.ico).*)"],
};
