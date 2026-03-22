from __future__ import annotations

import json

import pytest

from proxy_agent.app import (
    assistant_text_from_stream_json_line,
    decode_standard_output,
    is_cursor_stream_result_line,
)


def test_assistant_text_from_stream_json_line() -> None:
    line = json.dumps({"type": "assistant", "message": {"content": [{"text": "hi"}]}})
    assert assistant_text_from_stream_json_line(line) == "hi"


def test_is_cursor_stream_result_line() -> None:
    assert is_cursor_stream_result_line('{"type":"result","duration_ms":1}')
    assert not is_cursor_stream_result_line('{"type":"assistant","message":{}}')


def test_decode_standard_json_result() -> None:
    assert decode_standard_output('{"result": "ok"}\n', "json") == "ok"


def test_decode_standard_json_invalid() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        decode_standard_output("{", "json")
