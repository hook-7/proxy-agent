"""Prints two flushed lines to stdout (for streaming tests)."""
from __future__ import annotations

import sys


def main() -> None:
    _ = sys.argv[1:]  # prompt passed by proxy; ignored
    print("chunk-a", flush=True)
    print("chunk-b", flush=True)


if __name__ == "__main__":
    main()
