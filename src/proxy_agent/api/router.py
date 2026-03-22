from __future__ import annotations

import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from proxy_agent.api.deps import get_app_settings, verify_bearer
from proxy_agent.core.config import Settings
from proxy_agent.schemas.openai import (
    ChatCompletionRequest,
    ModelInfo,
    ModelsListResponse,
    build_chat_completion,
    extract_last_user_text,
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
        prompt = extract_last_user_text(body.messages)
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
                async for piece in stream_agent_cli(
                    argv,
                    cwd=cwd,
                    timeout_sec=settings.agent_timeout_sec,
                    stdout_chunk_size=settings.agent_stream_stdout_chunk_size,
                ):
                    if piece:
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
            media_type="text/event-stream",
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
