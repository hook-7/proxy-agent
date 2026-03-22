"""FastAPI ASGI app and backward-compatible re-exports for ``from proxy_agent.app import …``."""

from __future__ import annotations

import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from proxy_agent.api_models import (
    ChatCompletionRequest,
    ChatMessage,
    ModelInfo,
    ModelsListResponse,
)
from proxy_agent.cli_runner import (
    AgentCliError,
    build_argv,
    run_agent_cli,
    stream_agent_cli,
    wrap_agent_argv_for_stdbuf,
)
from proxy_agent.config import Settings, get_settings
from proxy_agent.cursor_stream import (
    _CursorNdjsonState,
    _iter_ndjson_stdout_deltas,
    assistant_text_from_stream_json_line,
    decode_standard_output,
    is_cursor_stream_result_line,
)
from proxy_agent.prompts import extract_last_user_text, messages_to_cli_prompt
from proxy_agent.sse import (
    build_chat_completion,
    openai_error_payload,
)
from proxy_agent.streaming import iter_chat_completion_sse, merge_async_iter_with_sse_comments

__all__ = [
    "AgentCliError",
    "ChatMessage",
    "Settings",
    "_CursorNdjsonState",
    "_iter_ndjson_stdout_deltas",
    "app",
    "assistant_text_from_stream_json_line",
    "build_argv",
    "build_chat_completion",
    "create_app",
    "decode_standard_output",
    "extract_last_user_text",
    "get_settings",
    "is_cursor_stream_result_line",
    "merge_async_iter_with_sse_comments",
    "messages_to_cli_prompt",
    "openai_error_payload",
    "run",
    "run_agent_cli",
    "stream_agent_cli",
    "wrap_agent_argv_for_stdbuf",
]


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
