from __future__ import annotations

import secrets
import time
from typing import Any, Literal

from pydantic import BaseModel, field_validator

ChatRole = Literal["system", "user", "assistant", "tool", "developer"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
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


def extract_last_user_text(messages: list[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        if msg.content is None:
            raise ValueError("Last user message has empty content")
        if isinstance(msg.content, list):
            raise ValueError("Multimodal user content is not supported; use a string")
        return msg.content
    raise ValueError("No user message found in messages")


def build_chat_completion(
    *,
    model: str,
    content: str,
    prompt_text: str,
) -> dict[str, Any]:
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


def openai_error_payload(message: str, type_: str = "invalid_request_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": type_}}
