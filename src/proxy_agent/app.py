"""proxy-agent: OpenAI-compatible HTTP API for a local agent CLI (single module)."""

from __future__ import annotations

import asyncio
import codecs
import errno
import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_AGENT_SUBPROCESS_STREAM_LIMIT_MIN = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    agent_command: str = "agent"
    agent_args_standard_template: str = "-p --output-format text {prompt}"
    agent_args_stream_template: str = (
        "-p --output-format stream-json --stream-partial-output {prompt}"
    )
    agent_stream_protocol: Literal["cursor_ndjson", "passthrough"] = "cursor_ndjson"
    agent_standard_output_format: Literal["text", "json"] = "text"
    agent_cwd: Path | None = None
    agent_timeout_sec: float = 300.0
    agent_subprocess_stream_limit: int = Field(
        default=16 * 1024 * 1024,
        ge=_AGENT_SUBPROCESS_STREAM_LIMIT_MIN,
    )
    agent_stream_stdout_chunk_size: int = 4096
    agent_use_stdbuf: bool = True
    agent_messages_format: Literal["transcript", "last_user_only"] = "transcript"
    agent_max_prompt_chars: int = 0
    agent_sse_comment_interval_sec: float = 15.0
    agent_stream_eof_process_wait_sec: float = 30.0
    agent_stream_check_client_disconnect: bool = False
    api_key: str | None = None
    default_model: str = "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Subprocess CLI
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Cursor stream-json helpers
# ---------------------------------------------------------------------------


def is_cursor_stream_result_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    try:
        obj: Any = json.loads(s)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and obj.get("type") == "result"


def assistant_text_from_stream_json_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    try:
        obj: Any = json.loads(s)
    except json.JSONDecodeError:
        return ""
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return ""
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return ""
    parts = msg.get("content")
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, dict):
            t = p.get("text")
            if isinstance(t, str):
                out.append(t)
    return "".join(out)


def decode_standard_output(text: str, format_: Literal["text", "json"]) -> str:
    raw = text.rstrip("\n")
    if format_ == "text":
        return raw
    try:
        obj: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"Agent standard output is not valid JSON: {e}"
        raise ValueError(msg) from e
    if isinstance(obj, dict) and isinstance(obj.get("result"), str):
        return obj["result"]
    raise ValueError("Agent JSON output must contain a string 'result' field")


@dataclass
class _CursorNdjsonState:
    run_finished: bool = False


def _iter_ndjson_stdout_deltas(piece: str, state: _CursorNdjsonState) -> list[str]:
    out: list[str] = []
    for raw_line in piece.splitlines(keepends=True):
        line_for_json = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        if is_cursor_stream_result_line(line_for_json):
            state.run_finished = True
            return out
        delta = assistant_text_from_stream_json_line(line_for_json)
        if delta:
            out.append(delta)
            continue
        stripped = line_for_json.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            line_out = raw_line if raw_line.endswith("\n") else raw_line + "\n"
            out.append(line_out)
    return out


# ---------------------------------------------------------------------------
# OpenAI-shaped schemas + messages → prompt
# ---------------------------------------------------------------------------

