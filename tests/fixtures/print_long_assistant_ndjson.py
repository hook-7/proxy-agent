#!/usr/bin/env python3
"""One NDJSON line > 64KiB plus result."""
from __future__ import annotations

import json
import sys

_blob = "x" * 70_000
line = {"type": "assistant", "message": {"content": [{"text": _blob}]}}
sys.stdout.write(json.dumps(line, ensure_ascii=False) + "\n")
sys.stdout.write(
    json.dumps({"type": "result", "duration_ms": 1}, ensure_ascii=False) + "\n"
)
sys.stdout.flush()
