"""Turn OpenAI Chat Completions ``messages`` into one string for the agent CLI.

Also normalizes multimodal / OpenClaw-style ``content`` arrays (text + images) so gateways
that send array ``content`` still work with plain-text CLIs when at least one text part exists.
"""

from __future__ import annotations

import json
from typing import Literal

from proxy_agent.schemas.openai import ChatMessage, _text_from_multimodal_parts

MessagesFormat = Literal["transcript", "last_user_only"]

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


def messages_to_cli_prompt(
    messages: list[ChatMessage],
    *,
    mode: MessagesFormat,
    max_chars: int = 0,
) -> str:
    """Build the single string passed to ``{prompt}`` in agent argument templates."""

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
            f"Transcript exceeds AGENT_MAX_PROMPT_CHARS ({max_chars}); shorten the conversation or raise the limit"
        )
    return out
