import {Redis} from "@upstash/redis";
import {NextResponse} from "next/server";

export async function GET() {
    try {
        const redis = Redis.fromEnv();
        const upstream = await redis.get<string>("storelimitless:current-upstream",);
        const updatedAt = await redis.get<string>("storelimitless:upstream-updated-at",);
        return NextResponse.json({ok: true, registered: Boolean(upstream), updatedAt: updatedAt ?? null,}, {headers: {"Cache-Control": "no-store"}},);
    } catch {
        return NextResponse.json({ok: false, error: "Registry unavailable."}, {status: 503, headers: {"Cache-Control": "no-store"},},);
    }
}
