from __future__ import annotations

import json

import pytest

from proxy_agent.services.cursor_cli_codec import (
    assistant_text_from_stream_json_line,
    decode_standard_output,
)


def test_assistant_text_from_stream_json_line_extracts_text() -> None:
    line = json.dumps(
        {"type": "assistant", "message": {"content": [{"text": "hi"}]}},
    )
    assert assistant_text_from_stream_json_line(line) == "hi"


def test_assistant_text_from_stream_json_line_joins_multiple_parts() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"text": "a"}, {"text": "b"}]},
        },
    )
    assert assistant_text_from_stream_json_line(line) == "ab"


def test_assistant_text_from_stream_json_line_ignores_non_assistant() -> None:
    line = json.dumps({"type": "tool_call", "subtype": "started"})
    assert assistant_text_from_stream_json_line(line) == ""


def test_assistant_text_from_stream_json_line_invalid_json() -> None:
    assert assistant_text_from_stream_json_line("not-json") == ""


def test_decode_standard_text() -> None:
    assert decode_standard_output("hello\n", "text") == "hello"


def test_decode_standard_json_result() -> None:
    assert decode_standard_output('{"result": "ok"}\n', "json") == "ok"


def test_decode_standard_json_missing_result() -> None:
    with pytest.raises(ValueError, match="result"):
        decode_standard_output('{"foo": 1}', "json")


def test_decode_standard_json_invalid() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        decode_standard_output("{", "json")
