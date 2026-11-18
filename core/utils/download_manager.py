"""Centralized bounded concurrency for downloads from remote storage parts.

Downloads are limited independently per provider and yielded in original order,
so streaming and cross-provider copies can prefetch chunks without reordering data.
"""

from __future__ import annotations

from asyncio import Semaphore, Task, create_task, gather
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Final, TypeVar

T = TypeVar("T")

DOWNLOAD_CONCURRENCY: Final[int] = 1
_DEFAULT_SLOT_KEY: Final[str] = "default"
_DOWNLOAD_SLOTS: dict[str, Semaphore] = {}
_DOWNLOAD_SLOT_LIMITS: dict[str, int] = {}


def _get_download_slot(slot_key: str, concurrency: int) -> Semaphore:
    """Return the shared semaphore for one provider, creating it on first use."""

    if not slot_key:
        raise ValueError("Download slot key must not be empty")

    if concurrency < 1:
        raise ValueError("Download concurrency must be at least one")

    slot = _DOWNLOAD_SLOTS.get(slot_key)

    if slot is None:
        slot = Semaphore(concurrency)
        _DOWNLOAD_SLOTS[slot_key] = slot
        _DOWNLOAD_SLOT_LIMITS[slot_key] = concurrency

    elif _DOWNLOAD_SLOT_LIMITS[slot_key] != concurrency:
        raise ValueError(
            f"Download concurrency for {slot_key!r} was already initialized "
            f"as {_DOWNLOAD_SLOT_LIMITS[slot_key]}, not {concurrency}"
        )

    return slot


async def download_with_slot(
    operation: Callable[[], Awaitable[T]],
    *,
    slot_key: str = _DEFAULT_SLOT_KEY,
    concurrency: int = DOWNLOAD_CONCURRENCY,
) -> T:
    """Run one remote download under its provider's shared concurrency limit."""

    slot = _get_download_slot(slot_key, concurrency)

    async with slot:
        return await operation()


async def ordered_parallel_downloads(
    indices: range,
    operation: Callable[[int], Awaitable[T]],
    *,
    concurrency: int = DOWNLOAD_CONCURRENCY,
    slot_key: str = _DEFAULT_SLOT_KEY,
) -> AsyncGenerator[T]:
    """Fetch up to the configured number of links concurrently and yield in index order."""

    if concurrency < 1:
        raise ValueError("Download concurrency must be at least one")

    iterator = iter(indices)
    pending: dict[int, Task[T]] = {}
    next_index = next(iterator, None)

    while next_index is not None and len(pending) < concurrency:
        pending[next_index] = create_task(
            download_with_slot(
                lambda index=next_index: operation(index),
                slot_key=slot_key,
                concurrency=concurrency,
            )
        )
        next_index = next(iterator, None)

    try:
        for index in indices:
            task = pending.pop(index, None)

            if task is None:
                raise RuntimeError(f"Download window lost item {index}")

            result = await task
            yield result

            if next_index is not None:
                pending[next_index] = create_task(
                    download_with_slot(
                        lambda item=next_index: operation(item),
                        slot_key=slot_key,
                        concurrency=concurrency,
                    )
                )
                next_index = next(iterator, None)

    finally:
        for task in pending.values():
            if not task.done():
                task.cancel()

        if pending:
            await gather(*pending.values(), return_exceptions=True)
