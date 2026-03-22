"""OpenAI-shaped request/response models and message text extraction helpers."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

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
