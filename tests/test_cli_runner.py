from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from proxy_agent.app import (
    AgentCliError,
    build_argv,
    run_agent_cli,
    stream_agent_cli,
    wrap_agent_argv_for_stdbuf,
)


def test_build_argv_inserts_prompt_in_middle() -> None:
    assert build_argv("echo", "before {prompt} after", "P") == ["echo", "before", "P", "after"]


def test_build_argv_appends_when_no_placeholder() -> None:
    assert build_argv("echo", "x", "hello") == ["echo", "x", "hello"]


@pytest.mark.asyncio
async def test_run_agent_cli_echo(tmp_path: Path) -> None:
    out = await run_agent_cli(["echo", "hello"], cwd=tmp_path, timeout_sec=5.0)
    assert out == "hello"


@pytest.mark.asyncio
async def test_run_agent_cli_nonzero_exit(tmp_path: Path) -> None:
    with pytest.raises(AgentCliError, match="non-zero"):
        await run_agent_cli(["/bin/false"], cwd=tmp_path, timeout_sec=5.0)


@pytest.mark.asyncio
async def test_stream_agent_cli_two_lines_line_mode(tmp_path: Path) -> None:
    parts: list[str] = []
    async for p in stream_agent_cli(
        ["sh", "-c", "printf 'a\\nb\\n'"],
        cwd=tmp_path,
        timeout_sec=5.0,
        stdout_chunk_size=0,
    ):
        parts.append(p)
    assert "".join(parts) == "a\nb\n"


@pytest.mark.asyncio
async def test_stream_agent_cli_nonzero_appends_exit_message(tmp_path: Path) -> None:
    buf = ""
    async for p in stream_agent_cli(["/bin/false"], cwd=tmp_path, timeout_sec=5.0):
        buf += p
    assert "exited with code" in buf


@pytest.mark.asyncio
async def test_run_agent_cli_stderr_only_success(tmp_path: Path) -> None:
    out = await run_agent_cli(
        ["sh", "-c", "echo err >&2"],
        cwd=tmp_path,
        timeout_sec=5.0,
    )
    assert out == "err"
