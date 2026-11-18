"""Centralized bounded concurrency for downloads from remote storage parts.

The manager fetches one remote link at a time and yields its bytes in original
order, so consumers can stream files and ZIP archives without staging whole files
or archives on disk.
"""

from __future__ import annotations

from asyncio import Semaphore, Task, create_task, gather
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Final, TypeVar

T = TypeVar("T")

DOWNLOAD_CONCURRENCY: Final[int] = 1
_DOWNLOAD_SLOTS: Final[Semaphore] = Semaphore(DOWNLOAD_CONCURRENCY)


async def download_with_slot(operation: Callable[[], Awaitable[T]]) -> T:
    """Run one remote download under the global download concurrency limit."""

    async with _DOWNLOAD_SLOTS:
        return await operation()


async def ordered_parallel_downloads(
    indices: range,
    operation: Callable[[int], Awaitable[T]],
    *,
    concurrency: int = DOWNLOAD_CONCURRENCY,
) -> AsyncGenerator[T]:
    """Fetch links sequentially and yield results in index order."""

    if concurrency < 1:
        raise ValueError("Download concurrency must be at least one")

    iterator = iter(indices)
    pending: dict[int, Task[T]] = {}
    next_index = next(iterator, None)

    while next_index is not None and len(pending) < concurrency:
        pending[next_index] = create_task(download_with_slot(lambda index=next_index: operation(index)))
        next_index = next(iterator, None)

    try:
        for index in indices:
            task = pending.pop(index, None)
            if task is None:
                raise RuntimeError(f"Download window lost item {index}")
            result = await task
            yield result
            if next_index is not None:
                pending[next_index] = create_task(download_with_slot(lambda item=next_index: operation(item)))
                next_index = next(iterator, None)
    finally:
        for task in pending.values():
            if not task.done():
                task.cancel()
        if pending:
            await gather(*pending.values(), return_exceptions=True)
