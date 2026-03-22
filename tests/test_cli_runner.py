from __future__ import annotations

import sys

import pytest

from proxy_agent.services.cli_runner import (
    AgentCliError,
    build_argv,
    run_agent_cli,
    stream_agent_cli,
    wrap_agent_argv_for_stdbuf,
)


def test_build_argv_with_prompt_placeholder() -> None:
    assert build_argv("echo", "-p {prompt}", "hello") == ["echo", "-p", "hello"]


def test_build_argv_prompt_with_spaces_is_single_arg() -> None:
    argv = build_argv("echo", "-p {prompt}", "hello world")
    assert argv == ["echo", "-p", "hello world"]


def test_build_argv_prefix_and_suffix() -> None:
    argv = build_argv("echo", "--before {prompt} --after", "mid")
    assert argv == ["echo", "--before", "mid", "--after"]


def test_build_argv_without_placeholder_appends_prompt() -> None:
    argv = build_argv("echo", "-n", "x")
    assert argv == ["echo", "-n", "x"]


def test_build_argv_empty_template_appends_prompt() -> None:
    argv = build_argv("echo", "", "only")
    assert argv == ["echo", "only"]


def test_wrap_stdbuf_inserts_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "proxy_agent.services.cli_runner.shutil.which",
        lambda name: "/usr/bin/stdbuf" if name == "stdbuf" else None,
    )
    assert wrap_agent_argv_for_stdbuf(["echo", "x"], True) == [
        "/usr/bin/stdbuf",
        "-oL",
        "-eL",
        "echo",
        "x",
    ]


def test_wrap_stdbuf_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "proxy_agent.services.cli_runner.shutil.which",
        lambda name: "/usr/bin/stdbuf" if name == "stdbuf" else None,
    )
    assert wrap_agent_argv_for_stdbuf(["echo", "x"], False) == ["echo", "x"]


def test_wrap_stdbuf_skips_when_stdbuf_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("proxy_agent.services.cli_runner.shutil.which", lambda _name: None)
    assert wrap_agent_argv_for_stdbuf(["echo", "x"], True) == ["echo", "x"]


def test_wrap_stdbuf_skips_if_already_stdbuf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "proxy_agent.services.cli_runner.shutil.which",
        lambda name: "/bin/stdbuf" if name == "stdbuf" else None,
    )
    orig = ["stdbuf", "-o0", "echo", "hi"]
    assert wrap_agent_argv_for_stdbuf(orig, True) == orig


@pytest.mark.asyncio
async def test_run_agent_cli_echo(tmp_path) -> None:
    out = await run_agent_cli(["echo", "hello"], cwd=tmp_path, timeout_sec=5.0)
    assert out == "hello"


@pytest.mark.asyncio
async def test_run_agent_cli_nonzero_exit(tmp_path) -> None:
    with pytest.raises(AgentCliError) as exc_info:
        await run_agent_cli(["/bin/false"], cwd=tmp_path, timeout_sec=5.0)
    assert exc_info.value.exit_code == 1


@pytest.mark.asyncio
async def test_run_agent_cli_timeout(tmp_path) -> None:
    with pytest.raises(AgentCliError, match="timed out"):
        await run_agent_cli(["sleep", "60"], cwd=tmp_path, timeout_sec=0.1)


@pytest.mark.asyncio
async def test_stream_agent_cli_two_lines_line_mode(tmp_path) -> None:
    parts: list[str] = []
    async for p in stream_agent_cli(
        ["bash", "-c", "printf 'a\nb\n'"],
        cwd=tmp_path,
        timeout_sec=5.0,
        stdout_chunk_size=0,
    ):
        parts.append(p)
    assert parts == ["a\n", "b\n"]


@pytest.mark.asyncio
async def test_stream_agent_cli_chunk_mode_concat(tmp_path) -> None:
    parts: list[str] = []
    async for p in stream_agent_cli(
        ["bash", "-c", "printf 'a\nb\n'"],
        cwd=tmp_path,
        timeout_sec=5.0,
        stdout_chunk_size=4096,
    ):
        parts.append(p)
    assert "".join(parts) == "a\nb\n"


@pytest.mark.asyncio
async def test_stream_agent_cli_nonzero_appends_exit_message(tmp_path) -> None:
    parts: list[str] = []
    async for p in stream_agent_cli(["/bin/false"], cwd=tmp_path, timeout_sec=5.0):
        parts.append(p)
    assert len(parts) == 1
    assert "[agent exited with code 1]" in parts[0]


@pytest.mark.asyncio
async def test_stream_agent_cli_timeout(tmp_path) -> None:
    with pytest.raises(AgentCliError, match="timed out"):
        async for _ in stream_agent_cli(
            ["sleep", "30"],
            cwd=tmp_path,
            timeout_sec=0.15,
        ):
            pass


@pytest.mark.asyncio
async def test_run_agent_cli_stderr_only_success(tmp_path) -> None:
    out = await run_agent_cli(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('err-only')",
        ],
        cwd=tmp_path,
        timeout_sec=10.0,
    )
    assert out == "err-only"
