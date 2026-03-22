from __future__ import annotations

import json
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from proxy_agent.api.deps import get_app_settings, verify_bearer
from proxy_agent.core.config import Settings
from proxy_agent.schemas.messages_prompt import messages_to_cli_prompt
from proxy_agent.schemas.openai import (
    ChatCompletionRequest,
    ModelInfo,
    ModelsListResponse,
    build_chat_completion,
    format_sse,
    openai_error_payload,
    stream_chunk_content,
    stream_chunk_finish,
    stream_chunk_role_assistant,
)
from proxy_agent.services.cli_runner import (
    AgentCliError,
    build_argv,
    run_agent_cli,
    stream_agent_cli,
    wrap_agent_argv_for_stdbuf,
)
from proxy_agent.services.cursor_cli_codec import (
    assistant_text_from_stream_json_line,
    decode_standard_output,
)
from proxy_agent.services.sse_keepalive import merge_async_iter_with_sse_comments

router = APIRouter()


@router.get("/v1/models")
async def list_models(
    settings: Annotated[Settings, Depends(get_app_settings)],
    _: Annotated[None, Depends(verify_bearer)],
) -> ModelsListResponse:
    mid = settings.default_model
    return ModelsListResponse(
        data=[
            ModelInfo(
                id=mid,
                created=int(time.time()),
            )
        ]
    )


@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    body: ChatCompletionRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _: Annotated[None, Depends(verify_bearer)],
) -> JSONResponse | StreamingResponse:
    try:
        prompt = messages_to_cli_prompt(
            body.messages,
            mode=settings.agent_messages_format,
            max_chars=settings.agent_max_prompt_chars,
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content=openai_error_payload(str(e)),
        )

    model = body.model or settings.default_model
    cwd = settings.agent_cwd

    if body.stream is True:
        argv = build_argv(settings.agent_command, settings.agent_args_stream_template, prompt)
        argv = wrap_agent_argv_for_stdbuf(argv, settings.agent_use_stdbuf)
        stream_chunk_size = (
            0
            if settings.agent_stream_protocol == "cursor_ndjson"
            else settings.agent_stream_stdout_chunk_size
        )

        async def event_gen():
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
            try:

                def _cli_stream():
                    return stream_agent_cli(
                        argv,
                        cwd=cwd,
                        timeout_sec=settings.agent_timeout_sec,
                        stdout_chunk_size=stream_chunk_size,
                        eof_process_wait_sec=settings.agent_stream_eof_process_wait_sec,
                    )

                async for piece in merge_async_iter_with_sse_comments(
                    _cli_stream,
                    settings.agent_sse_comment_interval_sec,
                ):
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
                        for raw_line in piece.splitlines(keepends=True):
                            line_for_json = raw_line[:-1] if raw_line.endswith("\n") else raw_line
                            delta = assistant_text_from_stream_json_line(line_for_json)
                            if delta:
                                streamed_completion_chars += len(delta)
                                yield format_sse(
                                    stream_chunk_content(
                                        completion_id=completion_id,
                                        created=created,
                                        model=model,
                                        content=delta,
                                    )
                                )
                                continue
                            stripped = line_for_json.strip()
                            if not stripped:
                                continue
                            try:
                                json.loads(stripped)
                            except json.JSONDecodeError:
                                # Cursor stream-json is NDJSON, but many CLIs print plain lines;
                                # forward as text so OpenClaw / pi-ai always see token deltas.
                                line_out = raw_line if raw_line.endswith("\n") else raw_line + "\n"
                                streamed_completion_chars += len(line_out)
                                yield format_sse(
                                    stream_chunk_content(
                                        completion_id=completion_id,
                                        created=created,
                                        model=model,
                                        content=line_out,
                                    )
                                )
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

        return StreamingResponse(
            event_gen(),
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
        )
    except AgentCliError as e:
        detail = openai_error_payload(
            str(e),
            type_="agent_execution_error",
        )
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