ChatRole = Literal["system", "user", "assistant", "tool", "developer", "function"]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: ChatRole
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    @field_validator("content", mode="before")
    @classmethod
    def coerce_content_list(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return [v]
        return v


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool | None = None

    @field_validator("messages")
    @classmethod
    def messages_non_empty(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if not v:
            raise ValueError("messages must be a non-empty array")
        return v


class ModelInfo(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "proxy-agent"


class ModelsListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelInfo]


def _text_from_multimodal_parts(parts: list[Any], *, strict: bool) -> str:
    texts: list[str] = []
    for part in parts:
        if isinstance(part, str):
            s = part.strip()
            if s:
                texts.append(s)
            continue
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype in ("text", "input_text", "output_text"):
            raw = part.get("text")
            if isinstance(raw, str) and raw.strip():
                texts.append(raw)
            continue
        if ptype in (
            "image_url",
            "input_image",
            "image",
            "file",
            "audio",
            "video",
            "thinking",
            "redacted_thinking",
            "reasoning",
        ):
            continue
        raw = part.get("text")
        if isinstance(raw, str) and raw.strip():
            texts.append(raw)
    if not texts:
        if strict:
            raise ValueError(
                "Last user message has no usable text (multimodal content had no text parts; "
                "images are not passed to the CLI)"
            )
        return ""
    return "\n".join(texts)


_ROLE_LABEL: dict[str, str] = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool",
    "developer": "Developer",
    "function": "Function",
}


def _assistant_without_text_body(msg: ChatMessage) -> str:
    if msg.role != "assistant" or not msg.tool_calls:
        return ""
    snippet = json.dumps(msg.tool_calls, ensure_ascii=False)[:12000]
    return f"[assistant tool_calls]\n{snippet}"


def _message_plain_text(msg: ChatMessage) -> str:
    if msg.content is None:
        return _assistant_without_text_body(msg)
    if isinstance(msg.content, str):
        return msg.content
    extracted = _text_from_multimodal_parts(msg.content, strict=False)
    if extracted.strip():
        return extracted
    if msg.role == "user":
        return (
            "[User: message had no plain-text parts (e.g. only images); "
            "nothing was sent to the CLI]"
        )
    return _assistant_without_text_body(msg)


def extract_last_user_text(messages: list[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        if msg.content is None:
            raise ValueError("Last user message has empty content")
        if isinstance(msg.content, list):
            return _text_from_multimodal_parts(msg.content, strict=True)
        return msg.content
    raise ValueError("No user message found in messages")


MessagesFormat = Literal["transcript", "last_user_only"]


def messages_to_cli_prompt(
    messages: list[ChatMessage],
    *,
    mode: MessagesFormat,
    max_chars: int = 0,
) -> str:
    if mode == "last_user_only":
        return extract_last_user_text(messages)

    blocks: list[str] = []
    for msg in messages:
        label = _ROLE_LABEL.get(msg.role, msg.role)
        text = _message_plain_text(msg).strip()
        if msg.role == "tool" and msg.tool_call_id and text:
            text = f"(tool_call_id={msg.tool_call_id})\n{text}"
        if not text:
            continue
        blocks.append(f"{label}:\n{text}")

    if not blocks:
        raise ValueError("No messages with non-empty text content")

    if not any(m.role == "user" for m in messages):
        raise ValueError("No user message found in messages")

    out = "\n\n".join(blocks)
    if max_chars > 0 and len(out) > max_chars:
        raise ValueError(
            f"Transcript exceeds AGENT_MAX_PROMPT_CHARS ({max_chars}); shorten the conversation "
            "or raise the limit"
        )
    return out


def openai_error_payload(message: str, type_: str = "invalid_request_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": type_}}


def format_sse(data: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8")


def build_stream_chunk(
    *,
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
    }


def stream_chunk_role_assistant(*, completion_id: str, created: int, model: str) -> dict[str, Any]:
    return build_stream_chunk(
        completion_id=completion_id,
        created=created,
        model=model,
        delta={"role": "assistant"},
        finish_reason=None,
    )


def stream_chunk_content(
    *, completion_id: str, created: int, model: str, content: str
) -> dict[str, Any]:
    return build_stream_chunk(
        completion_id=completion_id,
        created=created,
        model=model,
        delta={"content": content},
        finish_reason=None,
    )


def stream_chunk_finish(
    *,
    completion_id: str,
    created: int,
    model: str,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    chunk = build_stream_chunk(
        completion_id=completion_id,
        created=created,
        model=model,
        delta={},
        finish_reason="stop",
    )
    chunk["usage"] = usage if usage is not None else None
    return chunk


def build_chat_completion(*, model: str, content: str, prompt_text: str) -> dict[str, Any]:
    now = int(time.time())
    completion_id = "chatcmpl-" + secrets.token_hex(12)
    prompt_tokens = max(1, len(prompt_text) // 4)
    completion_tokens = max(1, len(content) // 4)
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# SSE keep-alive merge
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="proxy-agent", version="0.1.0")
    app.state.settings = settings if settings is not None else get_settings()

    @app.exception_handler(HTTPException)
    async def openai_style_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": str(detail),
                    "type": "http_error",
                }
            },
        )

    def get_app_settings(request: Request) -> Settings:
        return request.app.state.settings

    async def verify_bearer(
        settings: Settings = Depends(get_app_settings),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> None:
        expected = settings.api_key
        if not expected:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail=openai_error_payload("Unauthorized", type_="unauthorized"),
            )
        token = authorization.removeprefix("Bearer ").strip()
        if token != expected:
            raise HTTPException(
                status_code=401,
                detail=openai_error_payload("Unauthorized", type_="unauthorized"),
            )

    @app.get("/v1/models")
    async def list_models(
        settings: Settings = Depends(get_app_settings),
        _: None = Depends(verify_bearer),
    ) -> ModelsListResponse:
        mid = settings.default_model
        return ModelsListResponse(
            data=[ModelInfo(id=mid, created=int(time.time()))],
        )

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: Request,
        body: ChatCompletionRequest,
        settings: Settings = Depends(get_app_settings),
        _: None = Depends(verify_bearer),
    ) -> JSONResponse | StreamingResponse:
        try:
            prompt = messages_to_cli_prompt(
                body.messages,
                mode=settings.agent_messages_format,
                max_chars=settings.agent_max_prompt_chars,
            )
        except ValueError as e:
            return JSONResponse(status_code=400, content=openai_error_payload(str(e)))

        model = body.model or settings.default_model
        cwd = settings.agent_cwd

        if body.stream is True:
            argv = build_argv(settings.agent_command, settings.agent_args_stream_template, prompt)
            argv = wrap_agent_argv_for_stdbuf(argv, settings.agent_use_stdbuf)
            return StreamingResponse(
                iter_chat_completion_sse(
                    argv=argv,
                    cwd=cwd,
                    model=model,
                    prompt=prompt,
                    settings=settings,
                    client_disconnected=(
                        request.is_disconnected if settings.agent_stream_check_client_disconnect else None
                    ),
                ),
                media_type="text/event-stream; charset=utf-8",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        argv = build_argv(settings.agent_command, settings.agent_args_standard_template, prompt)
        argv = wrap_agent_argv_for_stdbuf(argv, settings.agent_use_stdbuf)
        try:
            raw = await run_agent_cli(
                argv,
                cwd=cwd,
                timeout_sec=settings.agent_timeout_sec,
                stream_reader_limit=settings.agent_subprocess_stream_limit,
            )
        except AgentCliError as e:
            detail = openai_error_payload(str(e), type_="agent_execution_error")
            if e.stderr:
                detail["error"]["stderr"] = e.stderr
            if e.exit_code is not None:
                detail["error"]["exit_code"] = e.exit_code
            return JSONResponse(status_code=502, content=detail)

        try:
            content = decode_standard_output(raw, settings.agent_standard_output_format)
        except ValueError as e:
            return JSONResponse(
                status_code=502,
                content=openai_error_payload(str(e), type_="agent_output_decode_error"),
            )

        payload = build_chat_completion(model=model, content=content, prompt_text=prompt)
        return JSONResponse(content=payload)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("proxy_agent.app:app", host="0.0.0.0", port=8000, factory=False)
