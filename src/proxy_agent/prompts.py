"""Build the CLI prompt string from chat messages."""

from __future__ import annotations

from typing import Literal

from proxy_agent.api_models import (
    ChatMessage,
    _ROLE_LABEL,
    _message_plain_text,
    _text_from_multimodal_parts,
)

MessagesFormat = Literal["transcript", "last_user_only"]


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
