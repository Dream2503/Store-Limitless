"""Redis-backed FIFO upload serialization shared by torrent and Google Drive ingestion."""

from __future__ import annotations

from asyncio import Task, CancelledError, create_task, gather, sleep, current_task
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import uuid4

from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import write_log

_QUEUE = "ingestion:upload:queue"
_SEQUENCE = "ingestion:upload:sequence"
_LOCK = "ingestion:upload:lock"
_PREFIX = "ingestion:upload:ticket:"
_TICKET_TTL = 300
_LOCK_TTL_MS = 120_000
_HEARTBEAT = 10.0

_ENQUEUE = """
-- storelimitless-ingestion-enqueue
local sequence = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[1], sequence, ARGV[1])
return sequence
"""
_CLAIM = """
-- storelimitless-ingestion-claim
local head
while true do
    head = redis.call('ZRANGE', KEYS[1], 0, 0)
    if #head == 0 then return -1 end
    if redis.call('EXISTS', ARGV[3] .. head[1]) == 0 then
        redis.call('ZREM', KEYS[1], head[1])
    else
        break
    end
end
if redis.call('EXISTS', KEYS[3]) == 0 then
    redis.call('ZREM', KEYS[1], ARGV[1])
    return -2
end
if head[1] ~= ARGV[1] then return 0 end
if not redis.call('SET', KEYS[2], ARGV[1], 'NX', 'PX', ARGV[2]) then return 0 end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[3])
return 1
"""
_RENEW_TICKET = """
-- storelimitless-ingestion-renew-ticket
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
return redis.call('EXPIRE', KEYS[1], ARGV[1])
"""
_RENEW_LOCK = """
-- storelimitless-ingestion-renew-lock
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('PEXPIRE', KEYS[1], ARGV[2])
"""
_RELEASE_LOCK = """
-- storelimitless-ingestion-release-lock
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""
_CANCEL = """
-- storelimitless-ingestion-cancel-ticket
redis.call('ZREM', KEYS[1], ARGV[1])
return redis.call('DEL', KEYS[2])
"""


@dataclass(slots=True)
class IngestionUploadTicket:
    """One FIFO position for a file that has finished downloading."""

    ticket_id: str
    key: str
    heartbeat: Task[None] | None = None
    claimed: bool = False
    closed: bool = False
    label: str = "upload"
    username: str = ""


async def _keep_ticket_alive(ticket: IngestionUploadTicket) -> None:
    """Renew a queued ticket so a busy uploader does not let it expire."""

    try:
        while not ticket.closed and not ticket.claimed:
            await sleep(_HEARTBEAT)
            if not ticket.closed and not ticket.claimed:
                alive = await Redis.redis.eval(_RENEW_TICKET, 1, ticket.key, _TICKET_TTL)
                if not alive:
                    return
    except CancelledError:
        raise
    except Exception:
        # The ticket will expire if Redis is unavailable; it may never upload without a valid claim.
        return


async def enqueue_ingestion_upload(label: str = "upload", username: str = "") -> IngestionUploadTicket:
    """Atomically append a named file to the shared Redis FIFO and keep its ticket alive."""

    ticket_id = uuid4().hex
    ticket = IngestionUploadTicket(
        ticket_id,
        f"{_PREFIX}{ticket_id}",
        label=str(label or "upload")[:160],
        username=str(username or ""),
    )
    await Redis.redis.eval(
        _ENQUEUE, 3, _QUEUE, _SEQUENCE, ticket.key, ticket_id, _TICKET_TTL
    )
    ticket.heartbeat = create_task(_keep_ticket_alive(ticket), name=f"ingestion-ticket-{ticket_id}")
    write_log(
        "INFO", Database, "UPLOAD QUEUE", ticket.username or "system",
        f"Queued ticket={ticket.ticket_id} name={ticket.label!r}; waiting for the shared upload slot.",
    )
    return ticket


async def cancel_ingestion_upload(ticket: IngestionUploadTicket) -> None:
    """Remove a not-yet-claimed ticket after cancellation or a failed queue handoff."""

    if ticket.closed or ticket.claimed:
        return
    ticket.closed = True
    await Redis.redis.eval(_CANCEL, 2, _QUEUE, ticket.key, ticket.ticket_id)
    write_log(
        "WARNING", Database, "UPLOAD QUEUE", ticket.username or "system",
        f"Cancelled unclaimed ticket={ticket.ticket_id} name={ticket.label!r}.",
    )
    if ticket.heartbeat is not None:
        ticket.heartbeat.cancel()
        await gather(ticket.heartbeat, return_exceptions=True)


async def _keep_lock_alive(ticket_id: str, owner: Task[object]) -> None:
    """Renew the upload lease; abort the upload if lease ownership cannot be confirmed."""

    try:
        while True:
            await sleep(_HEARTBEAT)
            renewed = await Redis.redis.eval(_RENEW_LOCK, 1, _LOCK, ticket_id, _LOCK_TTL_MS)
            if renewed:
                continue
            owner.cancel()
            return
    except CancelledError:
        raise
    except Exception:
        owner.cancel()


@asynccontextmanager
async def ingestion_upload_slot(ticket: IngestionUploadTicket) -> AsyncIterator[None]:
    """Wait for a FIFO turn and hold the single shared upload lease until the file is saved."""

    acquired = False
    ticket_heartbeat = ticket.heartbeat
    lock_heartbeat: Task[None] | None = None

    try:
        while True:
            if ticket.closed:
                raise RuntimeError("The ingestion upload ticket was cancelled or expired")
            result = int(await Redis.redis.eval(
                _CLAIM, 3, _QUEUE, _LOCK, ticket.key,
                ticket.ticket_id, _LOCK_TTL_MS, _PREFIX,
            ))
            if result == 1:
                acquired = True
                ticket.claimed = True
                write_log(
                    "INFO", Database, "UPLOAD QUEUE", ticket.username or "system",
                    f"Acquired upload slot ticket={ticket.ticket_id} name={ticket.label!r}.",
                )
                break
            if result < 0:
                raise RuntimeError("The ingestion upload ticket expired before it reached the front of the queue")
            await sleep(0.2)

        if ticket_heartbeat is not None:
            ticket_heartbeat.cancel()
            await gather(ticket_heartbeat, return_exceptions=True)
            ticket.heartbeat = None

        owner = current_task()
        if owner is None:
            raise RuntimeError("An upload slot requires an active asyncio task")
        lock_heartbeat = create_task(
            _keep_lock_alive(ticket.ticket_id, owner),
            name=f"ingestion-lock-{ticket.ticket_id}",
        )
        yield
    finally:
        ticket.closed = True
        if ticket_heartbeat is not None and not ticket_heartbeat.done():
            ticket_heartbeat.cancel()
            await gather(ticket_heartbeat, return_exceptions=True)
        if lock_heartbeat is not None:
            lock_heartbeat.cancel()
            await gather(lock_heartbeat, return_exceptions=True)
        if acquired:
            await Redis.redis.eval(_RELEASE_LOCK, 1, _LOCK, ticket.ticket_id)
            write_log(
                "INFO", Database, "UPLOAD QUEUE", ticket.username or "system",
                f"Released upload slot ticket={ticket.ticket_id} name={ticket.label!r}.",
            )
        else:
            await Redis.redis.eval(_CANCEL, 2, _QUEUE, ticket.key, ticket.ticket_id)
