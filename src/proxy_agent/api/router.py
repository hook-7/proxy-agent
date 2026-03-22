from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from proxy_agent.api.deps import get_app_settings, verify_bearer
from proxy_agent.core.config import Settings
from proxy_agent.schemas.openai import (
    ChatCompletionRequest,
    ModelInfo,
    ModelsListResponse,
    build_chat_completion,
    extract_last_user_text,
    openai_error_payload,
)
from proxy_agent.services.cli_runner import AgentCliError, build_argv, run_agent_cli

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


@router.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _: Annotated[None, Depends(verify_bearer)],
) -> JSONResponse:
    if body.stream is True:
        return JSONResponse(
            status_code=400,
            content=openai_error_payload("stream=true is not supported"),
        )
    try:
        prompt = extract_last_user_text(body.messages)
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content=openai_error_payload(str(e)),
        )

    model = body.model or settings.default_model
    argv = build_argv(settings.agent_command, settings.agent_args_template, prompt)
    cwd = settings.agent_cwd

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
