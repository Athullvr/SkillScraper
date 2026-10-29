"""Bounded offloading for synchronous catalog operations used by async MCP tools."""

from __future__ import annotations

import asyncio
import contextvars
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

MAX_BLOCKING_WORKERS = 8
_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_BLOCKING_WORKERS,
    thread_name_prefix="skillscout-catalog",
)
_LIMITERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)
_LIMITER_LOCK = threading.Lock()


def _limiter_for(loop: asyncio.AbstractEventLoop) -> asyncio.Semaphore:
    with _LIMITER_LOCK:
        limiter = _LIMITERS.get(loop)
        if limiter is None:
            limiter = asyncio.Semaphore(MAX_BLOCKING_WORKERS)
            _LIMITERS[loop] = limiter
        return limiter


async def run_bounded_sync[T](function: Callable[[], T]) -> T:
    """Run a synchronous workflow off-loop with a process-wide bounded worker pool."""
    loop = asyncio.get_running_loop()
    async with _limiter_for(loop):
        context = contextvars.copy_context()
        future = loop.run_in_executor(_EXECUTOR, context.run, function)
        try:
            # Polling also lets the request loop progress when the host runtime does
            # not promptly wake for a completion from its worker executor.
            while not future.done():
                await asyncio.sleep(0.01)
            return future.result()
        except asyncio.CancelledError:
            # A disconnected HTTP client must not release capacity while its read-only
            # catalog work is still running in a thread.
            while not future.done():
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    continue
            if future.done():
                future.exception()
            raise
