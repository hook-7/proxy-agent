"""Print JSON with result field."""
from __future__ import annotations

import json

print(json.dumps({"result": "standard-json-body", "meta": 1}))
