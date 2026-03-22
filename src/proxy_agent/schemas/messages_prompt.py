"""Turn OpenAI Chat Completions ``messages`` into one string for the agent CLI.

Layout follows the common *role-labelled blocks* pattern used by many OpenAI-compatible
stacks when targeting a plain-text backend (same idea as LiteLLM / local proxy fallbacks).

References (for behaviour alignment, not vendored code):

- OpenAI Chat Completions: https://platform.openai.com/docs/api-reference/chat/create
- LiteLLM (message handling patterns): https://github.com/BerriAI/litellm
"""

from __future__ import annotations

from typing import Literal

from proxy_agent.schemas.openai import ChatMessage, _text_from_multimodal_parts

MessagesFormat = Literal["transcript", "last_user_only"]

_ROLE_LABEL: dict[str, str] = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool",
    "developer": "Developer",
}


def _message_plain_text(msg: ChatMessage) -> str:
    if msg.content is None:
        return ""
    if isinstance(msg.content, str):
        return msg.content
    try:
        return _text_from_multimodal_parts(msg.content)
    except ValueError:
        return ""


def extract_last_user_text(messages: list[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        if msg.content is None:
            raise ValueError("Last user message has empty content")
        if isinstance(msg.content, list):
            return _text_from_multimodal_parts(msg.content)
        return msg.content
    raise ValueError("No user message found in messages")


def messages_to_cli_prompt(
    messages: list[ChatMessage],
    *,
    mode: MessagesFormat,
    max_chars: int = 0,
) -> str:
    """Build the single string passed to ``{prompt}`` in ``AGENT_ARGS_TEMPLATE``."""

    if mode == "last_user_only":
        return extract_last_user_text(messages)

    blocks: list[str] = []
    for msg in messages:
        label = _ROLE_LABEL.get(msg.role, msg.role)
        text = _message_plain_text(msg).strip()
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
