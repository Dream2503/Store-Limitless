import {timingSafeEqual} from "node:crypto";
import {Redis} from "@upstash/redis";
import {NextRequest, NextResponse} from "next/server";

const UPSTREAM_KEY = "storelimitless:current-upstream";
const UPDATED_KEY = "storelimitless:upstream-updated-at";

function getRedis() {
    return Redis.fromEnv();
}

function tokenMatches(candidate: string, expected: string) {
    const candidateBytes = Buffer.from(candidate);
    const expectedBytes = Buffer.from(expected);

    return (
        candidateBytes.length === expectedBytes.length &&
        timingSafeEqual(candidateBytes, expectedBytes)
    );
}

export async function POST(request: NextRequest) {
    const expectedToken = process.env.STORELIMITLESS_PROXY_TOKEN;
    const authorization = request.headers.get("authorization") ?? "";
    const suppliedToken = authorization.match(/^Bearer\s+(.+)$/i)?.[1] ?? "";

    if (
        !expectedToken ||
        !tokenMatches(suppliedToken, expectedToken)
    ) {
        return NextResponse.json(
            {error: "Unauthorized"},
            {
                status: 401,
                headers: {"Cache-Control": "no-store"},
            },
        );
    }

    const body = (await request.json().catch(() => null)) as
        | { url?: unknown }
        | null;

    if (typeof body?.url !== "string") {
        return NextResponse.json(
            {error: "A tunnel URL is required."},
            {status: 400},
        );
    }

    let tunnel: URL;

    try {
        tunnel = new URL(body.url);
    } catch {
        return NextResponse.json(
            {error: "The tunnel URL is invalid."},
            {status: 400},
        );
    }

    if (
        tunnel.protocol !== "https:" ||
        !tunnel.hostname.toLowerCase().endsWith(".trycloudflare.com") ||
        tunnel.username ||
        tunnel.password ||
        tunnel.port ||
        tunnel.pathname !== "/" ||
        tunnel.search ||
        tunnel.hash
    ) {
        return NextResponse.json(
            {error: "Only HTTPS Cloudflare Quick Tunnel URLs are accepted."},
            {status: 400},
        );
    }

    try {
        const redis = getRedis();
        const updatedAt = new Date().toISOString();

        await redis.set(UPSTREAM_KEY, tunnel.origin);
        await redis.set(UPDATED_KEY, updatedAt);

        return NextResponse.json(
            {ok: true, registered: true, updatedAt},
            {headers: {"Cache-Control": "no-store"}},
        );
    } catch {
        return NextResponse.json(
            {error: "Could not update the tunnel registry."},
            {status: 503},
        );
    }
}
