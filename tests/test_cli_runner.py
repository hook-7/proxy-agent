from __future__ import annotations

import sys

import pytest

from proxy_agent.services.cli_runner import AgentCliError, build_argv, run_agent_cli


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
