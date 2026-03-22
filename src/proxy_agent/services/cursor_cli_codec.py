"""Decode Cursor CLI headless output (text / json / stream-json NDJSON).

See: https://cursor.com/cn/docs/cli/headless
"""

from __future__ import annotations

import json
from typing import Any, Literal


def assistant_text_from_stream_json_line(line: str) -> str:
    """Extract assistant text deltas from one NDJSON line (``--output-format stream-json``)."""

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


def decode_standard_output(
    text: str,
    format_: Literal["text", "json"],
) -> str:
    """Decode blocking CLI stdout: plain text or a single JSON object with string ``result``."""

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
