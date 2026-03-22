"""SSE comment keep-alive (HTML5 / WHATWG): lines starting with ``:`` are ignored by clients."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

_SSE_COMMENT = b":\n\n"


async def merge_async_iter_with_sse_comments(
    factory: Callable[[], AsyncIterator[str]],
    interval_sec: float,
) -> AsyncIterator[str | bytes]:
    """While waiting for the next item from *factory*, periodically yield SSE comment bytes.

    OpenAI stream chunks are unchanged; extra ``b':\\n\\n'`` lines keep proxies from closing idle
    connections. Compatible with spec: https://html.spec.whatwg.org/multipage/server-sent-events.html
    """

    if interval_sec <= 0:
        async for item in factory():
            yield item
        return

    it = factory().__aiter__()
    read_task: asyncio.Task = asyncio.create_task(it.__anext__())

    try:
        while True:
            sleep_task = asyncio.create_task(asyncio.sleep(interval_sec))
            done, _ = await asyncio.wait(
                {read_task, sleep_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if sleep_task in done and not read_task.done():
                yield _SSE_COMMENT
                continue

            sleep_task.cancel()
            try:
                item = read_task.result()
            except StopAsyncIteration:
                break
            yield item
            read_task = asyncio.create_task(it.__anext__())
    finally:
        if not read_task.done():
            read_task.cancel()
            try:
                await read_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
