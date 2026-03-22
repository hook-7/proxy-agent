#!/usr/bin/env python3
"""Emit assistant + result then sleep (stdout stays open)."""
from __future__ import annotations

import json
import sys
import time

lines = [
    {"type": "assistant", "message": {"content": [{"text": "ok-from-hang-fix"}]}},
    {"type": "result", "duration_ms": 1},
]
for obj in lines:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()

time.sleep(3600)
