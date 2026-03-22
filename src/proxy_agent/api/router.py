from __future__ import annotations

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
    argv = build_argv(settings.agent_command, settings.agent_args_template, prompt)
    argv = wrap_agent_argv_for_stdbuf(argv, settings.agent_use_stdbuf)
    cwd = settings.agent_cwd

    if body.stream is True:

        async def event_gen():
            completion_id = "chatcmpl-" + secrets.token_hex(12)
            created = int(time.time())
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
                        stdout_chunk_size=settings.agent_stream_stdout_chunk_size,
                        eof_process_wait_sec=settings.agent_stream_eof_process_wait_sec,
                    )

                async for piece in merge_async_iter_with_sse_comments(
                    _cli_stream,
                    settings.agent_sse_comment_interval_sec,
                ):
                    if isinstance(piece, bytes):
                        yield piece
                    elif piece:
                        yield format_sse(
                            stream_chunk_content(
                                completion_id=completion_id,
                                created=created,
                                model=model,
                                content=piece,
                            )
                        )
            except AgentCliError as e:
                err_text = str(e)
                if e.stderr:
                    err_text += "\n" + e.stderr[:4000]
                yield format_sse(
                    stream_chunk_content(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        content=err_text + "\n",
                    )
                )
            yield format_sse(
                stream_chunk_finish(
                    completion_id=completion_id,
                    created=created,
                    model=model,
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

    try:
        content = await run_agent_cli(
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

    payload = build_chat_completion(model=model, content=content, prompt_text=prompt)
    return JSONResponse(content=payload)
