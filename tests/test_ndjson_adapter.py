"""Exercise private NDJSON delta helper (same logic as cursor_ndjson stream path)."""

from __future__ import annotations

import importlib
import json

# `import proxy_agent.app` resolves to the FastAPI instance re-exported on the package;
# load the implementation module explicitly.
_pa = importlib.import_module("proxy_agent.app")


def test_iter_ndjson_stops_at_result() -> None:
    state = _pa._CursorNdjsonState()
    blob = (
        json.dumps(
            {"type": "assistant", "message": {"content": [{"text": "hi"}]}},
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps({"type": "result", "duration_ms": 1}, ensure_ascii=False)
        + "\n"
    )
    out = _pa._iter_ndjson_stdout_deltas(blob, state)
    assert out == ["hi"]
    assert state.run_finished
