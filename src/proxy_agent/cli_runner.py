"""Run the configured agent CLI as a subprocess (blocking or streaming)."""

from __future__ import annotations

import asyncio
import codecs
import errno
import os
import shlex
import subprocess
import shutil
import signal
import sys
from collections.abc import AsyncIterator
from pathlib import Path

_STDERR_TAIL_MAX = 8000
_STDERR_DRAIN_MAX_SEC = 12.0
_AGENT_STDIO_STREAM_LIMIT_DEFAULT = 16 * 1024 * 1024


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    except OSError as e:
        if e.errno != errno.ESRCH:
            raise


def _kill_agent_tree(proc: asyncio.subprocess.Process) -> None:
    pid = proc.pid
    if pid is not None and sys.platform != "win32":
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError as e:
            if e.errno not in (errno.ESRCH, errno.EPERM):
                raise
    _kill_process(proc)


class AgentCliError(Exception):
    def __init__(self, message: str, *, exit_code: int | None, stderr: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


def build_argv(command: str, args_template: str, prompt: str) -> list[str]:
    template = args_template.strip()
    if "{prompt}" in template:
        pre, _, post = template.partition("{prompt}")
        argv: list[str] = []
        if pre.strip():
            argv.extend(shlex.split(pre))
        argv.append(prompt)
        if post.strip():
            argv.extend(shlex.split(post))
    else:
        argv = shlex.split(template) if template else []
        argv.append(prompt)
    return [command, *argv]


def wrap_agent_argv_for_stdbuf(argv: list[str], use_stdbuf: bool) -> list[str]:
    if not use_stdbuf or len(argv) == 0:
        return argv
    exe = argv[0]
    if exe == "stdbuf" or exe.endswith("/stdbuf"):
        return argv
    stdbuf = shutil.which("stdbuf")
    if not stdbuf:
        return argv
    return [stdbuf, "-oL", "-eL", *argv]


async def run_agent_cli(
    argv: list[str],
    *,
    cwd: Path | None,
    timeout_sec: float,
    stream_reader_limit: int = _AGENT_STDIO_STREAM_LIMIT_DEFAULT,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
        limit=stream_reader_limit,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        _kill_agent_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            pass
        raise AgentCliError(
            f"Agent CLI timed out after {timeout_sec} seconds",
            exit_code=None,
            stderr="",
        ) from None

    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    code = proc.returncode
    if code != 0:
        raise AgentCliError(
            "Agent CLI exited with a non-zero status",
            exit_code=code,
            stderr=stderr or stdout,
        )
    text = stdout.rstrip("\n")
    if not text and stderr.strip():
        return stderr.rstrip("\n")
    return text


async def stream_agent_cli(
    argv: list[str],
    *,
    cwd: Path | None,
    timeout_sec: float,
    stdout_chunk_size: int = 4096,
    eof_process_wait_sec: float = 30.0,
    stream_reader_limit: int = _AGENT_STDIO_STREAM_LIMIT_DEFAULT,
) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec

    def remaining() -> float:
        return max(0.0, deadline - loop.time())

    proc: asyncio.subprocess.Process | None = None
    drain_task: asyncio.Task[None] | None = None

    async def cancel_drain() -> None:
        nonlocal drain_task
        if drain_task and not drain_task.done():
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass

    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                start_new_session=True,
                limit=stream_reader_limit,
            )
        except OSError as e:
            yield f"[failed to start agent: {e}]\n"
            return

        stderr_parts: list[bytes] = []

        async def drain_stderr() -> None:
            assert proc is not None and proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(65536)
                if not chunk:
                    break
                stderr_parts.append(chunk)

        drain_task = asyncio.create_task(drain_stderr())
        any_stdout = False
        assert proc.stdout is not None

        if stdout_chunk_size <= 0:
            while True:
                if remaining() <= 0:
                    raise TimeoutError
                try:
                    line_b = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=remaining(),
                    )
                except TimeoutError:
                    _kill_agent_tree(proc)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except TimeoutError:
                        pass
                    await cancel_drain()
                    raise AgentCliError(
                        f"Agent CLI timed out after {timeout_sec} seconds",
                        exit_code=None,
                        stderr="",
                    ) from None
                if not line_b:
                    break
                any_stdout = True
                yield line_b.decode(errors="replace")
        else:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                if remaining() <= 0:
                    raise TimeoutError
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.read(stdout_chunk_size),
                        timeout=remaining(),
                    )
                except TimeoutError:
                    _kill_agent_tree(proc)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except TimeoutError:
                        pass
                    await cancel_drain()
                    raise AgentCliError(
                        f"Agent CLI timed out after {timeout_sec} seconds",
                        exit_code=None,
                        stderr="",
                    ) from None
                if not raw:
                    break
                any_stdout = True
                text = decoder.decode(raw, final=False)
                if text:
                    yield text
            tail = decoder.decode(b"", final=True)
            if tail:
                any_stdout = True
                yield tail

        if remaining() <= 0:
            _kill_agent_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                pass
            await cancel_drain()
            raise AgentCliError(
                f"Agent CLI timed out after {timeout_sec} seconds",
                exit_code=None,
                stderr="",
            ) from None

        if eof_process_wait_sec > 0:
            wait_cap = min(remaining(), eof_process_wait_sec)
        else:
            wait_cap = remaining()

        killed_stuck_after_stdout = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=wait_cap)
        except TimeoutError:
            _kill_agent_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=min(5.0, max(0.0, remaining())))
            except TimeoutError:
                pass
            if not any_stdout:
                await cancel_drain()
                raise AgentCliError(
                    f"Agent CLI timed out after {timeout_sec} seconds",
                    exit_code=None,
                    stderr="",
                ) from None
            killed_stuck_after_stdout = True

        if remaining() <= 0:
            await cancel_drain()
            raise AgentCliError(
                f"Agent CLI timed out after {timeout_sec} seconds",
                exit_code=None,
                stderr="",
            ) from None
        drain_cap = min(_STDERR_DRAIN_MAX_SEC, max(0.0, remaining()))
        try:
            await asyncio.wait_for(drain_task, timeout=drain_cap)
        except TimeoutError:
            await cancel_drain()

        stderr = b"".join(stderr_parts).decode(errors="replace")
        code = proc.returncode or 0
        if killed_stuck_after_stdout and any_stdout:
            code = 0

        if code != 0:
            tail = (stderr or "")[:_STDERR_TAIL_MAX]
            yield f"\n\n[agent exited with code {code}]\n{tail}"
        elif not any_stdout and stderr.strip():
            yield stderr if stderr.endswith("\n") else stderr + "\n"
    finally:
        if proc is not None and proc.returncode is None:
            _kill_agent_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                pass
        if drain_task and not drain_task.done():
            await cancel_drain()
