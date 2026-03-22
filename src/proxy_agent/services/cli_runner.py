from __future__ import annotations

import asyncio
import shlex
from pathlib import Path


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


async def run_agent_cli(
    argv: list[str],
    *,
    cwd: Path | None,
    timeout_sec: float,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_sec,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
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
