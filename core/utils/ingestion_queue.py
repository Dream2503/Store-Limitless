"""Redis-backed FIFO queue with bounded concurrent whole-file uploads.

Tickets are admitted in FIFO order. Up to UPLOAD_CONCURRENCY tickets may hold
leases at once, allowing independent files to upload concurrently while keeping
provider-level chunk concurrency in each DataCenter implementation.
"""

from __future__ import annotations

import os
from asyncio import Task, CancelledError, create_task, gather, sleep
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final
from collections.abc import AsyncIterator
from uuid import uuid4

from backend.database.redis_server import Redis
from core.data_center import Database
from core.utils import write_log

_QUEUE: Final[str] = "ingestion:upload:queue"
_SEQUENCE: Final[str] = "ingestion:upload:sequence"
_ACTIVE: Final[str] = "ingestion:upload:active"
_PREFIX: Final[str] = "ingestion:upload:ticket:"
_TICKET_TTL: Final[int] = 300
_LEASE_TTL_MS: Final[int] = 120_000
_HEARTBEAT: Final[float] = 10.0

try:
    UPLOAD_CONCURRENCY: Final[int] = max(
        1, min(8, int(os.environ.get("STORELIMITLESS_FILE_UPLOADS", "3")))
    )
except ValueError:
    UPLOAD_CONCURRENCY = 3

_ENQUEUE: Final[str] = """
-- storelimitless-ingestion-enqueue
local sequence = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[1], sequence, ARGV[1])
return sequence
"""
_CLAIM: Final[str] = """
-- storelimitless-ingestion-claim
local head
while true do
    head = redis.call('ZRANGE', KEYS[1], 0, 0)
    if #head == 0 then return -1 end
    if redis.call('EXISTS', ARGV[4] .. head[1]) == 0 then
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
local active = redis.call('ZCARD', KEYS[2])
if active >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[2], tonumber(ARGV[2]), ARGV[1])
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[3])
return 1
"""
_RENEW_TICKET: Final[str] = """
-- storelimitless-ingestion-renew-ticket
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
return redis.call('EXPIRE', KEYS[1], ARGV[1])
"""
_RENEW_LEASE: Final[str] = """
-- storelimitless-ingestion-renew-lease
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score then return 0 end
redis.call('ZADD', KEYS[1], tonumber(ARGV[2]), ARGV[1])
return redis.call('PEXPIRE', KEYS[1], ARGV[3])
"""
_RELEASE_LEASE: Final[str] = """
-- storelimitless-ingestion-release-lease
return redis.call('ZREM', KEYS[1], ARGV[1])
"""
_CANCEL: Final[str] = """
-- storelimitless-ingestion-cancel-ticket
redis.call('ZREM', KEYS[1], ARGV[1])
return redis.call('DEL', KEYS[2])
"""
_REAP_EXPIRED: Final[str] = """
-- storelimitless-ingestion-reap-expired-leases
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, ticket in ipairs(expired) do
    redis.call('ZREM', KEYS[1], ticket)
end
return #expired
"""


@dataclass(slots=True)
class IngestionUploadTicket:
    """One FIFO position for a file awaiting an upload slot."""

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
        return


async def enqueue_ingestion_upload(label: str = "upload", username: str = "") -> IngestionUploadTicket:
    """Atomically append a named file to the shared Redis FIFO."""

    ticket_id = uuid4().hex
    ticket = IngestionUploadTicket(
        ticket_id,
        f"{_PREFIX}{ticket_id}",
        label=str(label or "upload")[:160],
        username=str(username or ""),
    )
    await Redis.redis.eval(_ENQUEUE, 3, _QUEUE, _SEQUENCE, ticket.key, ticket_id, _TICKET_TTL)
    ticket.heartbeat = create_task(_keep_ticket_alive(ticket), name=f"ingestion-ticket-{ticket_id}")
    write_log(
        "INFO", Database, "UPLOAD QUEUE", ticket.username or "system",
        f"Queued ticket={ticket.ticket_id} name={ticket.label!r}; waiting for one of "
        f"{UPLOAD_CONCURRENCY} concurrent file-upload slots.",
    )
    return ticket


async def cancel_ingestion_upload(ticket: IngestionUploadTicket) -> None:
    """Remove a not-yet-claimed ticket after cancellation or failed queue handoff."""

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


async def _keep_lease_alive(ticket: IngestionUploadTicket, owner: Task[object]) -> None:
    """Renew a concurrent upload lease; abort if Redis no longer confirms ownership."""

    try:
        while True:
            await sleep(_HEARTBEAT)
            renewed = await Redis.redis.eval(
                _RENEW_LEASE, 1, _ACTIVE, ticket.ticket_id, ticket.ticket_id,
                _LEASE_TTL_MS, _LEASE_TTL_MS,
            )
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
    """Acquire a FIFO upload lease, with bounded whole-file concurrency."""

    acquired = False
    ticket_heartbeat = ticket.heartbeat
    lease_heartbeat: Task[None] | None = None

    try:
        while True:
            if ticket.closed:
                raise RuntimeError("The ingestion upload ticket was cancelled or expired")

            result = int(await Redis.redis.eval(
                _CLAIM,
                3,
                _QUEUE,
                _ACTIVE,
                ticket.key,
                ticket.ticket_id,
                _LEASE_TTL_MS,
                UPLOAD_CONCURRENCY,
                _PREFIX,
            ))
            if result == 1:
                acquired = True
                ticket.claimed = True
                write_log(
                    "INFO", Database, "UPLOAD QUEUE", ticket.username or "system",
                    f"Acquired upload slot ticket={ticket.ticket_id} name={ticket.label!r}; "
                    f"active limit={UPLOAD_CONCURRENCY}.",
                )
                break
            if result < 0:
                raise RuntimeError("The ingestion upload ticket expired before it reached the front of the queue")
            await sleep(0.2)

        if ticket_heartbeat is not None:
            ticket_heartbeat.cancel()
            await gather(ticket_heartbeat, return_exceptions=True)
            ticket.heartbeat = None

        from time import time
        lease_heartbeat = create_task(
            _keep_lease_alive(ticket, _owner_task()),
            name=f"ingestion-lease-{ticket.ticket_id}",
        )
        yield
    finally:
        ticket.closed = True
        if ticket_heartbeat is not None and not ticket_heartbeat.done():
            ticket_heartbeat.cancel()
            await gather(ticket_heartbeat, return_exceptions=True)
        if lease_heartbeat is not None:
            lease_heartbeat.cancel()
            await gather(lease_heartbeat, return_exceptions=True)
        if acquired:
            await Redis.redis.eval(_RELEASE_LEASE, 1, _ACTIVE, ticket.ticket_id)
            write_log(
                "INFO", Database, "UPLOAD QUEUE", ticket.username or "system",
                f"Released upload slot ticket={ticket.ticket_id} name={ticket.label!r}.",
            )
        else:
            await Redis.redis.eval(_CANCEL, 2, _QUEUE, ticket.key, ticket.ticket_id)


def _owner_task() -> Task[object]:
    """Return current task to the lease heartbeat without losing typing information."""

    from asyncio import current_task
    owner = current_task()
    if owner is None:
        raise RuntimeError("An upload slot requires an active asyncio task")
    return owner
