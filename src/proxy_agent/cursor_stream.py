"""Parse Cursor-style stream-json / NDJSON agent stdout."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal


def is_cursor_stream_result_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    try:
        obj: Any = json.loads(s)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and obj.get("type") == "result"


def assistant_text_from_stream_json_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    try:
        obj: Any = json.loads(s)
    except json.JSONDecodeError:
        return ""
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return ""
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return ""
    parts = msg.get("content")
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, dict):
            t = p.get("text")
            if isinstance(t, str):
                out.append(t)
    return "".join(out)


def decode_standard_output(text: str, format_: Literal["text", "json"]) -> str:
    raw = text.rstrip("\n")
    if format_ == "text":
        return raw
    try:
        obj: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"Agent standard output is not valid JSON: {e}"
        raise ValueError(msg) from e
    if isinstance(obj, dict) and isinstance(obj.get("result"), str):
        return obj["result"]
    raise ValueError("Agent JSON output must contain a string 'result' field")


@dataclass
class _CursorNdjsonState:
    run_finished: bool = False


def _iter_ndjson_stdout_deltas(piece: str, state: _CursorNdjsonState) -> list[str]:
    out: list[str] = []
    for raw_line in piece.splitlines(keepends=True):
        line_for_json = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        if is_cursor_stream_result_line(line_for_json):
            state.run_finished = True
            return out
        delta = assistant_text_from_stream_json_line(line_for_json)
        if delta:
            out.append(delta)
            continue
        stripped = line_for_json.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            line_out = raw_line if raw_line.endswith("\n") else raw_line + "\n"
            out.append(line_out)
    return out
