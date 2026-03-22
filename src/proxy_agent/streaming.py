"""Merge SSE keep-alives and turn agent stdout into chat.completion SSE chunks."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from proxy_agent.cli_runner import AgentCliError, stream_agent_cli
from proxy_agent.config import Settings
from proxy_agent.cursor_stream import _CursorNdjsonState, _iter_ndjson_stdout_deltas
from proxy_agent.sse import (
    format_sse,
    stream_chunk_content,
    stream_chunk_finish,
    stream_chunk_role_assistant,
)

_SSE_COMMENT = b":\n\n"


async def merge_async_iter_with_sse_comments(
    factory: Callable[[], AsyncIterator[str]],
    interval_sec: float,
) -> AsyncIterator[str | bytes]:
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


ClientDisconnectedFn = Callable[[], Awaitable[bool]]


async def iter_chat_completion_sse(
    *,
    argv: list[str],
    cwd: Path | None,
    model: str,
    prompt: str,
    settings: Settings,
    client_disconnected: ClientDisconnectedFn | None = None,
) -> AsyncIterator[bytes]:
    stream_chunk_size = (
        0 if settings.agent_stream_protocol == "cursor_ndjson" else settings.agent_stream_stdout_chunk_size
    )

    completion_id = "chatcmpl-" + secrets.token_hex(12)
    created = int(time.time())
    streamed_completion_chars = 0

    yield format_sse(
        stream_chunk_role_assistant(
            completion_id=completion_id,
            created=created,
            model=model,
        )
    )

    async def _gone() -> bool:
        if client_disconnected is None:
            return False
        return await client_disconnected()

    try:

        def _cli_stream():
            return stream_agent_cli(
                argv,
                cwd=cwd,
                timeout_sec=settings.agent_timeout_sec,
                stdout_chunk_size=stream_chunk_size,
                eof_process_wait_sec=settings.agent_stream_eof_process_wait_sec,
                stream_reader_limit=settings.agent_subprocess_stream_limit,
            )

        cursor_state = _CursorNdjsonState()
        async for piece in merge_async_iter_with_sse_comments(
            _cli_stream,
            settings.agent_sse_comment_interval_sec,
        ):
            if await _gone():
                break
            if cursor_state.run_finished:
                break
            if isinstance(piece, bytes):
                yield piece
            elif not piece:
                continue
            elif settings.agent_stream_protocol == "passthrough":
                streamed_completion_chars += len(piece)
                yield format_sse(
                    stream_chunk_content(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        content=piece,
                    )
                )
            else:
                for d in _iter_ndjson_stdout_deltas(piece, cursor_state):
                    streamed_completion_chars += len(d)
                    yield format_sse(
                        stream_chunk_content(
                            completion_id=completion_id,
                            created=created,
                            model=model,
                            content=d,
                        )
                    )
                if cursor_state.run_finished:
                    break
    except AgentCliError as e:
        err_text = str(e)
        if e.stderr:
            err_text += "\n" + e.stderr[:4000]
        err_chunk = err_text + "\n"
        streamed_completion_chars += len(err_chunk)
        yield format_sse(
            stream_chunk_content(
                completion_id=completion_id,
                created=created,
                model=model,
                content=err_chunk,
            )
        )
    except Exception as e:
        err_chunk = f"[proxy-agent stream error] {e!s}\n"
        streamed_completion_chars += len(err_chunk)
        yield format_sse(
            stream_chunk_content(
                completion_id=completion_id,
                created=created,
                model=model,
                content=err_chunk,
            )
        )

    prompt_tokens_est = max(1, len(prompt) // 4)
    completion_tokens_est = max(1, streamed_completion_chars // 4)
    usage = {
        "prompt_tokens": prompt_tokens_est,
        "completion_tokens": completion_tokens_est,
        "total_tokens": prompt_tokens_est + completion_tokens_est,
    }
    yield format_sse(
        stream_chunk_finish(
            completion_id=completion_id,
            created=created,
            model=model,
            usage=usage,
        )
    )
    yield b"data: [DONE]\n\n"
