"""Emit Cursor-style stream-json NDJSON lines (assistant deltas only)."""
from __future__ import annotations

import json
import sys


def main() -> None:
    _ = sys.argv[1:]
    for part in ("alpha", "beta"):
        line = {
            "type": "assistant",
            "message": {"content": [{"text": part}]},
        }
        print(json.dumps(line), flush=True)


if __name__ == "__main__":
    main()
