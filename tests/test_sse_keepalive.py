from __future__ import annotations

import asyncio

import pytest

from proxy_agent.app import merge_async_iter_with_sse_comments


async def _slow_stream() -> str:
    yield "a"
    await asyncio.sleep(0.08)
    yield "b"


@pytest.mark.asyncio
async def test_merge_emits_sse_comments_while_waiting() -> None:
    out: list[str | bytes] = []
    async for x in merge_async_iter_with_sse_comments(lambda: _slow_stream(), 0.03):
        out.append(x)
    assert b":\n\n" in out
    assert "a" in out and "b" in out


@pytest.mark.asyncio
async def test_merge_disabled_when_interval_zero() -> None:
    out: list[str | bytes] = []
    async for x in merge_async_iter_with_sse_comments(lambda: _slow_stream(), 0.0):
        out.append(x)
    assert b":\n\n" not in out
    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_merge_short_interval_like_production_default() -> None:
    out: list[str | bytes] = []
    async for x in merge_async_iter_with_sse_comments(lambda: _slow_stream(), 0.05):
        out.append(x)
    assert b":\n\n" in out
