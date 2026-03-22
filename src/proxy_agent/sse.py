"""OpenAI-style error payloads, SSE framing, and non-streaming completion JSON."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any


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
