"""Print a single JSON object like ``--output-format json`` (blocking)."""
from __future__ import annotations

import json
import sys


def main() -> None:
    _ = sys.argv[1:]
    print(json.dumps({"result": "standard-json-body"}))


if __name__ == "__main__":
    main()
