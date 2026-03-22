from __future__ import annotations

from proxy_agent.schemas.messages_prompt import messages_to_cli_prompt
from proxy_agent.schemas.openai import ChatMessage


def test_messages_to_cli_prompt_transcript_order() -> None:
    prompt = messages_to_cli_prompt(
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="u1"),
            ChatMessage(role="assistant", content="a1"),
            ChatMessage(role="user", content="u2"),
        ],
        mode="transcript",
    )
    assert "System:\nsys" in prompt
    assert "User:\nu1" in prompt
    assert "Assistant:\na1" in prompt
    assert "User:\nu2" in prompt
    assert prompt.index("u1") < prompt.index("a1") < prompt.index("u2")


def test_messages_to_cli_prompt_last_user_only() -> None:
    p = messages_to_cli_prompt(
        [
            ChatMessage(role="user", content="old"),
            ChatMessage(role="assistant", content="mid"),
            ChatMessage(role="user", content="new"),
        ],
        mode="last_user_only",
    )
    assert p == "new"


def test_messages_to_cli_prompt_includes_tool_result() -> None:
    prompt = messages_to_cli_prompt(
        [
            ChatMessage(role="user", content="q"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "fn"}}],
            ),
            ChatMessage(role="tool", content='{"ok": true}', tool_call_id="call_1"),
            ChatMessage(role="user", content="follow-up"),
        ],
        mode="transcript",
    )
    assert "tool_call_id=call_1" in prompt
    assert '"ok": true' in prompt
    assert "follow-up" in prompt


def test_messages_to_cli_prompt_max_chars() -> None:
    import pytest

    with pytest.raises(ValueError, match="exceeds"):
        messages_to_cli_prompt(
            [ChatMessage(role="user", content="x" * 100)],
            mode="transcript",
            max_chars=10,
        )
