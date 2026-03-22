from __future__ import annotations

import pytest

from proxy_agent.app import ChatMessage, build_chat_completion, extract_last_user_text, openai_error_payload


def test_extract_last_user_text_finds_last_user() -> None:
    msgs = [
        ChatMessage(role="system", content="s"),
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="a"),
        ChatMessage(role="user", content="last"),
    ]
    assert extract_last_user_text(msgs) == "last"


def test_extract_last_user_text_multimodal() -> None:
    assert (
        extract_last_user_text(
            [ChatMessage(role="user", content=[{"type": "text", "text": "hello"}])]
        )
        == "hello"
    )


def test_extract_last_user_text_multimodal_no_text_raises() -> None:
    with pytest.raises(ValueError, match="no usable text"):
        extract_last_user_text(
            [
                ChatMessage(
                    role="user",
                    content=[{"type": "image_url", "image_url": {"url": "https://x"}}],
                )
            ]
        )


def test_build_chat_completion_shape() -> None:
    payload = build_chat_completion(model="m", content="out", prompt_text="in")
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "out"


def test_openai_error_payload() -> None:
    err = openai_error_payload("bad", type_="invalid_request_error")
    assert err["error"]["message"] == "bad"
